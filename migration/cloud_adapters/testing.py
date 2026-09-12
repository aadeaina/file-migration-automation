"""Fakes for cloud_adapters tests: no real robocopy/azcopy/nfs4_setfacl
binaries or cloud accounts needed."""

from __future__ import annotations

from pathlib import Path

from migration.discovery.types import FileACL
from migration.permission_mapping.types import NFSv4Ace


class FakeTransport:
    def __init__(self, fail_dest_paths: frozenset[str] = frozenset()):
        self.fail_dest_paths = fail_dest_paths
        self.calls: list[tuple[str, str]] = []

    def copy(self, source_path: str, dest_path: str) -> None:
        self.calls.append((source_path, dest_path))
        if dest_path in self.fail_dest_paths:
            raise OSError(f"simulated copy failure for {dest_path}")


class FakeDestACLReader:
    def __init__(self, acls_by_dest_path: dict[str, FileACL]):
        self._acls = acls_by_dest_path

    def read_acl(self, path: Path) -> FileACL:
        key = str(path)
        if key not in self._acls:
            raise OSError(f"no such path: {key}")
        return self._acls[key]


class FakeNfs4AclApplier:
    def __init__(self):
        self.state: dict[str, list[NFSv4Ace]] = {}

    def set_acl(self, dest_path: str, aces: list[NFSv4Ace]) -> None:
        self.state[dest_path] = list(aces)

    def read_acl(self, dest_path: str) -> list[NFSv4Ace]:
        if dest_path not in self.state:
            raise OSError(f"no such path: {dest_path}")
        return self.state[dest_path]
