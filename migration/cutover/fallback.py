"""Fallback logic: manual by default, opt-in automatic.

Both paths read the same `SubtreesNeedingFallbackReview` view (Phase 1)
-- the only difference is who acts on it. `list_subtrees_needing_review`
is the manual path: it just surfaces candidates for a human to act on
via `insert_hard_freeze_config` (`updated_by=<username>`).
`evaluate_auto_fallback` is the opt-in automatic path: same view, but
acts itself (`updated_by="system:auto-fallback-workflow"`) only for
scopes that opted in via `CutoverConfig.auto_fallback_enabled`.
"""

from __future__ import annotations

from django.db import transaction

from migration.cutover.config_resolution import resolve_cutover_config
from migration.models import CutoverConfig, CutoverMode, SubtreesNeedingFallbackReview

AUTO_FALLBACK_UPDATED_BY = "system:auto-fallback-workflow"


def list_subtrees_needing_review() -> list[SubtreesNeedingFallbackReview]:
    return list(SubtreesNeedingFallbackReview.objects.all())


def insert_hard_freeze_config(
    scope: str, updated_by: str, fallback_reason: str, based_on: CutoverConfig | None = None
) -> CutoverConfig:
    """Insert a new hard_freeze config row for `scope`, deactivating any
    prior active row at that *exact* scope -- append-only history, one
    active row per scope. Guards against duplicates: if `scope` is
    already on hard_freeze, returns that existing row instead of
    inserting another."""
    with transaction.atomic():
        existing = CutoverConfig.objects.filter(scope=scope, active=True).first()
        if existing and existing.mode == CutoverMode.HARD_FREEZE:
            return existing

        if existing:
            existing.active = False
            existing.save(update_fields=["active"])

        return CutoverConfig.objects.create(
            scope=scope,
            mode=CutoverMode.HARD_FREEZE,
            max_acceptable_lag_seconds=(based_on.max_acceptable_lag_seconds if based_on else 300),
            lag_stability_window_minutes=(
                based_on.lag_stability_window_minutes if based_on else 30
            ),
            auto_fallback_enabled=(based_on.auto_fallback_enabled if based_on else False),
            auto_fallback_failure_threshold=(
                based_on.auto_fallback_failure_threshold if based_on else 5
            ),
            auto_fallback_window_hours=(based_on.auto_fallback_window_hours if based_on else 24),
            fallback_reason=fallback_reason,
            updated_by=updated_by,
        )


def evaluate_auto_fallback() -> list[CutoverConfig]:
    """For every subtree the view currently surfaces, fall back to
    hard_freeze automatically only if the config governing that subtree
    has opted in (`auto_fallback_enabled`); otherwise leave it for the
    manual path. Returns the newly inserted config rows (empty if
    nothing changed, e.g. everything was already handled)."""
    inserted = []
    for entry in list_subtrees_needing_review():
        governing_config = resolve_cutover_config(entry.subtree_path)
        if governing_config is None or not governing_config.auto_fallback_enabled:
            continue

        subtree_config = CutoverConfig.objects.filter(
            scope=entry.subtree_path, active=True
        ).first()
        if subtree_config and subtree_config.mode == CutoverMode.HARD_FREEZE:
            continue  # already on hard_freeze for this exact subtree -- no duplicate

        new_config = insert_hard_freeze_config(
            scope=entry.subtree_path,
            updated_by=AUTO_FALLBACK_UPDATED_BY,
            fallback_reason=(
                f"Automatic fallback: {entry.recent_failures} lag-stability failures "
                f"in the last 24h for {entry.subtree_path}"
            ),
            based_on=governing_config,
        )
        inserted.append(new_config)
    return inserted
