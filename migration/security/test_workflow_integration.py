"""Phase 8 deliverable: a test confirming no secret value ever appears
in a Temporal workflow history dump.

Runs the real `ApplyPermissionsForSubtree`/`ApplyPermissionsForBatch`
workflow (Phase 5) against a local Temporal server, with a
`FakeSecretsClient` returning an obviously-identifiable fake secret
value for the GCP credential path. `apply_acl` fetches and uses that
credential but never returns it or puts it in any dataclass that
crosses the activity boundary -- this test proves that holds by pulling
the entire recorded workflow history back from the server and scanning
its serialized bytes for the secret string.
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
from migration.security.testing import FakeSecretsClient

pytestmark = pytest.mark.temporal_integration

SOURCE_ROOT = "/onprem/share"
SOURCE_PATH = f"{SOURCE_ROOT}/secret_test.csv"
DEST_PATH = "/filestore/share/secret_test.csv"

# Obviously fake, obviously identifiable -- if this substring shows up
# anywhere in the workflow history dump, the credential leaked into
# durable Temporal state.
FAKE_SECRET_VALUE = "SECRET-VALUE-DO-NOT-LEAK-9f8e7d6c5b4a"


class _PathKeyedFakeACLReader:
    def __init__(self, acls_by_path: dict[str, FileACL]):
        self._acls = acls_by_path

    def read_acl(self, path):
        return self._acls[str(path)]


class NoSecretInWorkflowHistoryTest(TransactionTestCase):
    async def test_secret_value_never_appears_in_workflow_history(self):
        mapping_table = load_mapping_table()
        source_acl = FileACL(
            owner="DOMAIN\\admin",
            group=None,
            aces=(
                ACE(
                    identity="DOMAIN\\jane.doe",
                    rights=frozenset({"ReadData"}),
                    allow=True,
                    inherited=False,
                ),
            ),
        )
        acl_reader = _PathKeyedFakeACLReader({SOURCE_PATH: source_acl})

        row = await MigrationFileStatus.objects.acreate(
            source_path=SOURCE_PATH,
            dest_cloud=DestCloud.GCP,
            dest_path=DEST_PATH,
            transfer_status=TransferStatus.TRANSFERRED,
            permission_status=PermissionStatus.PENDING,
        )

        secrets_client = FakeSecretsClient(
            {"migration/gcp": {"service_account_key": FAKE_SECRET_VALUE}}
        )
        activities = MigrationActivities(
            gcp_adapter=GCPAdapter(
                transport=FakeTransport(),
                acl_applier=FakeNfs4AclApplier(),
                mapping_table=mapping_table,
            ),
            source_acl_reader=acl_reader,
            mapping_table=mapping_table,
            secrets_client=secrets_client,
        )

        client = await Client.connect("localhost:7233")
        workflow_id = f"secret-leak-check-{uuid.uuid4()}"
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
                    mapping_table_version=mapping_table.version,
                    batch_size=10,
                    batches_per_run=1,
                ),
                id=workflow_id,
                task_queue=TASK_QUEUE,
            )

        # Sanity check: the credential was actually fetched and used --
        # otherwise "it never appears" would be true for the trivial,
        # useless reason that the code path never ran.
        self.assertIn("migration/gcp", secrets_client.calls)

        await row.arefresh_from_db()
        self.assertEqual(row.permission_status, PermissionStatus.VERIFIED)

        # The six per-file activities (including apply_acl, the actual
        # ACL-write call) run inside the *child* ApplyPermissionsForBatch
        # workflow, not the parent -- so both histories need checking.
        # Our small fixture (batch_size=10, one file) produces exactly
        # one batch child, "<parent-id>-iter0-batch0".
        workflow_ids = [workflow_id, f"{workflow_id}-iter0-batch0"]
        raw_history = []
        for wf_id in workflow_ids:
            handle = client.get_workflow_handle(wf_id)
            async for event in handle.fetch_history_events():
                raw_history.append(event.SerializeToString())
        history_bytes = b"".join(raw_history)

        # Sanity check the search itself is meaningful: something
        # recognizable from this run (the dest path) really is in there.
        self.assertIn(DEST_PATH.encode(), history_bytes)
        self.assertNotIn(FAKE_SECRET_VALUE.encode(), history_bytes)
