"""Dashboard-only metrics: throughput, completion percentage per cloud,
mapping-table confidence distribution. No alerting -- these are read by
a dashboard, not pushed anywhere, so this module is just queries.
"""

from __future__ import annotations

from migration.models import MappingReviewQueueEntry, MigrationFileStatus, MigrationHealthOverview
from migration.permission_mapping.loader import load_mapping_table


def completion_percentage_by_cloud() -> dict[str, float]:
    result = {}
    for overview in MigrationHealthOverview.objects.all():
        total = MigrationFileStatus.objects.filter(dest_cloud=overview.dest_cloud).count()
        result[overview.dest_cloud] = (
            round(100 * overview.permissions_verified / total, 1) if total else 0.0
        )
    return result


def throughput_by_cloud() -> dict[str, int]:
    """Files transferred, per cloud -- a simple point-in-time count;
    a real dashboard would chart this over time using periodic
    snapshots, which this table doesn't keep (only current state)."""
    return {
        overview.dest_cloud: overview.transferred
        for overview in MigrationHealthOverview.objects.all()
    }


def mapping_table_confidence_distribution(mapping_table_version: str | None = None) -> dict[str, int]:
    """How many mapping rows fall into each confidence tier for the
    active (or given) mapping table version, plus how many distinct
    non-exact patterns have actually been *encountered* so far (queued
    for review) versus merely defined in the table."""
    mapping_table = load_mapping_table()
    version = mapping_table_version or mapping_table.version
    by_confidence: dict[str, int] = {}
    for row in mapping_table.mappings:
        by_confidence[row.confidence] = by_confidence.get(row.confidence, 0) + 1

    encountered_non_exact = MappingReviewQueueEntry.objects.filter(
        mapping_table_version=version
    ).count()
    return {**by_confidence, "encountered_non_exact_patterns": encountered_non_exact}
