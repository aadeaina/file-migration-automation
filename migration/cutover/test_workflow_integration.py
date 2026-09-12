"""Phase 7 deliverable: a simulated run (mock USN Journal events -- i.e.
we seed `ShadowSyncStatus`/`LagStabilityCheck` directly, standing in for
the separately-running `ContinuousShadowSyncWorkflow` that would
normally write them; no real file server needed) exercising both
cutover modes and both fallback paths.

Requires a local Temporal dev server on localhost:7233, same as
Phase 5's integration test.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.test import TransactionTestCase
from django.utils import timezone
from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from migration.cutover.activities import CutoverActivities
from migration.cutover.workflows import (
    AutoFallbackEvaluationWorkflow,
    CutoverWorkflow,
    CutoverWorkflowInput,
)
from migration.models import (
    CutoverConfig,
    CutoverMode,
    CutoverRun,
    CutoverRunStatus,
    LagStabilityCheck,
    PolicyAction,
    ShadowSyncStatus,
    SignOffPolicy,
)
from migration.orchestration.worker import TASK_QUEUE

pytestmark = pytest.mark.temporal_integration

SCOPE = "/onprem/share/finance"


async def _seed_all_log_only_policies():
    from migration.models import MismatchType

    for mismatch_type in MismatchType.values:
        await SignOffPolicy.objects.acreate(
            policy_version="v1",
            mismatch_type=mismatch_type,
            action=PolicyAction.LOG_ONLY,
            active=True,
            updated_by="test-seed",
        )


async def _run_workflow(workflow_cls, workflow_input, activities: CutoverActivities):
    client = await Client.connect("localhost:7233")
    async with Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[workflow_cls],
        activities=[
            activities.check_cutover_readiness,
            activities.announce,
            activities.run_diff_gate,
            activities.start_cutover_run,
            activities.finish_cutover_run,
            activities.evaluate_auto_fallback_activity,
        ],
        activity_executor=ThreadPoolExecutor(max_workers=4),
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        args = [workflow_input] if workflow_input is not None else []
        return await client.execute_workflow(
            workflow_cls.run,
            *args,
            id=f"{workflow_cls.__name__}-{uuid.uuid4()}",
            task_queue=TASK_QUEUE,
        )


class CutoverWorkflowIntegrationTest(TransactionTestCase):
    async def test_shadow_write_completes_when_lag_is_stable_and_gate_is_clear(self):
        await _seed_all_log_only_policies()
        await CutoverConfig.objects.acreate(
            scope=SCOPE,
            mode=CutoverMode.SHADOW_WRITE,
            max_acceptable_lag_seconds=60,
            lag_stability_window_minutes=15,
            updated_by="admin",
        )
        # Stand-in for ContinuousShadowSyncWorkflow's USN-Journal-driven
        # writes: lag is low and has been stable for the required window.
        await ShadowSyncStatus.objects.acreate(
            subtree_path=f"{SCOPE}/reports",
            last_sync_completed_at=timezone.now(),
            last_sync_lag_seconds=5,
            pending_change_count=0,
            consecutive_stable_checks=10,
        )

        result = await _run_workflow(
            CutoverWorkflow, CutoverWorkflowInput(scope=SCOPE, mode=CutoverMode.SHADOW_WRITE), CutoverActivities()
        )

        self.assertEqual(result.status, "completed")
        run = await CutoverRun.objects.aget(scope=SCOPE, mode=CutoverMode.SHADOW_WRITE)
        self.assertEqual(run.status, CutoverRunStatus.COMPLETED)
        self.assertIsNotNone(run.completed_at)

    async def test_shadow_write_aborts_when_lag_is_not_yet_stable(self):
        await _seed_all_log_only_policies()
        await CutoverConfig.objects.acreate(
            scope=SCOPE,
            mode=CutoverMode.SHADOW_WRITE,
            max_acceptable_lag_seconds=60,
            lag_stability_window_minutes=15,
            updated_by="admin",
        )
        await ShadowSyncStatus.objects.acreate(
            subtree_path=f"{SCOPE}/reports",
            last_sync_completed_at=timezone.now(),
            last_sync_lag_seconds=9999,  # way over threshold
            pending_change_count=500,
            consecutive_stable_checks=0,
        )

        result = await _run_workflow(
            CutoverWorkflow, CutoverWorkflowInput(scope=SCOPE, mode=CutoverMode.SHADOW_WRITE), CutoverActivities()
        )

        self.assertEqual(result.status, "aborted")
        run = await CutoverRun.objects.aget(scope=SCOPE, mode=CutoverMode.SHADOW_WRITE)
        self.assertEqual(run.status, CutoverRunStatus.ABORTED)

    async def test_hard_freeze_completes_without_waiting_on_lag(self):
        await _seed_all_log_only_policies()
        # Deliberately no ShadowSyncStatus/CutoverConfig at all -- hard_freeze
        # doesn't consult shadow-sync readiness.

        result = await _run_workflow(
            CutoverWorkflow, CutoverWorkflowInput(scope=SCOPE, mode=CutoverMode.HARD_FREEZE), CutoverActivities()
        )

        self.assertEqual(result.status, "completed")
        run = await CutoverRun.objects.aget(scope=SCOPE, mode=CutoverMode.HARD_FREEZE)
        self.assertEqual(run.status, CutoverRunStatus.COMPLETED)

    async def test_cutover_is_blocked_when_sign_off_gate_has_open_entries(self):
        from migration.models import (
            DestCloud,
            MigrationFileStatus,
            MismatchType,
        )

        # All-block policy this time, plus a real blocking diff.
        for mismatch_type in MismatchType.values:
            await SignOffPolicy.objects.acreate(
                policy_version="v1",
                mismatch_type=mismatch_type,
                action=PolicyAction.BLOCK,
                active=True,
                updated_by="test-seed",
            )
        await MigrationFileStatus.objects.acreate(
            source_path=f"{SCOPE}/report.csv",
            dest_cloud=DestCloud.AWS,
            dest_path=f"{SCOPE}/report.csv",
        )
        from migration.models import EffectivePermissionDiff

        row = await MigrationFileStatus.objects.aget(source_path=f"{SCOPE}/report.csv")
        await EffectivePermissionDiff.objects.acreate(
            file=row,
            identity="DOMAIN\\jane",
            source_access=["read", "write"],
            dest_access=["read"],
            mismatch_type=MismatchType.MISSING_GRANT,
            severity="warning",
        )

        result = await _run_workflow(
            CutoverWorkflow, CutoverWorkflowInput(scope=SCOPE, mode=CutoverMode.HARD_FREEZE), CutoverActivities()
        )

        self.assertEqual(result.status, "blocked")
        run = await CutoverRun.objects.aget(scope=SCOPE, mode=CutoverMode.HARD_FREEZE)
        self.assertEqual(run.status, CutoverRunStatus.BLOCKED)
        self.assertIn("sign-off gate", run.blocked_reason)


class FallbackIntegrationTest(TransactionTestCase):
    async def test_manual_path_surfaces_without_acting_automatically(self):
        for _ in range(5):
            await LagStabilityCheck.objects.acreate(
                subtree_path=f"{SCOPE}/reports", lag_seconds=999, threshold_seconds=60, passed=False
            )
        await CutoverConfig.objects.acreate(
            scope=SCOPE, auto_fallback_enabled=False, updated_by="admin"
        )

        result = await _run_workflow(AutoFallbackEvaluationWorkflow, None, CutoverActivities())

        self.assertEqual(result, [])
        self.assertFalse(
            await CutoverConfig.objects.filter(scope=f"{SCOPE}/reports").aexists()
        )

    async def test_automatic_path_inserts_hard_freeze_when_opted_in(self):
        for _ in range(5):
            await LagStabilityCheck.objects.acreate(
                subtree_path=f"{SCOPE}/reports", lag_seconds=999, threshold_seconds=60, passed=False
            )
        await CutoverConfig.objects.acreate(
            scope=SCOPE,
            mode=CutoverMode.SHADOW_WRITE,
            auto_fallback_enabled=True,
            updated_by="admin",
        )

        result = await _run_workflow(AutoFallbackEvaluationWorkflow, None, CutoverActivities())

        self.assertEqual(result, [f"{SCOPE}/reports"])
        new_config = await CutoverConfig.objects.aget(
            scope=f"{SCOPE}/reports", active=True
        )
        self.assertEqual(new_config.mode, CutoverMode.HARD_FREEZE)
        self.assertEqual(new_config.updated_by, "system:auto-fallback-workflow")
