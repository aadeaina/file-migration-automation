from django.test import TestCase

from migration.models import (
    DestCloud,
    MigrationFileStatus,
    PermissionStatus,
    TransferStatus,
)


class MigrationFileStatusDefaultsTest(TestCase):
    def test_defaults_on_create(self):
        row = MigrationFileStatus.objects.create(
            source_path=r"\\fileserver\share\folder\file.txt",
            dest_cloud=DestCloud.AWS,
            dest_path="share/folder/file.txt",
        )

        row.refresh_from_db()

        self.assertEqual(row.transfer_status, TransferStatus.QUEUED)
        self.assertEqual(row.permission_status, PermissionStatus.PENDING)
        self.assertEqual(row.retry_count, 0)
        self.assertIsNone(row.mapping_table_version)
        self.assertIsNotNone(row.updated_at)
