from django.test import TestCase

from migration.cloud_adapters.gcp import GCPAdapter
from migration.cloud_adapters.testing import FakeNfs4AclApplier, FakeTransport
from migration.cloud_adapters.types import FileRecord
from migration.discovery.types import ACE, FileACL
from migration.models import MappingReviewQueueEntry
from migration.permission_mapping.loader import load_mapping_table

MAPPING_TABLE = load_mapping_table()

SOURCE_ACL = FileACL(
    owner="DOMAIN\\admin",
    group=None,
    aces=(
        ACE(
            identity="DOMAIN\\jane.doe",
            rights=frozenset({"ReadData", "ReadAttributes"}),
            allow=True,
            inherited=False,
        ),
        ACE(
            identity="DOMAIN\\blocked.user",
            rights=frozenset({"WriteData"}),
            allow=False,
            inherited=False,
        ),
    ),
)


def _record(dest="/filestore/share/file.txt"):
    return FileRecord(
        file_status_id=1,
        source_path="/onprem/share/file.txt",
        dest_path=dest,
        is_dir=False,
        source_acl=SOURCE_ACL,
    )


def test_transfer_copies_bytes():
    transport = FakeTransport()
    adapter = GCPAdapter(transport=transport, acl_applier=FakeNfs4AclApplier(), mapping_table=MAPPING_TABLE)

    result = adapter.transfer([_record()])

    assert transport.calls == [("/onprem/share/file.txt", "/filestore/share/file.txt")]
    assert result.outcomes[0].success is True


class ApplyPermissionsTest(TestCase):
    def test_translates_and_applies_every_ace(self):
        applier = FakeNfs4AclApplier()
        adapter = GCPAdapter(transport=FakeTransport(), acl_applier=applier, mapping_table=MAPPING_TABLE)

        result = adapter.apply_permissions([_record()], mapping_table_version=MAPPING_TABLE.version)

        outcome = result.outcomes[0]
        assert outcome.success is True
        assert outcome.ace_count_source == 2
        assert outcome.ace_count_dest == 2

        applied = applier.state["/filestore/share/file.txt"]
        by_identity = {a.identity: a for a in applied}
        assert by_identity["DOMAIN\\jane.doe"].ace4_bits == frozenset(
            {"ACE4_READ_DATA", "ACE4_READ_ATTRIBUTES"}
        )
        assert by_identity["DOMAIN\\blocked.user"].allow is False
        assert by_identity["DOMAIN\\blocked.user"].ace4_bits == frozenset({"ACE4_WRITE_DATA"})

    def test_unmapped_right_surfaces_as_failed_outcome(self):
        # DeleteSubdirectoriesAndFiles has no mapping row for "file".
        acl = FileACL(
            owner="",
            group=None,
            aces=(
                ACE(
                    identity="DOMAIN\\jane.doe",
                    rights=frozenset({"DeleteSubdirectoriesAndFiles"}),
                    allow=True,
                    inherited=False,
                ),
            ),
        )
        record = FileRecord(
            file_status_id=1,
            source_path="/onprem/share/file.txt",
            dest_path="/filestore/share/file.txt",
            is_dir=False,
            source_acl=acl,
        )
        adapter = GCPAdapter(
            transport=FakeTransport(), acl_applier=FakeNfs4AclApplier(), mapping_table=MAPPING_TABLE
        )

        result = adapter.apply_permissions([record], mapping_table_version=MAPPING_TABLE.version)

        outcome = result.outcomes[0]
        assert outcome.success is False
        assert "unmapped" in outcome.error_message

    def test_non_exact_confidence_mapping_is_queued_for_review_once(self):
        acl = FileACL(
            owner="",
            group=None,
            aces=(
                ACE(
                    identity="DOMAIN\\jane.doe",
                    rights=frozenset({"TakeOwnership"}),
                    allow=True,
                    inherited=False,
                ),
            ),
        )
        record = FileRecord(
            file_status_id=1,
            source_path="/onprem/share/file.txt",
            dest_path="/filestore/share/file.txt",
            is_dir=False,
            source_acl=acl,
        )
        adapter = GCPAdapter(
            transport=FakeTransport(), acl_applier=FakeNfs4AclApplier(), mapping_table=MAPPING_TABLE
        )

        adapter.apply_permissions([record], mapping_table_version=MAPPING_TABLE.version)
        adapter.apply_permissions([record], mapping_table_version=MAPPING_TABLE.version)

        self.assertEqual(MappingReviewQueueEntry.objects.count(), 1)
        entry = MappingReviewQueueEntry.objects.get()
        self.assertEqual(entry.ntfs_right, "TakeOwnership")
        self.assertEqual(entry.confidence, "policy_decision")


class VerifyTest(TestCase):
    def test_verify_succeeds_when_dest_matches_translated_source(self):
        applier = FakeNfs4AclApplier()
        adapter = GCPAdapter(transport=FakeTransport(), acl_applier=applier, mapping_table=MAPPING_TABLE)
        adapter.apply_permissions([_record()], mapping_table_version=MAPPING_TABLE.version)

        result = adapter.verify([_record()])

        assert result.outcomes[0].success is True
        assert result.outcomes[0].ace_count_dest == 2

    def test_verify_fails_when_dest_diverges_from_translated_source(self):
        applier = FakeNfs4AclApplier()
        # Simulate drift: apply, then corrupt the destination ACL.
        adapter = GCPAdapter(transport=FakeTransport(), acl_applier=applier, mapping_table=MAPPING_TABLE)
        adapter.apply_permissions([_record()], mapping_table_version=MAPPING_TABLE.version)
        applier.state["/filestore/share/file.txt"].pop()

        result = adapter.verify([_record()])

        assert result.outcomes[0].success is False
