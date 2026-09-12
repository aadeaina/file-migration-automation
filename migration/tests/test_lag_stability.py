from django.test import TestCase
from django.utils import timezone

from migration.cutover.lag_stability import is_scope_lag_stable, required_consecutive_checks
from migration.models import CutoverConfig, ShadowSyncStatus


class RequiredConsecutiveChecksTest(TestCase):
    def test_rounds_up_to_whole_checks(self):
        self.assertEqual(required_consecutive_checks(30), 6)  # 30 / 5
        self.assertEqual(required_consecutive_checks(1), 1)  # min 1, rounds up from 0.2
        self.assertEqual(required_consecutive_checks(12), 3)  # 12/5 = 2.4 -> 3


class IsScopeLagStableTest(TestCase):
    def setUp(self):
        self.config = CutoverConfig.objects.create(
            scope="/share/finance",
            max_acceptable_lag_seconds=60,
            lag_stability_window_minutes=15,  # -> 3 required checks
            updated_by="admin",
        )

    def test_no_shadow_sync_data_is_not_stable(self):
        self.assertFalse(is_scope_lag_stable("/share/finance", self.config))

    def test_stable_when_lag_low_and_enough_consecutive_checks(self):
        ShadowSyncStatus.objects.create(
            subtree_path="/share/finance/reports",
            last_sync_completed_at=timezone.now(),
            last_sync_lag_seconds=10,
            pending_change_count=0,
            consecutive_stable_checks=5,
        )
        self.assertTrue(is_scope_lag_stable("/share/finance", self.config))

    def test_unstable_when_lag_exceeds_threshold(self):
        ShadowSyncStatus.objects.create(
            subtree_path="/share/finance/reports",
            last_sync_completed_at=timezone.now(),
            last_sync_lag_seconds=600,
            pending_change_count=100,
            consecutive_stable_checks=5,
        )
        self.assertFalse(is_scope_lag_stable("/share/finance", self.config))

    def test_unstable_when_not_enough_consecutive_checks_yet(self):
        ShadowSyncStatus.objects.create(
            subtree_path="/share/finance/reports",
            last_sync_completed_at=timezone.now(),
            last_sync_lag_seconds=10,
            pending_change_count=0,
            consecutive_stable_checks=1,
        )
        self.assertFalse(is_scope_lag_stable("/share/finance", self.config))

    def test_one_unstable_subtree_blocks_the_whole_scope(self):
        ShadowSyncStatus.objects.create(
            subtree_path="/share/finance/reports",
            last_sync_completed_at=timezone.now(),
            last_sync_lag_seconds=10,
            pending_change_count=0,
            consecutive_stable_checks=5,
        )
        ShadowSyncStatus.objects.create(
            subtree_path="/share/finance/audit",
            last_sync_completed_at=timezone.now(),
            last_sync_lag_seconds=999,
            pending_change_count=50,
            consecutive_stable_checks=5,
        )
        self.assertFalse(is_scope_lag_stable("/share/finance", self.config))
