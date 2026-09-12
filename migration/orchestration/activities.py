"""The six per-file activities from the brief: ReadSourceACL,
ResolveIdentity, TranslateACE, ApplyACL, VerifyACL, WriteAuditRecord.

Implemented as sync bound methods on `MigrationActivities` so the
concrete adapters/readers/appliers are constructor-injected (real
implementations by default, fakes in tests/local runs) rather than
hardcoded -- the same dependency-injection shape Phase 4's adapters
already use. Sync (not `async def`) so the Worker's thread-pool
activity executor handles the blocking Django ORM/subprocess calls
without an event loop workaround.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from temporalio import activity

from migration.cloud_adapters.aws import AWSAdapter
from migration.cloud_adapters.azure import AzureAdapter
from migration.cloud_adapters.gcp import GCPAdapter
from migration.cloud_adapters.runner import apply_permission_result, apply_verify_result
from migration.cloud_adapters.types import FileRecord
from migration.discovery.acl_reader import ACLReader, IcaclsACLReader, is_orphaned_identity
from migration.discovery.types import FileACL
from migration.models import DestCloud, MigrationFileStatus, PermissionStatus
from migration.orchestration.dto import (
    AceDTO,
    NFSv4AceDTO,
    ace_to_dto,
    dto_to_ace,
    dto_to_nfsv4ace,
    nfsv4ace_to_dto,
)
from migration.permission_mapping.loader import load_mapping_table
from migration.permission_mapping.review_queue import enqueue_review_flags
from migration.permission_mapping.translate import translate_ace
from migration.permission_mapping.types import MappingTable
from migration.security.audit_log import log_acl_write
from migration.security.credentials import fetch_scoped_credential
from migration.security.paging import page_immediately
from migration.security.secrets import NullSecretsClient, SecretsClient
from migration.temporal_utils import release_db_connection


@dataclass
class ReadSourceACLInput:
    file_status_id: int


@dataclass
class ReadSourceACLResult:
    file_status_id: int
    source_path: str
    dest_path: str
    dest_cloud: str
    is_dir: bool
    aces: list[AceDTO]


@dataclass
class ResolveIdentityInput:
    aces: list[AceDTO]


@dataclass
class ResolveIdentityResult:
    resolvable_aces: list[AceDTO]
    orphaned_identities: list[str]


@dataclass
class TranslateACEInput:
    file_status_id: int
    dest_cloud: str
    is_dir: bool
    aces: list[AceDTO]
    mapping_table_version: str


@dataclass
class TranslateACEResult:
    # GCP: NFSv4 bits/flags, ready for nfs4_setfacl.
    # AWS/Azure: the same ACEs, unchanged -- no bit remapping needed
    # since trust carries them over natively; kept as AceDTO so ApplyACL
    # can still reconstruct a FileRecord.source_acl for the verify-only path.
    translated_ace4: list[NFSv4AceDTO]  # only populated for GCP
    passthrough_aces: list[AceDTO]  # only populated for AWS/Azure
    unmapped_rights: list[str]


@dataclass
class ApplyACLInput:
    file_status_id: int
    dest_cloud: str
    dest_path: str
    is_dir: bool
    translated: TranslateACEResult
    mapping_table_version: str


@dataclass
class ApplyACLResult:
    success: bool
    error_message: str | None
    ace_count_source: int | None
    ace_count_dest: int | None


@dataclass
class VerifyACLInput:
    file_status_id: int
    dest_cloud: str
    source_path: str
    dest_path: str
    is_dir: bool
    aces: list[AceDTO]


@dataclass
class VerifyACLResult:
    success: bool
    error_message: str | None


@dataclass
class WriteAuditRecordInput:
    file_status_id: int
    workflow_id: str


@dataclass
class ListPendingFileIdsInput:
    root_path: str
    dest_cloud: str
    mapping_table_version: str
    limit: int | None = None


class MigrationActivities:
    def __init__(
        self,
        aws_adapter: AWSAdapter | None = None,
        azure_adapter: AzureAdapter | None = None,
        gcp_adapter: GCPAdapter | None = None,
        source_acl_reader: ACLReader | None = None,
        mapping_table: MappingTable | None = None,
        secrets_client: SecretsClient | None = None,
        notification_channel=None,
    ):
        self.aws_adapter = aws_adapter or AWSAdapter()
        self.azure_adapter = azure_adapter or AzureAdapter()
        self.gcp_adapter = gcp_adapter or GCPAdapter()
        self.source_acl_reader = source_acl_reader or IcaclsACLReader()
        self.mapping_table = mapping_table or load_mapping_table()
        # Default to fetching nothing: our transports shell out to CLI
        # tools assumed to run under an already-authenticated host
        # identity in this environment. A deployment that needs explicit
        # per-call credentials passes a real `VaultSecretsClient` here.
        self.secrets_client = secrets_client or NullSecretsClient()
        self.notification_channel = notification_channel

    def _adapter_for(self, dest_cloud: str):
        return {
            DestCloud.AWS: self.aws_adapter,
            DestCloud.AZURE: self.azure_adapter,
            DestCloud.GCP: self.gcp_adapter,
        }[dest_cloud]

    @activity.defn
    @release_db_connection
    def read_source_acl(self, input: ReadSourceACLInput) -> ReadSourceACLResult:
        row = MigrationFileStatus.objects.get(id=input.file_status_id)
        acl = self.source_acl_reader.read_acl(Path(row.source_path))
        return ReadSourceACLResult(
            file_status_id=row.id,
            source_path=row.source_path,
            dest_path=row.dest_path,
            dest_cloud=row.dest_cloud,
            # MigrationFileStatus only ever holds file rows -- the Phase 2
            # loader skips directories -- so this is always a file.
            is_dir=False,
            aces=[ace_to_dto(a) for a in acl.aces],
        )

    @activity.defn
    @release_db_connection
    def resolve_identity(self, input: ResolveIdentityInput) -> ResolveIdentityResult:
        resolvable, orphaned = [], []
        for dto in input.aces:
            if is_orphaned_identity(dto.identity):
                orphaned.append(dto.identity)
            else:
                resolvable.append(dto)
        return ResolveIdentityResult(resolvable_aces=resolvable, orphaned_identities=orphaned)

    @activity.defn
    @release_db_connection
    def translate_ace_activity(self, input: TranslateACEInput) -> TranslateACEResult:
        object_type = "directory" if input.is_dir else "file"

        if input.dest_cloud == DestCloud.GCP:
            translated_ace4 = []
            unmapped: set[str] = set()
            for dto in input.aces:
                result = translate_ace(dto_to_ace(dto), object_type, self.mapping_table)
                translated_ace4.append(nfsv4ace_to_dto(result.ace))
                enqueue_review_flags(result.review_flags)
                unmapped |= result.unmapped_rights
            return TranslateACEResult(
                translated_ace4=translated_ace4,
                passthrough_aces=[],
                unmapped_rights=sorted(unmapped),
            )

        # AWS/Azure: trust carries ACLs over verbatim, no bit remapping.
        return TranslateACEResult(
            translated_ace4=[], passthrough_aces=input.aces, unmapped_rights=[]
        )

    @activity.defn
    @release_db_connection
    def apply_acl(self, input: ApplyACLInput) -> ApplyACLResult:
        if input.dest_cloud != DestCloud.GCP:
            # AWS/Azure: no bit remapping happened in TranslateACE, so
            # applying here means re-delegating to the adapter's own
            # apply_permissions (verify-only, since ACLs already carried
            # over verbatim during transfer) -- avoids duplicating that
            # comparison logic.
            adapter = self._adapter_for(input.dest_cloud)
            source_aces = tuple(dto_to_ace(d) for d in input.translated.passthrough_aces)
            record = FileRecord(
                file_status_id=input.file_status_id,
                source_path="",
                dest_path=input.dest_path,
                is_dir=input.is_dir,
                source_acl=FileACL(owner="", group=None, aces=source_aces),
            )
            perm_result = adapter.apply_permissions([record], input.mapping_table_version)
            apply_permission_result(perm_result)
            outcome = perm_result.outcomes[0]
            return ApplyACLResult(
                success=outcome.success,
                error_message=outcome.error_message,
                ace_count_source=outcome.ace_count_source,
                ace_count_dest=outcome.ace_count_dest,
            )

        # GCP: apply the already-translated ACE4 list. Passing the full
        # target set (not appending) to a single `set_acl` call is what
        # makes this idempotent by construction -- a retry just sets the
        # same target state again.
        #
        # Credential fetched fresh here, at execution time, from Vault --
        # never stored on `self`, never returned from this activity, and
        # never part of `ApplyACLInput`/`ApplyACLResult` (the only things
        # that end up in Temporal's workflow history). It goes out of
        # scope the moment this method returns.
        try:
            credential = fetch_scoped_credential(input.dest_cloud, self.secrets_client)
        except Exception as exc:
            page_immediately(
                "cloud_auth_failure",
                f"failed to fetch credential for {input.dest_cloud} (file {input.file_status_id}): {exc}",
                self.notification_channel,
                subject_key=input.dest_cloud,
            )
            raise

        aces = [dto_to_nfsv4ace(d) for d in input.translated.translated_ace4]
        success = False
        error_message = None
        try:
            self.gcp_adapter._acl_applier.set_acl(input.dest_path, aces)
            success = not input.translated.unmapped_rights
            if not success:
                error_message = f"unmapped NTFS rights: {input.translated.unmapped_rights}"
        except OSError as exc:
            success = False
            error_message = str(exc)
        finally:
            log_acl_write(
                credential.service_identity, input.dest_path, success, error_message
            )

        result = ApplyACLResult(
            success=success,
            error_message=error_message,
            ace_count_source=len(input.translated.translated_ace4),
            ace_count_dest=len(aces),
        )
        update_fields = {
            "permission_status": (
                PermissionStatus.APPLIED if result.success else PermissionStatus.FAILED
            ),
            "mapping_table_version": input.mapping_table_version,
            "ace_count_source": result.ace_count_source,
            "ace_count_dest": result.ace_count_dest,
            "error_message": result.error_message,
        }
        if not result.success:
            current_retry_count = MigrationFileStatus.objects.get(id=input.file_status_id).retry_count
            update_fields["retry_count"] = current_retry_count + 1
        MigrationFileStatus.objects.filter(id=input.file_status_id).update(**update_fields)
        return result

    @activity.defn
    @release_db_connection
    def verify_acl(self, input: VerifyACLInput) -> VerifyACLResult:
        adapter = self._adapter_for(input.dest_cloud)
        record = FileRecord(
            file_status_id=input.file_status_id,
            source_path=input.source_path,
            dest_path=input.dest_path,
            is_dir=input.is_dir,
            source_acl=FileACL(
                owner="", group=None, aces=tuple(dto_to_ace(d) for d in input.aces)
            ),
        )
        verify_result = adapter.verify([record])
        apply_verify_result(verify_result)
        outcome = verify_result.outcomes[0]
        return VerifyACLResult(success=outcome.success, error_message=outcome.error_message)

    @activity.defn
    @release_db_connection
    def write_audit_record(self, input: WriteAuditRecordInput) -> None:
        MigrationFileStatus.objects.filter(id=input.file_status_id).update(
            workflow_id=input.workflow_id
        )

    @activity.defn
    @release_db_connection
    def list_pending_file_ids(self, input: ListPendingFileIdsInput) -> list[int]:
        # "Not yet done under the CURRENT mapping table version" covers
        # both a first run (permission_status='pending') and a targeted
        # re-run after a mapping-table fix (permission_status='failed'
        # AND mapping_table_version=<old version>) with one query --
        # anything already 'applied'/'verified' under this exact version
        # is left untouched, so a re-run never redoes settled work or
        # re-copies bytes.
        qs = (
            MigrationFileStatus.objects.filter(
                dest_cloud=input.dest_cloud, source_path__startswith=input.root_path
            )
            .exclude(
                permission_status__in=["applied", "verified"],
                mapping_table_version=input.mapping_table_version,
            )
            .order_by("id")
            .values_list("id", flat=True)
        )
        if input.limit:
            qs = qs[: input.limit]
        return list(qs)
