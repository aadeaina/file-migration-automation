"""GCP adapter: Storage Transfer Service for the byte copy, then real
ACE translation (Phase 3's `translate_ace`) applied via `nfs4_setfacl`
against Filestore. This is the adapter that needs real engineering
effort per the brief -- NTFS and NFSv4.1 ACEs are structurally
different even for an already-resolvable identity.
"""

from __future__ import annotations

from migration.cloud_adapters.nfs4_acl import (
    NFS4AclApplier,
    Nfs4SetfaclApplier,
    ace4_to_setfacl_spec,
)
from migration.cloud_adapters.transport import StorageTransferTransport, Transport
from migration.cloud_adapters.types import (
    FileOutcome,
    FileRecord,
    PermissionResult,
    TransferResult,
    VerifyResult,
)
from migration.permission_mapping.loader import load_mapping_table
from migration.permission_mapping.review_queue import enqueue_review_flags
from migration.permission_mapping.translate import translate_ace
from migration.permission_mapping.types import MappingTable, NFSv4Ace


class GCPAdapter:
    def __init__(
        self,
        transport: Transport | None = None,
        acl_applier: NFS4AclApplier | None = None,
        mapping_table: MappingTable | None = None,
    ):
        self._transport = transport or StorageTransferTransport()
        self._acl_applier = acl_applier or Nfs4SetfaclApplier()
        self._mapping_table = mapping_table or load_mapping_table()

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

    def _translate_record(self, record: FileRecord) -> tuple[list[NFSv4Ace], set[str]]:
        object_type = "directory" if record.is_dir else "file"
        translated: list[NFSv4Ace] = []
        unmapped: set[str] = set()
        for ace in (record.source_acl.aces if record.source_acl else ()):
            result = translate_ace(ace, object_type, self._mapping_table)
            translated.append(result.ace)
            enqueue_review_flags(result.review_flags)
            unmapped |= result.unmapped_rights
        return translated, unmapped

    def apply_permissions(
        self, file_batch: list[FileRecord], mapping_table_version: str
    ) -> PermissionResult:
        outcomes = []
        for record in file_batch:
            translated, unmapped = self._translate_record(record)
            source_count = len(record.source_acl.aces) if record.source_acl else 0
            try:
                self._acl_applier.set_acl(record.dest_path, translated)
                success = not unmapped
                outcomes.append(
                    FileOutcome(
                        file_status_id=record.file_status_id,
                        success=success,
                        ace_count_source=source_count,
                        ace_count_dest=len(translated),
                        error_message=(
                            None
                            if success
                            else f"unmapped NTFS rights: {sorted(unmapped)}"
                        ),
                    )
                )
            except OSError as exc:
                outcomes.append(
                    FileOutcome(
                        file_status_id=record.file_status_id,
                        success=False,
                        ace_count_source=source_count,
                        error_message=str(exc),
                    )
                )
        return PermissionResult(outcomes=tuple(outcomes), mapping_table_version=mapping_table_version)

    def verify(self, file_batch: list[FileRecord]) -> VerifyResult:
        outcomes = []
        for record in file_batch:
            translated, _unmapped = self._translate_record(record)
            expected_specs = {ace4_to_setfacl_spec(a) for a in translated}
            source_count = len(record.source_acl.aces) if record.source_acl else 0
            try:
                dest_aces = self._acl_applier.read_acl(record.dest_path)
                actual_specs = {ace4_to_setfacl_spec(a) for a in dest_aces}
                success = expected_specs == actual_specs
                outcomes.append(
                    FileOutcome(
                        file_status_id=record.file_status_id,
                        success=success,
                        ace_count_source=source_count,
                        ace_count_dest=len(dest_aces),
                        error_message=None if success else "Destination ACE set diverges from translated source ACL",
                    )
                )
            except OSError as exc:
                outcomes.append(
                    FileOutcome(
                        file_status_id=record.file_status_id,
                        success=False,
                        ace_count_source=source_count,
                        error_message=str(exc),
                    )
                )
        return VerifyResult(outcomes=tuple(outcomes))
