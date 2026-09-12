"""Temporal worker entrypoint.

Run with:

    uv run python -m migration.orchestration.worker

Uses `UnsandboxedWorkflowRunner`: the workflows here execute activities
that go through the Django ORM, and Temporal's default sandbox isn't a
good fit for that combination. Determinism inside the workflow bodies
themselves (no direct I/O, no non-deterministic calls) is preserved by
construction -- all I/O happens in activities.
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


async def run_worker(target_host: str = "localhost:7233") -> None:
    client = await Client.connect(target_host)
    permission_activities = MigrationActivities()
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
