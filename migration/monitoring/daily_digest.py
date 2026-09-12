"""The daily-digest tier: sourced directly from existing tables, same
as page-immediately -- no dedup needed here since a digest is a
point-in-time summary, not a per-event alert.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from migration.models import (
    EffectivePermissionDiff,
    MigrationFileStatus,
    MismatchType,
    PermissionStatus,
    SubtreesNeedingFallbackReview,
)
from migration.security.paging import NotificationChannel

DEFAULT_STUCK_FAILURE_AGE = timedelta(hours=4)


def build_daily_digest(stuck_failure_age: timedelta = DEFAULT_STUCK_FAILURE_AGE) -> str:
    missing_grant_count = EffectivePermissionDiff.objects.filter(
        mismatch_type=MismatchType.MISSING_GRANT, reviewed=False
    ).count()
    inheritance_divergence_count = EffectivePermissionDiff.objects.filter(
        mismatch_type=MismatchType.INHERITANCE_DIVERGENCE, reviewed=False
    ).count()

    stuck_since = timezone.now() - stuck_failure_age
    stuck_files = MigrationFileStatus.objects.filter(
        permission_status=PermissionStatus.FAILED, updated_at__lte=stuck_since
    ).count()

    unactioned_fallback_subtrees = list(
        SubtreesNeedingFallbackReview.objects.values_list("subtree_path", flat=True)
    )

    lines = [
        "*Daily migration digest*",
        f"- Open missing_grant diffs: {missing_grant_count}",
        f"- Open inheritance_divergence diffs: {inheritance_divergence_count}",
        f"- Files stuck in `failed` for over {stuck_failure_age}: {stuck_files}",
        f"- Subtrees needing fallback review, not yet acted on: {len(unactioned_fallback_subtrees)}",
    ]
    if unactioned_fallback_subtrees:
        lines.append("  " + ", ".join(unactioned_fallback_subtrees))

    return "\n".join(lines)


def send_daily_digest(channel: NotificationChannel) -> str:
    message = build_daily_digest()
    channel.send(message)
    return message
