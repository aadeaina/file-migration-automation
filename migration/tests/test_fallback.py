from django.test import TestCase

from migration.cutover.fallback import (
    AUTO_FALLBACK_UPDATED_BY,
    evaluate_auto_fallback,
    insert_hard_freeze_config,
    list_subtrees_needing_review,
)
from migration.models import CutoverConfig, CutoverMode, LagStabilityCheck


def _seed_failing_subtree(subtree_path: str, failures: int = 5):
    for _ in range(failures):
        LagStabilityCheck.objects.create(
            subtree_path=subtree_path, lag_seconds=999, threshold_seconds=60, passed=False
        )


class ManualFallbackPathTest(TestCase):
    def test_unstable_subtree_is_surfaced_without_any_auto_action(self):
        _seed_failing_subtree("/share/finance/reports")
        CutoverConfig.objects.create(
            scope="/share/finance", auto_fallback_enabled=False, updated_by="admin"
        )

        surfaced = list_subtrees_needing_review()
        self.assertEqual([e.subtree_path for e in surfaced], ["/share/finance/reports"])

        # Manual path: nothing gets inserted automatically.
        self.assertFalse(
            CutoverConfig.objects.filter(scope="/share/finance/reports").exists()
        )

    def test_human_can_insert_hard_freeze_after_seeing_the_review_queue(self):
        _seed_failing_subtree("/share/finance/reports")

        config = insert_hard_freeze_config(
            scope="/share/finance/reports",
            updated_by="jane.doe",
            fallback_reason="Manually reviewed: persistent lag instability",
        )

        self.assertEqual(config.mode, CutoverMode.HARD_FREEZE)
        self.assertEqual(config.updated_by, "jane.doe")


class AutoFallbackPathTest(TestCase):
    def test_does_nothing_when_auto_fallback_disabled(self):
        _seed_failing_subtree("/share/finance/reports")
        CutoverConfig.objects.create(
            scope="/share/finance", auto_fallback_enabled=False, updated_by="admin"
        )

        inserted = evaluate_auto_fallback()

        self.assertEqual(inserted, [])

    def test_inserts_hard_freeze_when_auto_fallback_enabled(self):
        _seed_failing_subtree("/share/finance/reports")
        CutoverConfig.objects.create(
            scope="/share/finance",
            mode=CutoverMode.SHADOW_WRITE,
            auto_fallback_enabled=True,
            updated_by="admin",
        )

        inserted = evaluate_auto_fallback()

        self.assertEqual(len(inserted), 1)
        new_config = inserted[0]
        self.assertEqual(new_config.scope, "/share/finance/reports")
        self.assertEqual(new_config.mode, CutoverMode.HARD_FREEZE)
        self.assertEqual(new_config.updated_by, AUTO_FALLBACK_UPDATED_BY)

    def test_second_evaluation_does_not_insert_a_duplicate(self):
        _seed_failing_subtree("/share/finance/reports")
        CutoverConfig.objects.create(
            scope="/share/finance",
            mode=CutoverMode.SHADOW_WRITE,
            auto_fallback_enabled=True,
            updated_by="admin",
        )

        first = evaluate_auto_fallback()
        second = evaluate_auto_fallback()

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0)
        self.assertEqual(
            CutoverConfig.objects.filter(
                scope="/share/finance/reports", mode=CutoverMode.HARD_FREEZE
            ).count(),
            1,
        )

    def test_previous_active_config_for_the_scope_is_deactivated_not_deleted(self):
        _seed_failing_subtree("/share/finance/reports")
        original = CutoverConfig.objects.create(
            scope="/share/finance/reports",
            mode=CutoverMode.SHADOW_WRITE,
            auto_fallback_enabled=True,
            updated_by="admin",
        )

        evaluate_auto_fallback()

        original.refresh_from_db()
        self.assertFalse(original.active)
        active = CutoverConfig.objects.get(scope="/share/finance/reports", active=True)
        self.assertEqual(active.mode, CutoverMode.HARD_FREEZE)
