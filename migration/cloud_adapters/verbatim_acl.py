"""Shared logic for AWS and Azure: since the AD trust means ACLs carry
over near-verbatim, `apply_permissions`/`verify` on those two clouds is
a comparison, not a translation -- read the destination ACL back and
confirm it still has the same ACE count as the source.
"""

from __future__ import annotations

from pathlib import Path

from migration.cloud_adapters.types import FileOutcome, FileRecord
from migration.discovery.acl_reader import ACLReader


def verify_acl_carried_over(
    file_batch: list[FileRecord], dest_acl_reader: ACLReader
) -> tuple[FileOutcome, ...]:
    outcomes: list[FileOutcome] = []

    for record in file_batch:
        source_count = len(record.source_acl.aces) if record.source_acl else None
        try:
            dest_acl = dest_acl_reader.read_acl(Path(record.dest_path))
            dest_count = len(dest_acl.aces)
            success = source_count is None or dest_count == source_count
            outcomes.append(
                FileOutcome(
                    file_status_id=record.file_status_id,
                    success=success,
                    ace_count_source=source_count,
                    ace_count_dest=dest_count,
                    error_message=None
                    if success
                    else f"ACE count mismatch after transfer: source={source_count} dest={dest_count}",
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

    return tuple(outcomes)
