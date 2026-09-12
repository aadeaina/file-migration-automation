from django.test import TestCase

from migration.models import DestCloud, MigrationFileStatus, PermissionStatus
from migration.monitoring.dashboard import (
    completion_percentage_by_cloud,
    mapping_table_confidence_distribution,
    throughput_by_cloud,
)


class CompletionPercentageByCloudTest(TestCase):
    def test_computes_percentage_from_migration_health_overview_view(self):
        MigrationFileStatus.objects.create(
            source_path="/a", dest_cloud=DestCloud.AWS, dest_path="/a",
            permission_status=PermissionStatus.VERIFIED,
        )
        MigrationFileStatus.objects.create(
            source_path="/b", dest_cloud=DestCloud.AWS, dest_path="/b",
            permission_status=PermissionStatus.PENDING,
        )

        result = completion_percentage_by_cloud()

        self.assertEqual(result[DestCloud.AWS], 50.0)

    def test_no_files_for_a_cloud_yields_no_entry(self):
        result = completion_percentage_by_cloud()
        self.assertEqual(result, {})


class ThroughputByCloudTest(TestCase):
    def test_counts_transferred_files_per_cloud(self):
        from migration.models import TransferStatus

        MigrationFileStatus.objects.create(
            source_path="/a", dest_cloud=DestCloud.GCP, dest_path="/a",
            transfer_status=TransferStatus.TRANSFERRED,
        )
        MigrationFileStatus.objects.create(
            source_path="/b", dest_cloud=DestCloud.GCP, dest_path="/b",
            transfer_status=TransferStatus.QUEUED,
        )

        result = throughput_by_cloud()

        self.assertEqual(result[DestCloud.GCP], 1)


class MappingTableConfidenceDistributionTest(TestCase):
    def test_reports_confidence_tier_counts_from_the_mapping_table(self):
        result = mapping_table_confidence_distribution()

        self.assertIn("exact", result)
        self.assertIn("partial", result)
        self.assertIn("policy_decision", result)
        self.assertEqual(result["encountered_non_exact_patterns"], 0)
        self.assertEqual(
            sum(v for k, v in result.items() if k != "encountered_non_exact_patterns"),
            result["exact"] + result["partial"] + result["policy_decision"],
        )
