"""Test-only ACL reader: lets tests inject ACLs by path instead of relying
on a real NTFS filesystem (which macOS/Linux dev machines don't have)."""

from __future__ import annotations

from pathlib import Path

from migration.discovery.types import FileACL


class FakeACLReader:
    def __init__(self, acls_by_relative_path: dict[str, FileACL], root: Path):
        self._acls = acls_by_relative_path
        self._root = root

    def read_acl(self, path: Path) -> FileACL:
        rel = str(path.relative_to(self._root))
        return self._acls[rel]
