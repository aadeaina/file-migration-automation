"""Temporal worker entrypoint.

Run with:

    uv run python -m migration.orchestration.worker

Uses `UnsandboxedWorkflowRunner`: the workflows here execute activities
that go through the Django ORM, and Temporal's default sandbox isn't a
good fit for that combination. Determinism inside the workflow bodies
themselves (no direct I/O, no non-deterministic calls) is preserved by
construction -- all I/O happens in activities.

Set `WORKER_DEMO_MODE=1` to construct `MigrationActivities` with the
same fakes `scripts/demo_run.py` uses (`FakeTransport`,
`FakeNfs4AclApplier`, a dict-keyed ACL reader for a fixed demo fixture)
instead of the real defaults (which shell out to `icacls`/`robocopy`/
`azcopy`/`nfs4_setfacl` and need a real Windows-adjacent host to work).
This is what lets `scripts/k8s_smoke_test.py` prove a full pending ->
verified run through the worker Deployment in a plain Linux container
with no real file server or cloud APIs reachable.
"""

from __future__ import annotations

import asyncio
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from migration.cutover.activities import CutoverActivities
from migration.cutover.workflows import AutoFallbackEvaluationWorkflow, CutoverWorkflow
from migration.orchestration.activities import MigrationActivities
from migration.orchestration.workflows import (
    ApplyPermissionsForBatch,
    ApplyPermissionsForSubtree,
)

TASK_QUEUE = "file-migration"

# Fixture paths for WORKER_DEMO_MODE, shared with scripts/k8s_smoke_test.py.
DEMO_SOURCE_PATH = "/onprem/k8s-smoke/report.csv"
DEMO_DEST_PATH = "/fsx/k8s-smoke/report.csv"


def _build_demo_permission_activities() -> MigrationActivities:
    from migration.cloud_adapters.aws import AWSAdapter
    from migration.cloud_adapters.testing import FakeTransport
    from migration.discovery.testing import DictACLReader
    from migration.discovery.types import ACE, FileACL

    acl = FileACL(
        owner="DOMAIN\\admin",
        group=None,
        aces=(ACE(identity="DOMAIN\\jane.doe", rights=frozenset({"ReadData"}), allow=True, inherited=False),),
    )
    return MigrationActivities(
        aws_adapter=AWSAdapter(
            transport=FakeTransport(),
            dest_acl_reader=DictACLReader({DEMO_DEST_PATH: acl}),
        ),
        source_acl_reader=DictACLReader({DEMO_SOURCE_PATH: acl}),
    )


async def run_worker(target_host: str | None = None) -> None:
    target_host = target_host or os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
    client = await Client.connect(target_host)
    demo_mode = os.environ.get("WORKER_DEMO_MODE") == "1"
    permission_activities = (
        _build_demo_permission_activities() if demo_mode else MigrationActivities()
    )
    cutover_activities = CutoverActivities()

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[
            ApplyPermissionsForSubtree,
            ApplyPermissionsForBatch,
            CutoverWorkflow,
            AutoFallbackEvaluationWorkflow,
        ],
        activities=[
            permission_activities.read_source_acl,
            permission_activities.resolve_identity,
            permission_activities.translate_ace_activity,
            permission_activities.apply_acl,
            permission_activities.verify_acl,
            permission_activities.write_audit_record,
            permission_activities.list_pending_file_ids,
            cutover_activities.check_cutover_readiness,
            cutover_activities.announce,
            cutover_activities.run_diff_gate,
            cutover_activities.start_cutover_run,
            cutover_activities.finish_cutover_run,
            cutover_activities.evaluate_auto_fallback_activity,
        ],
        activity_executor=ThreadPoolExecutor(max_workers=10),
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(run_worker())
