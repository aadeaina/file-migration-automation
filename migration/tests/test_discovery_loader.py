from django.test import TestCase

from migration.discovery.manifest import load_manifest_into_db
from migration.discovery.types import ACE, FileACL, ManifestEntry
from migration.models import (
    DestCloud,
    MigrationFileStatus,
    PermissionStatus,
    TransferStatus,
)


def _entry(path: str, is_dir: bool = False) -> ManifestEntry:
    return ManifestEntry(
        path=path,
        is_dir=is_dir,
        size=0 if is_dir else 123,
        depth=path.count("/"),
        acl=FileACL(
            owner="DOMAIN\\admin",
            group=None,
            aces=(
                ACE(
                    identity="BUILTIN\\Administrators",
                    rights=frozenset({"FullControl"}),
                    allow=True,
                    inherited=False,
                    inherit_to_files=True,
                    inherit_to_subfolders=True,
                ),
            ),
        ),
    )


class LoadManifestIntoDbTest(TestCase):
    def test_populates_one_row_per_file_per_dest_cloud(self):
        manifest = [
            _entry("folder", is_dir=True),
            _entry("folder/file_a.txt"),
            _entry("folder/file_b.txt"),
        ]

        created = load_manifest_into_db(manifest)

        # Directories are skipped; each of the 2 files fans out to 3 clouds.
        self.assertEqual(len(created), 6)
        self.assertEqual(MigrationFileStatus.objects.count(), 6)

        for cloud in (DestCloud.AWS, DestCloud.AZURE, DestCloud.GCP):
            self.assertEqual(
                MigrationFileStatus.objects.filter(dest_cloud=cloud).count(), 2
            )

        row = MigrationFileStatus.objects.filter(
            source_path="folder/file_a.txt", dest_cloud=DestCloud.AWS
        ).get()
        self.assertEqual(row.transfer_status, TransferStatus.QUEUED)
        self.assertEqual(row.permission_status, PermissionStatus.PENDING)

    def test_respects_explicit_dest_clouds(self):
        manifest = [_entry("folder/file_a.txt")]

        load_manifest_into_db(manifest, dest_clouds=(DestCloud.GCP,))

        self.assertEqual(MigrationFileStatus.objects.count(), 1)
        self.assertEqual(
            MigrationFileStatus.objects.get().dest_cloud, DestCloud.GCP
        )
