"""Phase 6 deliverable: run the diff tool against a fixture, confirm it
correctly classifies at least one missing_grant and one
inheritance_divergence, and confirm the gate is blocked given seeded
"all block" SignOffPolicy rows.
"""

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from migration.cloud_adapters.testing import FakeDestACLReader
from migration.diffing.gate import blocked_entries_for_subtree, is_subtree_blocked
from migration.diffing.run_diff import diff_file, diff_subtree
from migration.discovery.types import ACE, FileACL
from migration.models import (
    DestCloud,
    EffectivePermissionDiff,
    MigrationFileStatus,
    MismatchType,
    PolicyAction,
    SignOffPolicy,
)

SOURCE_PATH = "/onprem/share/report.csv"
DEST_PATH = "/fsx/share/report.csv"


class DiffFileTest(TestCase):
    def setUp(self):
        # Seed all four mismatch-type policies as "block" -- the default,
        # conservative starting policy per the design doc.
        for mismatch_type in MismatchType.values:
            SignOffPolicy.objects.create(
                policy_version="v1",
                mismatch_type=mismatch_type,
                action=PolicyAction.BLOCK,
                active=True,
                updated_by="test-seed",
            )

        self.row = MigrationFileStatus.objects.create(
            source_path=SOURCE_PATH,
            dest_cloud=DestCloud.AWS,
            dest_path=DEST_PATH,
        )

        # identity A: source grants read+write explicitly; dest only got
        # read applied -- a missing_grant.
        # identity B: source grants read via an inherited ACE; dest has
        # the same effective read access but via an explicit ACE --
        # an inheritance_divergence (access matches, provenance doesn't).
        source_acl = FileACL(
            owner="DOMAIN\\admin",
            group=None,
            aces=(
                ACE(identity="DOMAIN\\a", rights=frozenset({"ReadData", "WriteData"}), allow=True, inherited=False),
                ACE(identity="DOMAIN\\b", rights=frozenset({"ReadData"}), allow=True, inherited=True),
            ),
        )
        dest_acl = FileACL(
            owner="DOMAIN\\admin",
            group=None,
            aces=(
                ACE(identity="DOMAIN\\a", rights=frozenset({"ReadData"}), allow=True, inherited=False),
                ACE(identity="DOMAIN\\b", rights=frozenset({"ReadData"}), allow=True, inherited=False),
            ),
        )
        self.source_reader = FakeDestACLReader({SOURCE_PATH: source_acl})
        self.dest_reader = FakeDestACLReader({DEST_PATH: dest_acl})

    def test_classifies_missing_grant_and_inheritance_divergence(self):
        created = diff_file(self.row, self.source_reader, self.dest_reader)

        by_identity = {d.identity: d for d in created}
        self.assertEqual(by_identity["DOMAIN\\a"].mismatch_type, MismatchType.MISSING_GRANT)
        self.assertEqual(by_identity["DOMAIN\\a"].source_access, ["read", "write"])
        self.assertEqual(by_identity["DOMAIN\\a"].dest_access, ["read"])
        self.assertEqual(by_identity["DOMAIN\\a"].policy_version_at_eval, "v1")

        self.assertEqual(
            by_identity["DOMAIN\\b"].mismatch_type, MismatchType.INHERITANCE_DIVERGENCE
        )
        self.assertEqual(by_identity["DOMAIN\\b"].policy_version_at_eval, "v1")

    def test_gate_is_blocked_via_the_view_not_reimplemented_logic(self):
        diff_file(self.row, self.source_reader, self.dest_reader)

        self.assertTrue(is_subtree_blocked("/onprem/share"))
        gate_identities = set(
            blocked_entries_for_subtree("/onprem/share").values_list("identity", flat=True)
        )
        self.assertEqual(gate_identities, {"DOMAIN\\a", "DOMAIN\\b"})

    def test_rerun_replaces_unreviewed_diffs_but_keeps_reviewed_ones(self):
        diff_file(self.row, self.source_reader, self.dest_reader)
        reviewed_diff = EffectivePermissionDiff.objects.get(identity="DOMAIN\\a")
        reviewed_diff.reviewed = True
        reviewed_diff.save()

        # Second run with identical fixture data: the reviewed row for A
        # must survive untouched; B's unreviewed row gets recomputed.
        diff_file(self.row, self.source_reader, self.dest_reader)

        reviewed_diff.refresh_from_db()
        self.assertTrue(reviewed_diff.reviewed)
        self.assertEqual(
            EffectivePermissionDiff.objects.filter(identity="DOMAIN\\a").count(), 1
        )
        self.assertEqual(
            EffectivePermissionDiff.objects.filter(
                identity="DOMAIN\\b", reviewed=False
            ).count(),
            1,
        )

    def test_no_mismatch_when_access_and_provenance_both_match(self):
        matching_dest_acl = FileACL(
            owner="",
            group=None,
            aces=(
                ACE(identity="DOMAIN\\a", rights=frozenset({"ReadData", "WriteData"}), allow=True, inherited=False),
                ACE(identity="DOMAIN\\b", rights=frozenset({"ReadData"}), allow=True, inherited=True),
            ),
        )
        dest_reader = FakeDestACLReader({DEST_PATH: matching_dest_acl})

        created = diff_file(self.row, self.source_reader, dest_reader)

        self.assertEqual(created, [])
        self.assertFalse(is_subtree_blocked("/onprem/share"))

    def test_a_file_with_no_prior_diffs_and_no_new_mismatches_issues_one_query(self):
        """The common case at scale: most files have never had a diff row
        and produce none now. That should cost one read query, not a
        delete + a separate settled-check query on top."""
        clean_row = MigrationFileStatus.objects.create(
            source_path="/onprem/share/clean.csv",
            dest_cloud=DestCloud.AWS,
            dest_path="/fsx/share/clean.csv",
        )
        acl = FileACL(
            owner="",
            group=None,
            aces=(ACE(identity="DOMAIN\\a", rights=frozenset({"ReadData"}), allow=True, inherited=False),),
        )
        reader = FakeDestACLReader(
            {"/onprem/share/clean.csv": acl, "/fsx/share/clean.csv": acl}
        )

        with CaptureQueriesContext(connection) as ctx:
            created = diff_file(clean_row, reader, reader)

        self.assertEqual(created, [])
        diff_queries = [q for q in ctx.captured_queries if "effective_permission_diff" in q["sql"]]
        self.assertEqual(len(diff_queries), 1)


