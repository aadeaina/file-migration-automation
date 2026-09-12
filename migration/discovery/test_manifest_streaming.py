"""Scalability fixes: walk_tree is a generator (never holds the whole
tree in memory), the manifest file is JSONL (streamed, not one giant
JSON array), and load_manifest_into_db writes in chunks rather than
building every row before a single bulk_create call.
"""

from __future__ import annotations

import inspect

from django.test import TestCase

from migration.discovery.crawler import walk_tree
from migration.discovery.manifest import load_manifest_into_db, read_manifest, write_manifest
from migration.discovery.testing import FakeACLReader
from migration.discovery.types import ACE, FileACL, ManifestEntry
from migration.models import DestCloud, MigrationFileStatus


def _acl() -> FileACL:
    return FileACL(
        owner="DOMAIN\\admin",
        group=None,
        aces=(ACE(identity="DOMAIN\\jane", rights=frozenset({"ReadData"}), allow=True, inherited=False),),
    )


def test_walk_tree_is_a_generator_not_a_list(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    reader = FakeACLReader({"a.txt": _acl()}, root=tmp_path)

    result = walk_tree(tmp_path, reader)

    assert inspect.isgenerator(result)
    assert list(result) != []  # still fully usable once consumed


def test_manifest_round_trips_through_jsonl_without_loading_it_all_at_once(tmp_path):
    entries = [
        ManifestEntry(path=f"file_{i}.txt", is_dir=False, size=1, depth=0, acl=_acl())
        for i in range(5)
    ]
    manifest_path = tmp_path / "manifest.jsonl"

    written = write_manifest(entries, manifest_path)
    assert written == 5

    # One JSON object per line -- not a single array spanning the file.
    lines = manifest_path.read_text().strip().splitlines()
    assert len(lines) == 5

    read_back = read_manifest(manifest_path)
    assert inspect.isgenerator(read_back)
    round_tripped = list(read_back)
    assert [e.path for e in round_tripped] == [f"file_{i}.txt" for i in range(5)]


class LoadManifestIntoDbChunkingTest(TestCase):
    def test_writes_in_batches_smaller_than_the_full_manifest(self):
        entries = (
            ManifestEntry(path=f"file_{i}.txt", is_dir=False, size=1, depth=0, acl=_acl())
            for i in range(10)
        )

        created = load_manifest_into_db(entries, dest_clouds=(DestCloud.AWS,), batch_size=3)

        # 10 files x 1 cloud = 10 rows, written across multiple batch_size=3
        # chunks -- correctness shouldn't depend on the whole manifest
        # fitting in one bulk_create call.
        self.assertEqual(len(created), 10)
        self.assertEqual(MigrationFileStatus.objects.count(), 10)

    def test_consumes_a_generator_without_requiring_a_list(self):
        def gen():
            for i in range(4):
                yield ManifestEntry(
                    path=f"file_{i}.txt", is_dir=False, size=1, depth=0, acl=_acl()
                )

        created = load_manifest_into_db(gen(), dest_clouds=(DestCloud.GCP,), batch_size=2)

        self.assertEqual(len(created), 4)

    def test_collect_created_false_skips_accumulating_the_return_list(self):
        entries = [
            ManifestEntry(path=f"file_{i}.txt", is_dir=False, size=1, depth=0, acl=_acl())
            for i in range(4)
        ]

        created = load_manifest_into_db(
            entries, dest_clouds=(DestCloud.AWS,), batch_size=2, collect_created=False
        )

        self.assertEqual(created, [])
        self.assertEqual(MigrationFileStatus.objects.count(), 4)
