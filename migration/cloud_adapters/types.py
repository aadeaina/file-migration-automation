"""Shared types for the per-cloud transfer adapters (Phase 4)."""

from __future__ import annotations

from dataclasses import dataclass

from migration.discovery.types import FileACL


@dataclass(frozen=True)
class FileRecord:
    """Everything an adapter needs to act on one file. `file_status_id`
    ties results back to a `MigrationFileStatus` row; `source_acl` is
    only needed by adapters that translate permissions (GCP) rather than
    just verify them carried over (AWS/Azure)."""

    file_status_id: int
    source_path: str
    dest_path: str
    is_dir: bool
    source_acl: FileACL | None = None


@dataclass(frozen=True)
class FileOutcome:
    file_status_id: int
    success: bool
    error_message: str | None = None
    ace_count_source: int | None = None
    ace_count_dest: int | None = None


@dataclass(frozen=True)
class TransferResult:
    outcomes: tuple[FileOutcome, ...]


@dataclass(frozen=True)
class PermissionResult:
    outcomes: tuple[FileOutcome, ...]
    mapping_table_version: str


@dataclass(frozen=True)
class VerifyResult:
    outcomes: tuple[FileOutcome, ...]
