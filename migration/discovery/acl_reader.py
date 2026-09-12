"""ACL readers: pluggable sources of `FileACL` for a given path.

`IcaclsACLReader` shells out to Windows' `icacls`, which is how this
crawler is meant to run in production (on or adjacent to the on-prem
file server). It's kept behind the `ACLReader` protocol so the crawler
itself, and its tests, don't depend on actually running on Windows.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Protocol

from migration.discovery.types import ACE, FileACL

# icacls prints a SID literally (e.g. "S-1-5-21-3623811015-3361044348-30300820-1013")
# when it cannot resolve it to an account name against the trusted domain -- that's
# our signal for an orphaned/unresolvable identity.
SID_PATTERN = re.compile(r"^S-1-(\d+-)+\d+$")


def is_orphaned_identity(identity: str) -> bool:
    return bool(SID_PATTERN.match(identity.strip()))


# icacls rights letters -> normalized right names used everywhere downstream
# (Phase 3's mapping table keys off these same names).
_ICACLS_RIGHT_ALIASES = {
    "F": {"FullControl"},
    "M": {"Modify"},
    "RX": {"ReadAndExecute"},
    "R": {"ReadData"},
    "W": {"WriteData"},
    "D": {"Delete"},
    "DC": {"Delete"},
    "WD": {"WriteData"},
    "AD": {"AppendData"},
    "REA": {"ReadExtendedAttributes"},
    "WEA": {"WriteExtendedAttributes"},
    "X": {"ExecuteFile"},
    "DE": {"DeleteSubdirectoriesAndFiles"},
    "RA": {"ReadAttributes"},
    "WA": {"WriteAttributes"},
    "RC": {"ReadPermissions"},
    "WDAC": {"ChangePermissions"},
    "WO": {"TakeOwnership"},
    "S": {"Synchronize"},
}

_INHERITANCE_FLAG_TOKENS = {"OI", "CI", "IO", "NP", "I"}

_ACE_LINE_RE = re.compile(
    r"^\s*(?P<header>[^:]+?:)?\((?P<flags>(?:[A-Z]+)(?:,[A-Z]+)*)\)"
)


class ACLReader(Protocol):
    def read_acl(self, path: Path) -> FileACL: ...


def _parse_flags(raw_flags: list[str]) -> tuple[set[str], set[str], bool, bool]:
    """Split a parenthesized icacls flag group into (rights, inheritance
    tokens, is_deny, is_inherited)."""
    inheritance_tokens = {t for t in raw_flags if t in _INHERITANCE_FLAG_TOKENS}
    is_deny = "DENY" in raw_flags
    is_inherited = "I" in raw_flags
    rights: set[str] = set()
    for token in raw_flags:
        if token in _INHERITANCE_FLAG_TOKENS or token == "DENY":
            continue
        rights |= _ICACLS_RIGHT_ALIASES.get(token, {token})
    return rights, inheritance_tokens, is_deny, is_inherited


def parse_icacls_output(output: str) -> FileACL:
    """Parse the text `icacls <path>` prints on stdout into a FileACL.

    Expected shape (one identity per logical line, continuation lines
    indented under it for multiple ACEs on the same identity):

        C:\\share\\folder>icacls file.txt
        file.txt BUILTIN\\Administrators:(F)
                  NT AUTHORITY\\SYSTEM:(F)
                  DOMAIN\\jane.doe:(RX)
                  DOMAIN\\denied-user:(DENY)(W)
                  DOMAIN\\group:(OI)(CI)(IO)(GR,GW)
                  S-1-5-21-1111111111-2222222222-3333333333-9999:(R)

        Successfully processed 1 files; Failed processing 0 files.
    """
    aces: list[ACE] = []
    seen_first_ace_line = False

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("successfully processed"):
            continue
        if ":" not in line or "(" not in line:
            continue

        identity_part, _, remainder = line.partition(":")
        # Only the very first ACE line also carries the path before the
        # identity (e.g. "file.txt BUILTIN\Administrators:(F)"); every
        # continuation line is indented with just the identity.
        if not seen_first_ace_line and " " in identity_part:
            identity_part = identity_part.rsplit(" ", 1)[-1]
        seen_first_ace_line = True
        identity = identity_part.strip()

        # All parenthesized groups on this one identity line describe a
        # single ACE (e.g. "(I)(F)" = one inherited Full Control ACE) --
        # a second ACE for the same identity shows up as its own line with
        # the identity repeated, which the outer loop already handles.
        groups = re.findall(r"\(([^()]*)\)", remainder)
        tokens = [t.strip() for group in groups for t in group.split(",") if t.strip()]
        rights, inheritance_tokens, is_deny, is_inherited = _parse_flags(tokens)
        aces.append(
            ACE(
                identity=identity,
                rights=frozenset(rights),
                allow=not is_deny,
                inherited=is_inherited,
                inherit_to_files="OI" in inheritance_tokens,
                inherit_to_subfolders="CI" in inheritance_tokens,
                inherit_only="IO" in inheritance_tokens,
                no_propagate="NP" in inheritance_tokens,
            )
        )

    return FileACL(owner="", group=None, aces=tuple(aces))


def _parse_owner(output: str) -> str:
    match = re.search(r"^Owner:\s*(.+)$", output, re.MULTILINE)
    return match.group(1).strip() if match else ""


class IcaclsACLReader:
    """Reads ACLs via Windows `icacls`. Only usable on/against a Windows host."""

    def read_acl(self, path: Path) -> FileACL:
        result = subprocess.run(
            ["icacls", str(path)],
            capture_output=True,
            text=True,
            check=True,
        )
        owner_result = subprocess.run(
            ["icacls", str(path), "/Q"],
            capture_output=True,
            text=True,
            check=False,
        )
        acl = parse_icacls_output(result.stdout)
        owner = _parse_owner(owner_result.stdout) or _parse_owner(result.stdout)
        return FileACL(owner=owner, group=acl.group, aces=acl.aces)