class DiffSubtreeScalabilityTest(TestCase):
    def test_streams_rows_via_iterator_and_processes_every_file(self):
        for i in range(5):
            MigrationFileStatus.objects.create(
                source_path=f"/onprem/share/file_{i}.csv",
                dest_cloud=DestCloud.AWS,
                dest_path=f"/fsx/share/file_{i}.csv",
            )
        acl = FileACL(owner="", group=None, aces=())
        reader = FakeDestACLReader(
            {
                p: acl
                for i in range(5)
                for p in (f"/onprem/share/file_{i}.csv", f"/fsx/share/file_{i}.csv")
            }
        )

        created = diff_subtree(
            "/onprem/share", DestCloud.AWS, reader, reader, chunk_size=2
        )

        self.assertEqual(created, [])  # no ACEs on either side -> nothing to diff

    def test_collect_created_false_skips_building_the_return_list(self):
        row = MigrationFileStatus.objects.create(
            source_path="/onprem/share/x.csv", dest_cloud=DestCloud.AWS, dest_path="/fsx/share/x.csv"
        )
        source_acl = FileACL(
            owner="", group=None,
            aces=(ACE(identity="DOMAIN\\a", rights=frozenset({"ReadData"}), allow=True, inherited=False),),
        )
        dest_acl = FileACL(owner="", group=None, aces=())
        reader = FakeDestACLReader(
            {"/onprem/share/x.csv": source_acl, "/fsx/share/x.csv": dest_acl}
        )

        result = diff_subtree(
            "/onprem/share", DestCloud.AWS, reader, reader, collect_created=False
        )

        self.assertEqual(result, [])
        # The diff was still written to the DB -- just not returned.
        self.assertEqual(
            EffectivePermissionDiff.objects.filter(file_id=row.id).count(), 1
        )
