"""Cutover activities (Phase 7).

Every step past the lag-stability check is a real action against the
source/destination in production (flip to read-only, repoint DNS/mount,
etc.) with nothing to call from this environment -- each is a
constructor-injected `Announcer` callback (defaulting to a no-op) so
the workflow logic and its sequencing are real and tested, while the
side effect itself is swappable, same DI shape as every other phase.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from django.utils import timezone
from temporalio import activity

from migration.cutover.config_resolution import resolve_cutover_config
from migration.cutover.fallback import evaluate_auto_fallback
from migration.cutover.lag_stability import is_scope_lag_stable
from migration.diffing.gate import is_subtree_blocked
from migration.diffing.run_diff import diff_subtree
from migration.discovery.acl_reader import ACLReader
from migration.models import CutoverMode, CutoverRun
from migration.security.paging import page_immediately
from migration.temporal_utils import release_db_connection


class Announcer(Protocol):
    def __call__(self, scope: str, action: str) -> None: ...


def _noop_announcer(scope: str, action: str) -> None:
    return None


@dataclass
class CheckCutoverReadinessInput:
    scope: str
    mode: str


@dataclass
class CheckCutoverReadinessResult:
    ready: bool
    reason: str | None


@dataclass
class ScopeActionInput:
    scope: str
    action: str


@dataclass
class RunDiffGateInput:
    scope: str


@dataclass
class RunDiffGateResult:
    blocked: bool
    diff_count: int


@dataclass
class StartCutoverRunInput:
    scope: str
    mode: str
    workflow_id: str


@dataclass
class FinishCutoverRunInput:
    run_id: int
    status: str
    blocked_reason: str | None = None


class CutoverActivities:
    def __init__(
        self,
        announcer: Announcer = _noop_announcer,
        source_acl_reader: ACLReader | None = None,
        dest_acl_reader: ACLReader | None = None,
        gcp_acl_applier=None,
        diff_dest_cloud: str | None = None,
        notification_channel=None,
    ):
        self.announcer = announcer
        self.source_acl_reader = source_acl_reader
        self.dest_acl_reader = dest_acl_reader
        self.gcp_acl_applier = gcp_acl_applier
        self.diff_dest_cloud = diff_dest_cloud
        self.notification_channel = notification_channel

    @activity.defn
    @release_db_connection
    def check_cutover_readiness(
        self, input: CheckCutoverReadinessInput
    ) -> CheckCutoverReadinessResult:
        if input.mode == CutoverMode.HARD_FREEZE:
            # hard_freeze doesn't wait on shadow-sync lag at all -- that's
            # the whole point of the mode.
            return CheckCutoverReadinessResult(ready=True, reason=None)

        config = resolve_cutover_config(input.scope)
        if config is None:
            return CheckCutoverReadinessResult(
                ready=False, reason=f"no CutoverConfig governs scope {input.scope!r}"
            )
        if not is_scope_lag_stable(input.scope, config):
            return CheckCutoverReadinessResult(
                ready=False, reason="shadow sync lag not yet stable within threshold"
            )
        return CheckCutoverReadinessResult(ready=True, reason=None)

    @activity.defn
    @release_db_connection
    def announce(self, input: ScopeActionInput) -> None:
        self.announcer(input.scope, input.action)

    @activity.defn
    @release_db_connection
    def run_diff_gate(self, input: RunDiffGateInput) -> RunDiffGateResult:
        if self.diff_dest_cloud is not None:
            diff_subtree(
                input.scope,
                self.diff_dest_cloud,
                self.source_acl_reader,
                self.dest_acl_reader,
                self.gcp_acl_applier,
            )
        blocked = is_subtree_blocked(input.scope)
        return RunDiffGateResult(blocked=blocked, diff_count=0)

    @activity.defn
    @release_db_connection
    def start_cutover_run(self, input: StartCutoverRunInput) -> int:
        run = CutoverRun.objects.create(
            scope=input.scope, mode=input.mode, workflow_id=input.workflow_id
        )
        return run.id

    @activity.defn
    @release_db_connection
    def finish_cutover_run(self, input: FinishCutoverRunInput) -> None:
        CutoverRun.objects.filter(id=input.run_id).update(
            status=input.status,
            blocked_reason=input.blocked_reason,
            completed_at=timezone.now(),
        )

    @activity.defn
    @release_db_connection
    def evaluate_auto_fallback_activity(self) -> list[str]:
        new_configs = evaluate_auto_fallback()
        for config in new_configs:
            page_immediately(
                "auto_fallback_triggered",
                f"scope {config.scope} switched to hard_freeze automatically: "
                f"{config.fallback_reason}",
                self.notification_channel,
            )
        return [c.scope for c in new_configs]
