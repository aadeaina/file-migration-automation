"""Applies translated NFSv4 ACEs to Filestore, via `nfs4_setfacl`.

Kept behind an `NFS4AclApplier` protocol so the GCP adapter's own logic
(translation, review-flag routing, count bookkeeping) is testable
without the real binary or a live Filestore instance.
"""

from __future__ import annotations

import subprocess
from typing import Protocol

from migration.permission_mapping.types import NFSv4Ace

# nfs4_setfacl's single-letter permission spec, per POSIX Draft ACL /
# Solaris NFSv4 ACL conventions.
_ACE4_BIT_TO_LETTER = {
    "ACE4_READ_DATA": "r",
    "ACE4_LIST_DIRECTORY": "r",
    "ACE4_WRITE_DATA": "w",
    "ACE4_ADD_FILE": "w",
    "ACE4_APPEND_DATA": "a",
    "ACE4_ADD_SUBDIRECTORY": "a",
    "ACE4_READ_NAMED_ATTRS": "n",
    "ACE4_WRITE_NAMED_ATTRS": "N",
    "ACE4_EXECUTE": "x",
    "ACE4_DELETE_CHILD": "D",
    "ACE4_DELETE": "d",
    "ACE4_READ_ATTRIBUTES": "t",
    "ACE4_WRITE_ATTRIBUTES": "T",
    "ACE4_READ_ACL": "c",
    "ACE4_WRITE_ACL": "C",
    "ACE4_WRITE_OWNER": "o",
    "ACE4_SYNCHRONIZE": "y",
}

_ACE4_FLAG_TO_LETTER = {
    "ACE4_FILE_INHERIT_ACE": "f",
    "ACE4_DIRECTORY_INHERIT_ACE": "d",
    "ACE4_INHERIT_ONLY_ACE": "i",
    "ACE4_NO_PROPAGATE_INHERIT_ACE": "n",
}


def ace4_to_setfacl_spec(ace: NFSv4Ace) -> str:
    """e.g. `A:fd:DOMAIN\\jane.doe:rxtc` (allow, file+dir inherit)."""
    ace_type = "A" if ace.allow else "D"
    flags = "".join(sorted(_ACE4_FLAG_TO_LETTER[f] for f in ace.ace4_flags if f in _ACE4_FLAG_TO_LETTER))
    perms = "".join(sorted(_ACE4_BIT_TO_LETTER[b] for b in ace.ace4_bits if b in _ACE4_BIT_TO_LETTER))
    return f"{ace_type}:{flags}:{ace.identity}:{perms}"


class NFS4AclApplier(Protocol):
    def set_acl(self, dest_path: str, aces: list[NFSv4Ace]) -> None: ...

    def read_acl(self, dest_path: str) -> list[NFSv4Ace]: ...


class Nfs4SetfaclApplier:
    def set_acl(self, dest_path: str, aces: list[NFSv4Ace]) -> None:
        spec = ",".join(ace4_to_setfacl_spec(ace) for ace in aces)
        result = subprocess.run(
            ["nfs4_setfacl", "-s", spec, dest_path],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise OSError(f"nfs4_setfacl failed ({result.returncode}): {result.stderr}")

    def read_acl(self, dest_path: str) -> list[NFSv4Ace]:
        result = subprocess.run(
            ["nfs4_getfacl", dest_path],
            capture_output=True,
            text=True,
            check=True,
        )
        aces = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            ace_type, flags, identity, perms = line.split(":", 3)
            aces.append(
                NFSv4Ace(
                    identity=identity,
                    allow=ace_type == "A",
                    ace4_bits=frozenset(
                        bit
                        for bit, letter in _ACE4_BIT_TO_LETTER.items()
                        if letter in perms
                    ),
                    ace4_flags=frozenset(
                        flag
                        for flag, letter in _ACE4_FLAG_TO_LETTER.items()
                        if letter in flags
                    ),
                )
            )
        return aces
