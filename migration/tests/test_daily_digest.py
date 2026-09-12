from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from migration.models import (
    DestCloud,
    EffectivePermissionDiff,
    LagStabilityCheck,
    MigrationFileStatus,
    MismatchType,
    PermissionStatus,
)
from migration.monitoring.daily_digest import build_daily_digest, send_daily_digest
from migration.monitoring.testing import FakeChannel


class BuildDailyDigestTest(TestCase):
    def test_counts_open_missing_grant_and_inheritance_divergence(self):
        row = MigrationFileStatus.objects.create(
            source_path="/a", dest_cloud=DestCloud.AWS, dest_path="/a"
        )
        EffectivePermissionDiff.objects.create(
            file=row,
            identity="D\\x",
            source_access=["read"],
            dest_access=[],
            mismatch_type=MismatchType.MISSING_GRANT,
            severity="warning",
        )
        EffectivePermissionDiff.objects.create(
            file=row,
            identity="D\\y",
            source_access=["read"],
            dest_access=["read"],
            mismatch_type=MismatchType.INHERITANCE_DIVERGENCE,
            severity="info",
        )
        # Reviewed diffs don't count as "open".
        EffectivePermissionDiff.objects.create(
            file=row,
            identity="D\\z",
            source_access=["read"],
            dest_access=[],
            mismatch_type=MismatchType.MISSING_GRANT,
            severity="warning",
            reviewed=True,
        )

        digest = build_daily_digest()

        self.assertIn("Open missing_grant diffs: 1", digest)
        self.assertIn("Open inheritance_divergence diffs: 1", digest)

    def test_counts_files_stuck_in_failed_past_the_age_threshold(self):
        stuck = MigrationFileStatus.objects.create(
            source_path="/a", dest_cloud=DestCloud.AWS, dest_path="/a",
            permission_status=PermissionStatus.FAILED,
        )
        MigrationFileStatus.objects.filter(id=stuck.id).update(
            updated_at=timezone.now() - timedelta(hours=10)
        )
        MigrationFileStatus.objects.create(
            source_path="/b", dest_cloud=DestCloud.AWS, dest_path="/b",
            permission_status=PermissionStatus.FAILED,
        )  # recently failed, not "stuck" yet

        digest = build_daily_digest(stuck_failure_age=timedelta(hours=4))

        stuck_line = next(line for line in digest.splitlines() if "stuck in `failed`" in line)
        self.assertTrue(stuck_line.endswith(": 1"))

    def test_lists_subtrees_needing_fallback_review(self):
        for _ in range(5):
            LagStabilityCheck.objects.create(
                subtree_path="/share/unstable", lag_seconds=999, threshold_seconds=60, passed=False
            )

        digest = build_daily_digest()

        self.assertIn("needing fallback review, not yet acted on: 1", digest)
        self.assertIn("/share/unstable", digest)

    def test_send_daily_digest_delivers_to_the_channel(self):
        channel = FakeChannel()
        message = send_daily_digest(channel)
        self.assertEqual(channel.messages, [message])
        self.assertIn("Daily migration digest", message)
