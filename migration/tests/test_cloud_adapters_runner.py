from django.test import TestCase

from migration.cloud_adapters.aws import AWSAdapter
from migration.cloud_adapters.gcp import GCPAdapter
from migration.cloud_adapters.runner import (
    apply_permission_result,
    apply_transfer_result,
    apply_verify_result,
)
from migration.cloud_adapters.testing import (
    FakeDestACLReader,
    FakeNfs4AclApplier,
    FakeTransport,
)
from migration.cloud_adapters.types import FileRecord
from migration.discovery.types import ACE, FileACL
from migration.models import (
    DestCloud,
    MigrationFileStatus,
    PermissionStatus,
    TransferStatus,
)
from migration.permission_mapping.loader import load_mapping_table

SOURCE_ACL = FileACL(
    owner="DOMAIN\\admin",
    group=None,
    aces=(ACE(identity="DOMAIN\\jane.doe", rights=frozenset({"ReadData"}), allow=True, inherited=False),),
)


class AWSAdapterUpdatesDbEndToEndTest(TestCase):
    def test_transfer_apply_verify_flow_updates_migration_file_status(self):
        row = MigrationFileStatus.objects.create(
            source_path="/onprem/share/file.txt",
            dest_cloud=DestCloud.AWS,
            dest_path="/fsx/share/file.txt",
        )
        record = FileRecord(
            file_status_id=row.id,
            source_path=row.source_path,
            dest_path=row.dest_path,
            is_dir=False,
            source_acl=SOURCE_ACL,
        )
        adapter = AWSAdapter(
            transport=FakeTransport(),
            dest_acl_reader=FakeDestACLReader({row.dest_path: SOURCE_ACL}),
        )

        apply_transfer_result(adapter.transfer([record]))
        row.refresh_from_db()
        self.assertEqual(row.transfer_status, TransferStatus.TRANSFERRED)
        self.assertTrue(row.transfer_checksum_match)

        apply_permission_result(adapter.apply_permissions([record], mapping_table_version="n/a"))
        row.refresh_from_db()
        self.assertEqual(row.permission_status, PermissionStatus.APPLIED)
        self.assertEqual(row.ace_count_source, 1)
        self.assertEqual(row.ace_count_dest, 1)

        apply_verify_result(adapter.verify([record]))
        row.refresh_from_db()
        self.assertEqual(row.permission_status, PermissionStatus.VERIFIED)
        self.assertIsNotNone(row.permission_verified_at)

    def test_transfer_failure_increments_retry_count_and_records_error(self):
        row = MigrationFileStatus.objects.create(
            source_path="/onprem/share/file.txt",
            dest_cloud=DestCloud.AWS,
            dest_path="/fsx/share/file.txt",
        )
        record = FileRecord(
            file_status_id=row.id,
            source_path=row.source_path,
            dest_path=row.dest_path,
            is_dir=False,
        )
        adapter = AWSAdapter(
            transport=FakeTransport(fail_dest_paths=frozenset({row.dest_path})),
            dest_acl_reader=FakeDestACLReader({}),
        )

        apply_transfer_result(adapter.transfer([record]))

        row.refresh_from_db()
        self.assertEqual(row.transfer_status, TransferStatus.TRANSFER_FAILED)
        self.assertEqual(row.retry_count, 1)
        self.assertIn("simulated copy failure", row.error_message)


class GCPAdapterUpdatesDbEndToEndTest(TestCase):
    def test_permission_translation_flow_updates_migration_file_status(self):
        row = MigrationFileStatus.objects.create(
            source_path="/onprem/share/file.txt",
            dest_cloud=DestCloud.GCP,
            dest_path="/filestore/share/file.txt",
        )
        record = FileRecord(
            file_status_id=row.id,
            source_path=row.source_path,
            dest_path=row.dest_path,
            is_dir=False,
            source_acl=SOURCE_ACL,
        )
        mapping_table = load_mapping_table()
        adapter = GCPAdapter(
            transport=FakeTransport(),
            acl_applier=FakeNfs4AclApplier(),
            mapping_table=mapping_table,
        )

        apply_transfer_result(adapter.transfer([record]))
        apply_permission_result(
            adapter.apply_permissions([record], mapping_table_version=mapping_table.version)
        )

        row.refresh_from_db()
        self.assertEqual(row.transfer_status, TransferStatus.TRANSFERRED)
        self.assertEqual(row.permission_status, PermissionStatus.APPLIED)
        self.assertEqual(row.mapping_table_version, mapping_table.version)
        self.assertEqual(row.ace_count_source, 1)
        self.assertEqual(row.ace_count_dest, 1)
