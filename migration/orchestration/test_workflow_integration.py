"""Phase 5 deliverable: a Temporal worker running locally that executes
`ApplyPermissionsForSubtree` end to end against a test fixture, with a
demonstrated re-run after bumping the mapping table version.

Requires a local Temporal dev server (`temporal server start-dev`) on
localhost:7233 -- this is an integration test, not part of the fast
unit suite. Uses `TransactionTestCase` (not `TestCase`) because
activities run in worker threads with their own DB connections, which
don't see a `TestCase`'s uncommitted wrapping transaction.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.test import TransactionTestCase
from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from migration.cloud_adapters.gcp import GCPAdapter
from migration.cloud_adapters.testing import FakeNfs4AclApplier, FakeTransport
from migration.discovery.types import ACE, FileACL
from migration.models import DestCloud, MigrationFileStatus, PermissionStatus, TransferStatus
from migration.orchestration.activities import MigrationActivities
from migration.orchestration.worker import TASK_QUEUE
from migration.orchestration.workflows import (
    ApplyPermissionsForBatch,
    ApplyPermissionsForSubtree,
    ApplyPermissionsForSubtreeInput,
)
from migration.permission_mapping.loader import load_mapping_table

pytestmark = pytest.mark.temporal_integration

SOURCE_ROOT = "/onprem/share"
SOURCE_PATH = f"{SOURCE_ROOT}/report.csv"
DEST_PATH = "/filestore/share/report.csv"


class _PathKeyedFakeACLReader:
    """Activities read ACLs by absolute path; key directly on the string
    (Phase 2's `FakeACLReader` instead keys relative to a crawl root,
    which doesn't apply here)."""

    def __init__(self, acls_by_path: dict[str, FileACL]):
        self._acls = acls_by_path

    def read_acl(self, path):
        return self._acls[str(path)]


class ApplyPermissionsForSubtreeIntegrationTest(TransactionTestCase):
    async def _run_subtree_workflow(self, activities: MigrationActivities, mapping_table_version: str):
        client = await Client.connect("localhost:7233")
        async with Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[ApplyPermissionsForSubtree, ApplyPermissionsForBatch],
            activities=[
                activities.read_source_acl,
                activities.resolve_identity,
                activities.translate_ace_activity,
                activities.apply_acl,
                activities.verify_acl,
                activities.write_audit_record,
                activities.list_pending_file_ids,
            ],
            activity_executor=ThreadPoolExecutor(max_workers=4),
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            await client.execute_workflow(
                ApplyPermissionsForSubtree.run,
                ApplyPermissionsForSubtreeInput(
                    root_path=SOURCE_ROOT,
                    dest_cloud=DestCloud.GCP,
                    mapping_table_version=mapping_table_version,
                    batch_size=10,
                    batches_per_run=1,
                ),
                id=f"apply-permissions-subtree-{uuid.uuid4()}",
                task_queue=TASK_QUEUE,
            )

    async def test_first_run_applies_and_verifies_then_rerun_after_mapping_fix(self):
        mapping_table = load_mapping_table()
        source_acl = FileACL(
            owner="DOMAIN\\admin",
            group=None,
            aces=(
                ACE(
                    identity="DOMAIN\\jane.doe",
                    rights=frozenset({"ReadData", "ReadAttributes"}),
                    allow=True,
                    inherited=False,
                ),
            ),
        )
        acl_reader = _PathKeyedFakeACLReader({SOURCE_PATH: source_acl})
        applier = FakeNfs4AclApplier()

        row = await MigrationFileStatus.objects.acreate(
            source_path=SOURCE_PATH,
            dest_cloud=DestCloud.GCP,
            dest_path=DEST_PATH,
            transfer_status=TransferStatus.TRANSFERRED,
            permission_status=PermissionStatus.PENDING,
        )

        activities = MigrationActivities(
            gcp_adapter=GCPAdapter(
                transport=FakeTransport(), acl_applier=applier, mapping_table=mapping_table
            ),
            source_acl_reader=acl_reader,
            mapping_table=mapping_table,
        )

        # --- First run: pending -> applied -> verified ---
        await self._run_subtree_workflow(activities, mapping_table.version)

        await row.arefresh_from_db()
        self.assertEqual(row.permission_status, PermissionStatus.VERIFIED)
        self.assertEqual(row.mapping_table_version, mapping_table.version)
        self.assertEqual(row.ace_count_dest, 1)
        self.assertIsNotNone(row.workflow_id)
        # Phase 5 only touches permissions -- the transfer side (bytes) is
        # untouched by this workflow.
        self.assertEqual(row.transfer_status, TransferStatus.TRANSFERRED)

        # --- Simulate: mapping table gets fixed (new version), and this
        # file's *old* result is now considered failed under the old
        # version (as if verification against the new table would fail
        # until reprocessed). ---
        new_version = f"{mapping_table.version}-fixed"
        await MigrationFileStatus.objects.filter(id=row.id).aupdate(
            permission_status=PermissionStatus.FAILED,
        )

        # --- Re-run scoped to the new mapping table version: only this
        # file (failed, old version) is a candidate; it gets reprocessed
        # without any byte re-copy (transfer_status never touched). ---
        await self._run_subtree_workflow(activities, new_version)

        await row.arefresh_from_db()
        self.assertEqual(row.permission_status, PermissionStatus.VERIFIED)
        self.assertEqual(row.mapping_table_version, new_version)
        self.assertEqual(row.transfer_status, TransferStatus.TRANSFERRED)

        # --- A further run at the same (already-succeeded) version finds
        # nothing left to do -- already-verified files are left alone. ---
        remaining = await MigrationFileStatus.objects.filter(
            dest_cloud=DestCloud.GCP,
            source_path__startswith=SOURCE_ROOT,
        ).exclude(
            permission_status__in=["applied", "verified"], mapping_table_version=new_version
        ).acount()
        self.assertEqual(remaining, 0)
