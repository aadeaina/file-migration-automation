"""Phase 7 workflows: `CutoverWorkflow` (both modes) and
`AutoFallbackEvaluationWorkflow` (the opt-in automatic fallback path).

`shadow_write`: check readiness (lag stable under threshold) -> final
delta pass -> flip source read-only -> catch-up pass -> repoint clients
-> Phase 6 diff gate -> complete (or blocked, if the gate says so).

`hard_freeze`: skips the readiness check entirely (that's the point of
the mode) -> announce write-block -> single final sync -> repoint ->
same diff gate -> complete/blocked.

The diff gate is where both modes converge and where either can still
end up `blocked` rather than `completed` -- per the design doc,
`sign_off_gate` having open, blocking entries halts stage promotion
regardless of how clean the sync itself was.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from migration.cutover.activities import (
    CheckCutoverReadinessInput,
    CheckCutoverReadinessResult,
    FinishCutoverRunInput,
    RunDiffGateInput,
    RunDiffGateResult,
    ScopeActionInput,
    StartCutoverRunInput,
)

ACTIVITY_TIMEOUT = timedelta(seconds=30)
DEFAULT_RETRY_POLICY = RetryPolicy(maximum_attempts=3)


@dataclass
class CutoverWorkflowInput:
    scope: str
    mode: str  # CutoverMode.SHADOW_WRITE or CutoverMode.HARD_FREEZE


@dataclass
class CutoverWorkflowResult:
    status: str  # CutoverRunStatus value
    reason: str | None = None


@workflow.defn
class CutoverWorkflow:
    @workflow.run
    async def run(self, input: CutoverWorkflowInput) -> CutoverWorkflowResult:
        run_id = await workflow.execute_activity(
            "start_cutover_run",
            StartCutoverRunInput(
                scope=input.scope, mode=input.mode, workflow_id=workflow.info().workflow_id
            ),
            result_type=int,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=DEFAULT_RETRY_POLICY,
        )

        readiness = await workflow.execute_activity(
            "check_cutover_readiness",
            CheckCutoverReadinessInput(scope=input.scope, mode=input.mode),
            result_type=CheckCutoverReadinessResult,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=DEFAULT_RETRY_POLICY,
        )
        if not readiness.ready:
            await self._finish(run_id, "aborted", readiness.reason)
            return CutoverWorkflowResult(status="aborted", reason=readiness.reason)

        if input.mode == "hard_freeze":
            await self._announce(input.scope, "announce_write_block")
            await self._announce(input.scope, "single_final_sync")
        else:
            await self._announce(input.scope, "final_delta_pass")
            await self._announce(input.scope, "flip_source_read_only")
            await self._announce(input.scope, "catch_up_pass")

        await self._announce(input.scope, "repoint_clients")

        gate: RunDiffGateResult = await workflow.execute_activity(
            "run_diff_gate",
            RunDiffGateInput(scope=input.scope),
            result_type=RunDiffGateResult,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=DEFAULT_RETRY_POLICY,
        )

        if gate.blocked:
            reason = "sign-off gate has open blocking entries"
            await self._finish(run_id, "blocked", reason)
            return CutoverWorkflowResult(status="blocked", reason=reason)

        await self._finish(run_id, "completed", None)
        return CutoverWorkflowResult(status="completed")

    async def _announce(self, scope: str, action: str) -> None:
        await workflow.execute_activity(
            "announce",
            ScopeActionInput(scope=scope, action=action),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=DEFAULT_RETRY_POLICY,
        )

    async def _finish(self, run_id: int, status: str, reason: str | None) -> None:
        await workflow.execute_activity(
            "finish_cutover_run",
            FinishCutoverRunInput(run_id=run_id, status=status, blocked_reason=reason),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=DEFAULT_RETRY_POLICY,
        )


@workflow.defn
class AutoFallbackEvaluationWorkflow:
    """The opt-in automatic fallback path: evaluates
    `subtrees_needing_fallback_review` and inserts a hard_freeze
    `CutoverConfig` row for any scope that both appears there and has
    opted in via `auto_fallback_enabled`. A no-op for everything else,
    including scopes already on hard_freeze (guarded in
    `evaluate_auto_fallback` itself)."""

    @workflow.run
    async def run(self) -> list[str]:
        return await workflow.execute_activity(
            "evaluate_auto_fallback_activity",
            result_type=list[str],
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=DEFAULT_RETRY_POLICY,
        )
