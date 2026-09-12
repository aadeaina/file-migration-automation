"""Applies adapter results to `MigrationFileStatus` rows.

Kept separate from the adapters themselves so `transfer`/`apply_permissions`/
`verify` stay focused on talking to the cloud, and so this same
result-to-row mapping can be reused unchanged once Phase 5 wraps these
calls in Temporal activities.
"""

from __future__ import annotations

from django.utils import timezone

from migration.cloud_adapters.types import (
    PermissionResult,
    TransferResult,
    VerifyResult,
)
from migration.models import MigrationFileStatus, PermissionStatus, TransferStatus


def apply_transfer_result(result: TransferResult) -> None:
    for outcome in result.outcomes:
        if outcome.success:
            MigrationFileStatus.objects.filter(id=outcome.file_status_id).update(
                transfer_status=TransferStatus.TRANSFERRED,
                transfer_checksum_match=True,
                transferred_at=timezone.now(),
                error_message=None,
            )
        else:
            MigrationFileStatus.objects.filter(id=outcome.file_status_id).update(
                transfer_status=TransferStatus.TRANSFER_FAILED,
                transfer_checksum_match=False,
                error_message=outcome.error_message,
                retry_count=_incremented_retry_count(outcome.file_status_id),
            )


def apply_permission_result(result: PermissionResult) -> None:
    for outcome in result.outcomes:
        if outcome.success:
            MigrationFileStatus.objects.filter(id=outcome.file_status_id).update(
                permission_status=PermissionStatus.APPLIED,
                permission_applied_at=timezone.now(),
                mapping_table_version=result.mapping_table_version,
                ace_count_source=outcome.ace_count_source,
                ace_count_dest=outcome.ace_count_dest,
                error_message=None,
            )
        else:
            MigrationFileStatus.objects.filter(id=outcome.file_status_id).update(
                permission_status=PermissionStatus.FAILED,
                mapping_table_version=result.mapping_table_version,
                error_message=outcome.error_message,
                retry_count=_incremented_retry_count(outcome.file_status_id),
            )


def apply_verify_result(result: VerifyResult) -> None:
    for outcome in result.outcomes:
        if outcome.success:
            MigrationFileStatus.objects.filter(id=outcome.file_status_id).update(
                permission_status=PermissionStatus.VERIFIED,
                permission_verified_at=timezone.now(),
                ace_count_source=outcome.ace_count_source,
                ace_count_dest=outcome.ace_count_dest,
                error_message=None,
            )
        else:
            MigrationFileStatus.objects.filter(id=outcome.file_status_id).update(
                permission_status=PermissionStatus.FAILED,
                ace_count_source=outcome.ace_count_source,
                ace_count_dest=outcome.ace_count_dest,
                error_message=outcome.error_message or "Verification mismatch",
            )


def _incremented_retry_count(file_status_id: int) -> int:
    current = MigrationFileStatus.objects.get(id=file_status_id).retry_count
    return current + 1
