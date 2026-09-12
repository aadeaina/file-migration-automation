"""Runs inside a pod in the cluster: seeds one fixture file, starts a
real ApplyPermissionsForSubtree workflow against the in-cluster
Temporal server, and lets whichever of the (2+) migration-worker pods
picks it up process it -- proving the worker Deployment actually works
as a horizontally-scalable pool of Temporal workers, not just that a
container starts.

Requires the worker Deployment to be running with WORKER_DEMO_MODE=1
(see migration/orchestration/worker.py) so its activities use fakes
for the fixed DEMO_SOURCE_PATH/DEMO_DEST_PATH fixture instead of
shelling out to icacls/robocopy, which don't exist on this plain Linux
image.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")
django.setup()

from temporalio.client import Client

from migration.models import (
    DestCloud,
    MigrationFileStatus,
    PermissionStatus,
    TransferStatus,
)
from migration.orchestration.worker import (
    DEMO_DEST_PATH,
    DEMO_SOURCE_PATH,
    TASK_QUEUE,
)
from migration.orchestration.workflows import (
    ApplyPermissionsForSubtree,
    ApplyPermissionsForSubtreeInput,
)
from migration.permission_mapping.loader import load_mapping_table


async def main():
    MigrationFileStatus.objects.filter(source_path=DEMO_SOURCE_PATH).delete()
    row = MigrationFileStatus.objects.create(
        source_path=DEMO_SOURCE_PATH,
        dest_cloud=DestCloud.AWS,
        dest_path=DEMO_DEST_PATH,
        transfer_status=TransferStatus.TRANSFERRED,
        permission_status=PermissionStatus.PENDING,
    )
    print(f"Seeded MigrationFileStatus id={row.id}")

    mapping_table = load_mapping_table()
    client = await Client.connect(os.environ["TEMPORAL_ADDRESS"])

    workflow_id = f"k8s-smoke-{uuid.uuid4().hex[:8]}"
    print(f"Starting {workflow_id} on task queue {TASK_QUEUE!r} (handled by the worker Deployment)...")
    result = await client.execute_workflow(
        ApplyPermissionsForSubtree.run,
        ApplyPermissionsForSubtreeInput(
            root_path="/onprem/k8s-smoke",
            dest_cloud=DestCloud.AWS,
            mapping_table_version=mapping_table.version,
            batch_size=10,
            batches_per_run=1,
        ),
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    print(f"Workflow result: {result}")

    row.refresh_from_db()
    print(
        f"MigrationFileStatus after run: permission_status={row.permission_status} "
        f"ace_count_source={row.ace_count_source} ace_count_dest={row.ace_count_dest}"
    )

    assert row.permission_status == PermissionStatus.VERIFIED, "expected the file to be verified"
    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    asyncio.run(main())
