"""AWS adapter: DataSync/Robocopy into FSx for Windows File Server.

Trust into AWS Managed AD means SIDs resolve and NTFS ACLs carry over
verbatim, so `apply_permissions` here is a verification step, not a
translation step (see `verbatim_acl.py`).
"""

from __future__ import annotations

from migration.cloud_adapters.transport import RobocopyTransport, Transport
from migration.cloud_adapters.types import (
    FileOutcome,
    FileRecord,
    PermissionResult,
    TransferResult,
    VerifyResult,
)
from migration.cloud_adapters.verbatim_acl import verify_acl_carried_over
from migration.discovery.acl_reader import ACLReader, IcaclsACLReader


class AWSAdapter:
    def __init__(
        self,
        transport: Transport | None = None,
        dest_acl_reader: ACLReader | None = None,
    ):
        self._transport = transport or RobocopyTransport()
        self._dest_acl_reader = dest_acl_reader or IcaclsACLReader()

    def transfer(self, file_batch: list[FileRecord]) -> TransferResult:
        outcomes = []
        for record in file_batch:
            try:
                self._transport.copy(record.source_path, record.dest_path)
                outcomes.append(FileOutcome(file_status_id=record.file_status_id, success=True))
            except OSError as exc:
                outcomes.append(
                    FileOutcome(
                        file_status_id=record.file_status_id,
                        success=False,
                        error_message=str(exc),
                    )
                )
        return TransferResult(outcomes=tuple(outcomes))

    def apply_permissions(
        self, file_batch: list[FileRecord], mapping_table_version: str
    ) -> PermissionResult:
        outcomes = verify_acl_carried_over(file_batch, self._dest_acl_reader)
        return PermissionResult(outcomes=outcomes, mapping_table_version=mapping_table_version)

    def verify(self, file_batch: list[FileRecord]) -> VerifyResult:
        outcomes = verify_acl_carried_over(file_batch, self._dest_acl_reader)
        return VerifyResult(outcomes=outcomes)
