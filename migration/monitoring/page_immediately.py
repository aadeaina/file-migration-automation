"""Page-immediately checks (Phase 9), sourced directly from existing
tables -- no new instrumentation, per the brief.

Two of the four page-immediately conditions are event-driven and fire
at their own call site rather than being polled here:
- "repeated cloud API auth failures" -- `apply_acl` already calls
  `page_immediately` the moment a credential fetch fails (Phase 8).
- "auto_fallback_enabled triggering a mode change" -- `CutoverActivities
  .evaluate_auto_fallback_activity` calls `page_immediately` for each
  new hard_freeze config the instant it's inserted.

The other two are poll-based scans over existing tables, each guarded
by `AlertLog` so the same underlying row only pages once:
- a new `extra_grant` row in `EffectivePermissionDiff`
- a Temporal workflow stuck retrying past a threshold, read off
  `MigrationFileStatus.retry_count` (incremented on each apply_acl
  failure) rather than querying Temporal's own visibility API.
"""

from __future__ import annotations

from migration.models import (
    AlertLog,
    EffectivePermissionDiff,
    MigrationFileStatus,
    MismatchType,
    PermissionStatus,
)
from migration.security.paging import NotificationChannel, page_immediately

DEFAULT_STUCK_RETRY_THRESHOLD = 3


def _already_alerted(alert_type: str, subject_key: str) -> bool:
    return AlertLog.objects.filter(alert_type=alert_type, subject_key=subject_key).exists()


def _mark_alerted(alert_type: str, subject_key: str) -> None:
    AlertLog.objects.get_or_create(alert_type=alert_type, subject_key=subject_key)


def check_for_new_extra_grants(
    channel: NotificationChannel | None = None,
) -> list[EffectivePermissionDiff]:
    alerted = []
    for diff in EffectivePermissionDiff.objects.filter(mismatch_type=MismatchType.EXTRA_GRANT):
        subject_key = str(diff.id)
        if _already_alerted("extra_grant", subject_key):
            continue
        page_immediately(
            "extra_grant",
            f"{diff.identity} has dest access {diff.dest_access} beyond source "
            f"{diff.source_access} on file {diff.file.source_path} (diff_id={diff.id})",
            channel,
        )
        _mark_alerted("extra_grant", subject_key)
        alerted.append(diff)
    return alerted


def check_for_stuck_retries(
    channel: NotificationChannel | None = None, threshold: int = DEFAULT_STUCK_RETRY_THRESHOLD
) -> list[MigrationFileStatus]:
    alerted = []
    stuck = MigrationFileStatus.objects.filter(
        permission_status=PermissionStatus.FAILED, retry_count__gte=threshold
    )
    for row in stuck:
        subject_key = str(row.id)
        if _already_alerted("stuck_retry", subject_key):
            continue
        page_immediately(
            "stuck_retry",
            f"file {row.source_path} -> {row.dest_cloud} has failed {row.retry_count} "
            f"times (threshold {threshold}): {row.error_message}",
            channel,
        )
        _mark_alerted("stuck_retry", subject_key)
        alerted.append(row)
    return alerted


def run_page_immediately_checks(
    channel: NotificationChannel | None = None, stuck_retry_threshold: int = DEFAULT_STUCK_RETRY_THRESHOLD
) -> dict[str, int]:
    """Run every poll-based page-immediately check once. Meant to be
    invoked on a short interval (a cron-scheduled job or a periodic
    Temporal workflow), independent of the migration/permission/cutover
    workflows themselves."""
    return {
        "extra_grant": len(check_for_new_extra_grants(channel)),
        "stuck_retry": len(check_for_stuck_retries(channel, stuck_retry_threshold)),
    }
