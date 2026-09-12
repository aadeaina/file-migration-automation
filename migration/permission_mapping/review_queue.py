"""I/O wrapper around `translate_ace`'s review flags: routes anything
non-"exact" to the review queue table, deduped per pattern.
"""

from __future__ import annotations

from migration.models import MappingReviewQueueEntry
from migration.permission_mapping.types import ReviewFlag


def enqueue_review_flags(review_flags: tuple[ReviewFlag, ...]) -> None:
    for flag in review_flags:
        MappingReviewQueueEntry.objects.get_or_create(
            ntfs_right=flag.ntfs_right,
            object_type=flag.object_type,
            mapping_table_version=flag.mapping_table_version,
            defaults={"confidence": flag.confidence},
        )
