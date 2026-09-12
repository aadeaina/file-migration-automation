from django.test import TestCase

from migration.models import (
    AlertLog,
    DestCloud,
    EffectivePermissionDiff,
    MigrationFileStatus,
    MismatchType,
    PermissionStatus,
)
from migration.monitoring.page_immediately import (
    DEFAULT_STUCK_RETRY_THRESHOLD,
    check_for_new_extra_grants,
    check_for_stuck_retries,
    run_page_immediately_checks,
)
from migration.monitoring.testing import FakeChannel, FakeFiringChannel


class CheckForNewExtraGrantsTest(TestCase):
    def setUp(self):
        self.row = MigrationFileStatus.objects.create(
            source_path="/onprem/share/report.csv",
            dest_cloud=DestCloud.AWS,
            dest_path="/fsx/share/report.csv",
        )

    def test_pages_and_sends_for_a_new_extra_grant(self):
        diff = EffectivePermissionDiff.objects.create(
            file=self.row,
            identity="DOMAIN\\jane",
            source_access=["read"],
            dest_access=["read", "write"],
            mismatch_type=MismatchType.EXTRA_GRANT,
            severity="critical",
        )
        channel = FakeChannel()

        alerted = check_for_new_extra_grants(channel)

        self.assertEqual([d.id for d in alerted], [diff.id])
        self.assertEqual(len(channel.messages), 1)
        self.assertIn("DOMAIN\\jane", channel.messages[0])
        self.assertTrue(
            AlertLog.objects.filter(alert_type="extra_grant", subject_key=str(diff.id)).exists()
        )

    def test_does_not_re_alert_on_second_run(self):
        EffectivePermissionDiff.objects.create(
            file=self.row,
            identity="DOMAIN\\jane",
            source_access=["read"],
            dest_access=["read", "write"],
            mismatch_type=MismatchType.EXTRA_GRANT,
            severity="critical",
        )
        channel = FakeChannel()

        check_for_new_extra_grants(channel)
        second_alerted = check_for_new_extra_grants(channel)

        self.assertEqual(second_alerted, [])
        self.assertEqual(len(channel.messages), 1)

    def test_ignores_other_mismatch_types(self):
        EffectivePermissionDiff.objects.create(
            file=self.row,
            identity="DOMAIN\\jane",
            source_access=["read", "write"],
            dest_access=["read"],
            mismatch_type=MismatchType.MISSING_GRANT,
            severity="warning",
        )
        channel = FakeChannel()

        alerted = check_for_new_extra_grants(channel)

        self.assertEqual(alerted, [])
        self.assertEqual(channel.messages, [])


class CheckForNewExtraGrantsWithFiringChannelTest(TestCase):
    """With an Alertmanager-style channel, dedup is Alertmanager's job:
    every currently-open extra_grant re-fires on every run, unlike the
    AlertLog-backed .send() path above."""

    def setUp(self):
        self.row = MigrationFileStatus.objects.create(
            source_path="/onprem/share/report.csv",
            dest_cloud=DestCloud.AWS,
            dest_path="/fsx/share/report.csv",
        )
        self.diff = EffectivePermissionDiff.objects.create(
            file=self.row,
            identity="DOMAIN\\jane",
            source_access=["read"],
            dest_access=["read", "write"],
            mismatch_type=MismatchType.EXTRA_GRANT,
            severity="critical",
        )

    def test_fires_a_labeled_alert(self):
        channel = FakeFiringChannel()

        check_for_new_extra_grants(channel)

        self.assertEqual(len(channel.fired), 1)
        alertname, subject_key, summary, severity = channel.fired[0]
        self.assertEqual(alertname, "extra_grant")
        self.assertEqual(subject_key, str(self.diff.id))
        self.assertIn("DOMAIN\\jane", summary)
        self.assertEqual(severity, "critical")

    def test_re_fires_on_every_run_no_alertlog_bookkeeping(self):
        channel = FakeFiringChannel()

        check_for_new_extra_grants(channel)
        second_alerted = check_for_new_extra_grants(channel)

        self.assertEqual(len(second_alerted), 1)  # still reported as open
        self.assertEqual(len(channel.fired), 2)  # fired again, not suppressed here
        self.assertFalse(AlertLog.objects.exists())  # no dedup bookkeeping used


class CheckForStuckRetriesTest(TestCase):
    def test_pages_a_file_at_or_over_threshold(self):
        row = MigrationFileStatus.objects.create(
            source_path="/onprem/share/a.csv",
            dest_cloud=DestCloud.GCP,
            dest_path="/filestore/share/a.csv",
            permission_status=PermissionStatus.FAILED,
            retry_count=DEFAULT_STUCK_RETRY_THRESHOLD,
        )
        channel = FakeChannel()

        alerted = check_for_stuck_retries(channel)

        self.assertEqual([r.id for r in alerted], [row.id])
        self.assertEqual(len(channel.messages), 1)

    def test_does_not_page_below_threshold(self):
        MigrationFileStatus.objects.create(
            source_path="/onprem/share/a.csv",
            dest_cloud=DestCloud.GCP,
            dest_path="/filestore/share/a.csv",
            permission_status=PermissionStatus.FAILED,
            retry_count=DEFAULT_STUCK_RETRY_THRESHOLD - 1,
        )
        channel = FakeChannel()

        alerted = check_for_stuck_retries(channel)

        self.assertEqual(alerted, [])

    def test_does_not_page_a_verified_file_regardless_of_retry_count(self):
        MigrationFileStatus.objects.create(
            source_path="/onprem/share/a.csv",
            dest_cloud=DestCloud.GCP,
            dest_path="/filestore/share/a.csv",
            permission_status=PermissionStatus.VERIFIED,
            retry_count=99,
        )
        channel = FakeChannel()

        alerted = check_for_stuck_retries(channel)

        self.assertEqual(alerted, [])


class RunPageImmediatelyChecksTest(TestCase):
    def test_returns_counts_for_each_check(self):
        channel = FakeChannel()
        counts = run_page_immediately_checks(channel)
        self.assertEqual(counts, {"extra_grant": 0, "stuck_retry": 0})
