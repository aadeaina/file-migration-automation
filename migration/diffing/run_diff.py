"""The diff batch job itself (Phase 6): decoupled from the transfer/
permission workflows on purpose -- runnable independently, at any time,
against any subtree, per the brief. Not a Temporal workflow: it's a
plain, idempotent batch function, since it has no multi-step durability
requirements of its own (each file's diff is independent and cheap to
recompute).
"""

from __future__ import annotations

from pathlib import Path

from migration.diffing.diff import diff_effective_access
from migration.diffing.effective_access import (
    effective_access_from_ace4,
    effective_access_from_ntfs,
)
from migration.discovery.acl_reader import ACLReader, IcaclsACLReader, is_orphaned_identity
from migration.models import (
    DestCloud,
    EffectivePermissionDiff,
    MigrationFileStatus,
    SignOffPolicy,
)


def _active_policy_version(mismatch_type: str) -> str | None:
    return (
        SignOffPolicy.objects.filter(mismatch_type=mismatch_type, active=True)
        .order_by("-updated_at")
        .values_list("policy_version", flat=True)
        .first()
    )


def diff_file(
    row: MigrationFileStatus,
    source_acl_reader: ACLReader,
    dest_acl_reader: ACLReader | None = None,
    gcp_acl_applier=None,
) -> list[EffectivePermissionDiff]:
    """Compute and persist the diff rows for one file. Re-running this
    for the same file replaces its previously-computed *unreviewed*
    diffs (recomputed state can change as source/dest data changes) but
    never touches rows that already carry a review decision -- an
    approved `DiffException` stays valid across re-runs."""
    source_acl = source_acl_reader.read_acl(Path(row.source_path))
    orphaned = frozenset(
        ace.identity for ace in source_acl.aces if is_orphaned_identity(ace.identity)
    )
    source_effective = effective_access_from_ntfs(source_acl.aces)

    if row.dest_cloud == DestCloud.GCP:
        dest_aces = gcp_acl_applier.read_acl(row.dest_path)
        dest_effective = effective_access_from_ace4(dest_aces)
    else:
        dest_acl = dest_acl_reader.read_acl(Path(row.dest_path))
        dest_effective = effective_access_from_ntfs(dest_acl.aces)

    diff_rows = diff_effective_access(source_effective, dest_effective, orphaned)

    EffectivePermissionDiff.objects.filter(file_id=row.id, reviewed=False).delete()

    # A reviewed row (e.g. one carrying an approved DiffException) whose
    # (identity, mismatch_type, access) is unchanged since it was reviewed
    # is still valid -- don't spawn a duplicate unreviewed row next to it
    # just because nothing actually changed.
    settled = {
        (d.identity, d.mismatch_type, tuple(d.source_access), tuple(d.dest_access))
        for d in EffectivePermissionDiff.objects.filter(file_id=row.id, reviewed=True)
    }

    created = []
    for diff_row in diff_rows:
        key = (
            diff_row.identity,
            diff_row.mismatch_type,
            tuple(diff_row.source_access),
            tuple(diff_row.dest_access),
        )
        if key in settled:
            continue
        created.append(
            EffectivePermissionDiff.objects.create(
                file=row,
                identity=diff_row.identity,
                source_access=diff_row.source_access,
                dest_access=diff_row.dest_access,
                mismatch_type=diff_row.mismatch_type,
                severity=diff_row.severity,
                policy_version_at_eval=_active_policy_version(diff_row.mismatch_type),
            )
        )
    return created


def diff_subtree(
    root_path: str,
    dest_cloud: str,
    source_acl_reader: ACLReader | None = None,
    dest_acl_reader: ACLReader | None = None,
    gcp_acl_applier=None,
) -> list[EffectivePermissionDiff]:
    source_acl_reader = source_acl_reader or IcaclsACLReader()
    dest_acl_reader = dest_acl_reader or IcaclsACLReader()

    rows = MigrationFileStatus.objects.filter(
        dest_cloud=dest_cloud, source_path__startswith=root_path
    )
    created: list[EffectivePermissionDiff] = []
    for row in rows:
        created.extend(diff_file(row, source_acl_reader, dest_acl_reader, gcp_acl_applier))
    return created
