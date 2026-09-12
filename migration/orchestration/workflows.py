"""Phase 5 workflows.

`ApplyPermissionsForSubtree` (parent) queries for files still needing
permission work under a subtree, fans out batches to
`ApplyPermissionsForBatch` (child) workflows, and uses continue-as-new
once a subtree has more candidates than fit in one run -- so a single
massive tree doesn't grow one workflow's history unboundedly.

Re-invocation after a mapping-table fix falls out of the same query
(`list_pending_file_ids`, see activities.py) rather than needing a
separate code path: starting a new `ApplyPermissionsForSubtree` run
with the bumped `mapping_table_version` naturally picks up only rows
that haven't succeeded under that version yet (pending, or failed under
an older version) -- already-verified files and already-transferred
bytes are untouched.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from migration.orchestration.activities import (
    ApplyACLInput,
    ApplyACLResult,
    ListPendingFileIdsInput,
    ReadSourceACLInput,
    ReadSourceACLResult,
    ResolveIdentityInput,
    ResolveIdentityResult,
    TranslateACEInput,
    TranslateACEResult,
    VerifyACLInput,
    VerifyACLResult,
    WriteAuditRecordInput,
)

ACTIVITY_TIMEOUT = timedelta(seconds=30)
DEFAULT_RETRY_POLICY = RetryPolicy(maximum_attempts=3)


@dataclass
class ApplyPermissionsForBatchInput:
    file_status_ids: list[int]
    dest_cloud: str
    mapping_table_version: str


@workflow.defn
class ApplyPermissionsForBatch:
    """Child workflow: iterates one batch, running the six per-file
    activities for each. A file that fails is left in `failed` state by
    `apply_acl`/`verify_acl` themselves and reported back, not retried
    within this run -- retries happen via a future `ApplyPermissionsForSubtree`
    invocation, per the brief's targeted-reprocessing model.
    """

    @workflow.run
    async def run(self, input: ApplyPermissionsForBatchInput) -> list[int]:
        failed_ids: list[int] = []
        for file_status_id in input.file_status_ids:
            try:
                await self._process_one(file_status_id, input.dest_cloud, input.mapping_table_version)
            except Exception:  # noqa: BLE001 -- one file's failure must not abort the batch
                workflow.logger.exception("file %s failed permission processing", file_status_id)
                failed_ids.append(file_status_id)
        return failed_ids

    async def _process_one(
        self, file_status_id: int, dest_cloud: str, mapping_table_version: str
    ) -> None:
        source = await workflow.execute_activity(
            "read_source_acl",
            ReadSourceACLInput(file_status_id),
            result_type=ReadSourceACLResult,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=DEFAULT_RETRY_POLICY,
        )
        resolved = await workflow.execute_activity(
            "resolve_identity",
            ResolveIdentityInput(source.aces),
            result_type=ResolveIdentityResult,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=DEFAULT_RETRY_POLICY,
        )
        translated = await workflow.execute_activity(
            "translate_ace_activity",
            TranslateACEInput(
                file_status_id=file_status_id,
                dest_cloud=dest_cloud,
                is_dir=source.is_dir,
                aces=resolved.resolvable_aces,
                mapping_table_version=mapping_table_version,
            ),
            result_type=TranslateACEResult,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=DEFAULT_RETRY_POLICY,
        )
        await workflow.execute_activity(
            "apply_acl",
            ApplyACLInput(
                file_status_id=file_status_id,
                dest_cloud=dest_cloud,
                dest_path=source.dest_path,
                is_dir=source.is_dir,
                translated=translated,
                mapping_table_version=mapping_table_version,
            ),
            result_type=ApplyACLResult,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=DEFAULT_RETRY_POLICY,
        )
        await workflow.execute_activity(
            "verify_acl",
            VerifyACLInput(
                file_status_id=file_status_id,
                dest_cloud=dest_cloud,
                source_path=source.source_path,
                dest_path=source.dest_path,
                is_dir=source.is_dir,
                aces=resolved.resolvable_aces,
            ),
            result_type=VerifyACLResult,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=DEFAULT_RETRY_POLICY,
        )
        await workflow.execute_activity(
            "write_audit_record",
            WriteAuditRecordInput(
                file_status_id=file_status_id, workflow_id=workflow.info().workflow_id
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=DEFAULT_RETRY_POLICY,
        )


@dataclass
class ApplyPermissionsForSubtreeInput:
    root_path: str
    dest_cloud: str
    mapping_table_version: str
    batch_size: int = 500
    batches_per_run: int = 5
    iteration: int = 0


@workflow.defn
class ApplyPermissionsForSubtree:
    """Parent workflow: one run processes up to
    `batch_size * batches_per_run` files, fanning them out to concurrent
    `ApplyPermissionsForBatch` children, then continues-as-new if the
    subtree has more candidates than that -- so a very large tree is
    processed as a bounded chain of runs rather than one workflow whose
    history grows without limit.
    """

    @workflow.run
    async def run(self, input: ApplyPermissionsForSubtreeInput) -> None:
        limit = input.batch_size * input.batches_per_run
        candidate_ids = await workflow.execute_activity(
            "list_pending_file_ids",
            ListPendingFileIdsInput(
                root_path=input.root_path,
                dest_cloud=input.dest_cloud,
                mapping_table_version=input.mapping_table_version,
                limit=limit + 1,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=DEFAULT_RETRY_POLICY,
        )

        more_remaining = len(candidate_ids) > limit
        ids_to_process = candidate_ids[:limit]

        batches = [
            ids_to_process[i : i + input.batch_size]
            for i in range(0, len(ids_to_process), input.batch_size)
        ]

        await asyncio.gather(
            *[
                workflow.execute_child_workflow(
                    ApplyPermissionsForBatch.run,
                    ApplyPermissionsForBatchInput(
                        file_status_ids=batch,
                        dest_cloud=input.dest_cloud,
                        mapping_table_version=input.mapping_table_version,
                    ),
                    id=f"{workflow.info().workflow_id}-iter{input.iteration}-batch{i}",
                )
                for i, batch in enumerate(batches)
            ]
        )

        if more_remaining:
            workflow.continue_as_new(replace(input, iteration=input.iteration + 1))
