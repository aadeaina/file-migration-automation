"""The common adapter contract every cloud implements (Phase 4)."""

from __future__ import annotations

from typing import Protocol

from migration.cloud_adapters.types import (
    FileRecord,
    PermissionResult,
    TransferResult,
    VerifyResult,
)


class CloudAdapter(Protocol):
    def transfer(self, file_batch: list[FileRecord]) -> TransferResult: ...

    def apply_permissions(
        self, file_batch: list[FileRecord], mapping_table_version: str
    ) -> PermissionResult: ...

    def verify(self, file_batch: list[FileRecord]) -> VerifyResult: ...
