"""Phase 2: walk the source tree and build a permission-aware manifest.

Inheritance-chain checking here is a deliberately simplified model of
NTFS propagation (it does not model the `NP` no-propagate flag beyond
"stops at this level" and does not model owner-of-ACE precedence
edge cases) -- it exists to catch the common real-world failure mode
(a subtree where an ancestor grants access but a descendant is
mysteriously missing it) for the `broken_inheritance` anomaly flag,
not to be a full NTFS inheritance engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from migration.discovery.acl_reader import ACLReader, is_orphaned_identity
from migration.discovery.types import ACE, Anomaly, ManifestEntry


@dataclass(frozen=True)
class _CarryForwardACE:
    identity: str
    rights: frozenset[str]
    allow: bool
    inherit_to_files: bool
    inherit_to_subfolders: bool

    def propagates_to(self, *, is_dir: bool) -> bool:
        return self.inherit_to_subfolders if is_dir else self.inherit_to_files

    def matches(self, ace: ACE) -> bool:
        return (
            ace.identity == self.identity
            and ace.rights == self.rights
            and ace.allow == self.allow
        )


def _explicit_inheritable(aces: tuple[ACE, ...]) -> list[_CarryForwardACE]:
    return [
        _CarryForwardACE(
            identity=ace.identity,
            rights=ace.rights,
            allow=ace.allow,
            inherit_to_files=ace.inherit_to_files,
            inherit_to_subfolders=ace.inherit_to_subfolders,
        )
        for ace in aces
        if not ace.inherited and (ace.inherit_to_files or ace.inherit_to_subfolders)
    ]


def _detect_anomalies(
    entry_acl_aces: tuple[ACE, ...],
    expected_from_parent: list[_CarryForwardACE],
) -> list[str]:
    anomalies: list[str] = []

    if any(is_orphaned_identity(ace.identity) for ace in entry_acl_aces):
        anomalies.append(Anomaly.ORPHANED_SID)

    if any(not ace.allow for ace in entry_acl_aces):
        anomalies.append(Anomaly.EXPLICIT_DENY)

    for expected in expected_from_parent:
        if is_orphaned_identity(expected.identity):
            continue
        if not any(expected.matches(ace) for ace in entry_acl_aces):
            anomalies.append(Anomaly.BROKEN_INHERITANCE)
            break

    return anomalies


def walk_tree(root: Path | str, acl_reader: ACLReader) -> list[ManifestEntry]:
    """Recursively walk `root`, reading each file/directory's ACL via
    `acl_reader` and producing one `ManifestEntry` per filesystem object
    (root itself excluded)."""
    root = Path(root)
    manifest: list[ManifestEntry] = []

    def _visit(dir_path: Path, depth: int, carry_forward: list[_CarryForwardACE]) -> None:
        try:
            children = sorted(dir_path.iterdir(), key=lambda p: p.name)
        except OSError:
            return

        for child in children:
            is_dir = child.is_dir()
            acl = acl_reader.read_acl(child)
            expected = [c for c in carry_forward if c.propagates_to(is_dir=is_dir)]
            anomalies = _detect_anomalies(acl.aces, expected)
            size = 0 if is_dir else child.stat().st_size

            manifest.append(
                ManifestEntry(
                    path=str(child.relative_to(root)),
                    is_dir=is_dir,
                    size=size,
                    depth=depth,
                    acl=acl,
                    anomalies=anomalies,
                )
            )

            if is_dir:
                own_inheritable = _explicit_inheritable(acl.aces)
                child_carry_forward = [
                    c for c in carry_forward if c.inherit_to_subfolders
                ] + own_inheritable
                _visit(child, depth + 1, child_carry_forward)

    _visit(root, 0, [])
    return manifest
