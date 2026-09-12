"""Demo driver: runs the full simulated pipeline (Phases 5-7) through a
real Temporal worker so the executions show up in the Temporal Web UI.

"Simulates the cloud portion" means every cloud adapter is constructed
with the same fakes the test suite uses (FakeTransport,
FakeNfs4AclApplier, a dict-keyed fake ACL reader) instead of real
boto3/azure-sdk/google-cloud-filestore clients -- no real cloud calls,
same real workflow/activity code path.

Run with:  uv run python scripts/demo_run.py
"""

from __future__ import annotations

import asyncio
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
# This is a one-shot local script, not a request-serving process --
# safe to use sync Django ORM calls from inside the asyncio loop that
# drives the Temporal client/worker.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")
django.setup()

from django.utils import timezone
from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from migration.cloud_adapters.aws import AWSAdapter
from migration.cloud_adapters.azure import AzureAdapter
from migration.cloud_adapters.gcp import GCPAdapter
from migration.cloud_adapters.testing import FakeNfs4AclApplier, FakeTransport
from migration.cutover.activities import CutoverActivities
from migration.cutover.workflows import (
    AutoFallbackEvaluationWorkflow,
    CutoverWorkflow,
    CutoverWorkflowInput,
)
from migration.discovery.testing import DictACLReader
from migration.discovery.types import ACE, FileACL
from migration.models import (
    CutoverConfig,
    CutoverMode,
    DestCloud,
    LagStabilityCheck,
    MigrationFileStatus,
    PermissionStatus,
    PolicyAction,
    ShadowSyncStatus,
    SignOffPolicy,
    TransferStatus,
)
from migration.orchestration.activities import MigrationActivities
from migration.orchestration.workflows import (
    ApplyPermissionsForBatch,
    ApplyPermissionsForSubtree,
    ApplyPermissionsForSubtreeInput,
)
from migration.permission_mapping.loader import load_mapping_table

TASK_QUEUE = "file-migration-demo"
SOURCE_ROOT = "/onprem/demo-share"
RUN_ID = uuid.uuid4().hex[:8]




def seed_permission_pipeline_fixture():
    print(f"[{RUN_ID}] Seeding MigrationFileStatus rows for all three clouds...")
    MigrationFileStatus.objects.filter(source_path__startswith=SOURCE_ROOT).delete()

    files = [
        ("finance/q3_report.xlsx", ("DOMAIN\\jane.doe", {"ReadData", "WriteData"})),
        ("finance/q3_summary.pdf", ("DOMAIN\\finance-team", {"ReadData"})),
        ("hr/policies.docx", ("DOMAIN\\hr-team", {"ReadData", "WriteData", "ChangePermissions"})),
    ]

    source_acls: dict[str, FileACL] = {}
    aws_dest_acls: dict[str, FileACL] = {}
    azure_dest_acls: dict[str, FileACL] = {}
    rows_by_cloud: dict[str, list[MigrationFileStatus]] = {c: [] for c in DestCloud.values}

    for rel_path, (identity, rights) in files:
        source_path = f"{SOURCE_ROOT}/{rel_path}"
        acl = FileACL(
            owner="DOMAIN\\admin",
            group=None,
            aces=(ACE(identity=identity, rights=frozenset(rights), allow=True, inherited=False),),
        )
        source_acls[source_path] = acl

        for cloud, dest_prefix, dest_acls in [
            (DestCloud.AWS, "/fsx/demo-share", aws_dest_acls),
            (DestCloud.AZURE, "/azfiles/demo-share", azure_dest_acls),
            (DestCloud.GCP, "/filestore/demo-share", None),
        ]:
            dest_path = f"{dest_prefix}/{rel_path}"
            row = MigrationFileStatus.objects.create(
                source_path=source_path,
                dest_cloud=cloud,
                dest_path=dest_path,
                transfer_status=TransferStatus.TRANSFERRED,
                permission_status=PermissionStatus.PENDING,
            )
            rows_by_cloud[cloud].append(row)
            if dest_acls is not None:
                # AWS/Azure: ACL carried over verbatim during transfer --
                # same ACE count as source, simulating a clean copy.
                dest_acls[dest_path] = acl

    return source_acls, aws_dest_acls, azure_dest_acls, rows_by_cloud


async def run_apply_permissions_for_all_clouds(client: Client, mapping_table_version: str):
    for cloud in DestCloud.values:
        workflow_id = f"demo-{RUN_ID}-apply-permissions-{cloud}"
        print(f"[{RUN_ID}] Starting ApplyPermissionsForSubtree for {cloud} ({workflow_id})...")
        result = await client.execute_workflow(
            ApplyPermissionsForSubtree.run,
            ApplyPermissionsForSubtreeInput(
                root_path=SOURCE_ROOT,
                dest_cloud=cloud,
                mapping_table_version=mapping_table_version,
                batch_size=10,
                batches_per_run=1,
            ),
            id=workflow_id,
            task_queue=TASK_QUEUE,
        )
        print(f"[{RUN_ID}]   -> {cloud} subtree workflow completed: {result}")


async def run_cutover_demo(client: Client):
    scope = f"{SOURCE_ROOT}/finance"

    # --- shadow_write: seed a stable shadow sync, expect completed ---
    CutoverConfig.objects.filter(scope=scope).delete()
    CutoverConfig.objects.create(
        scope=scope,
        mode=CutoverMode.SHADOW_WRITE,
        max_acceptable_lag_seconds=60,
        lag_stability_window_minutes=15,
        updated_by="demo-admin",
    )
    ShadowSyncStatus.objects.update_or_create(
        subtree_path=f"{scope}/q3_report.xlsx",
        defaults={
            "last_sync_completed_at": timezone.now(),
            "last_sync_lag_seconds": 5,
            "pending_change_count": 0,
            "consecutive_stable_checks": 10,
        },
    )
    for mismatch_type in ["missing_grant", "extra_grant", "inheritance_divergence", "unresolvable_identity"]:
        SignOffPolicy.objects.get_or_create(
            policy_version="v1",
            mismatch_type=mismatch_type,
            defaults={"action": PolicyAction.LOG_ONLY, "active": True, "updated_by": "demo-seed"},
        )

    workflow_id = f"demo-{RUN_ID}-cutover-shadow-write"
    print(f"[{RUN_ID}] Starting CutoverWorkflow (shadow_write) for {scope} ({workflow_id})...")
    result = await client.execute_workflow(
        CutoverWorkflow.run,
        CutoverWorkflowInput(scope=scope, mode=CutoverMode.SHADOW_WRITE),
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    print(f"[{RUN_ID}]   -> shadow_write cutover result: {result}")

    # --- hard_freeze: no shadow sync needed, expect completed ---
    hard_freeze_scope = f"{SOURCE_ROOT}/hr"
    workflow_id = f"demo-{RUN_ID}-cutover-hard-freeze"
    print(f"[{RUN_ID}] Starting CutoverWorkflow (hard_freeze) for {hard_freeze_scope} ({workflow_id})...")
    result = await client.execute_workflow(
        CutoverWorkflow.run,
        CutoverWorkflowInput(scope=hard_freeze_scope, mode=CutoverMode.HARD_FREEZE),
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    print(f"[{RUN_ID}]   -> hard_freeze cutover result: {result}")


async def run_auto_fallback_demo(client: Client):
    unstable_scope = f"{SOURCE_ROOT}/legacy-archive"
    CutoverConfig.objects.filter(scope=unstable_scope).delete()
    CutoverConfig.objects.create(
        scope=unstable_scope,
        mode=CutoverMode.SHADOW_WRITE,
        auto_fallback_enabled=True,
        updated_by="demo-admin",
    )
    for _ in range(5):
        LagStabilityCheck.objects.create(
            subtree_path=unstable_scope, lag_seconds=999, threshold_seconds=60, passed=False
        )

    workflow_id = f"demo-{RUN_ID}-auto-fallback-eval"
    print(f"[{RUN_ID}] Starting AutoFallbackEvaluationWorkflow ({workflow_id})...")
    result = await client.execute_workflow(
        AutoFallbackEvaluationWorkflow.run,
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    print(f"[{RUN_ID}]   -> auto-fallback triggered for scopes: {result}")


async def main():
    mapping_table = load_mapping_table()
    source_acls, aws_dest_acls, azure_dest_acls, _rows = seed_permission_pipeline_fixture()

    activities = MigrationActivities(
        aws_adapter=AWSAdapter(
            transport=FakeTransport(), dest_acl_reader=DictACLReader(aws_dest_acls)
        ),
        azure_adapter=AzureAdapter(
            transport=FakeTransport(), dest_acl_reader=DictACLReader(azure_dest_acls)
        ),
        gcp_adapter=GCPAdapter(
            transport=FakeTransport(), acl_applier=FakeNfs4AclApplier(), mapping_table=mapping_table
        ),
        source_acl_reader=DictACLReader(source_acls),
        mapping_table=mapping_table,
    )
    cutover_activities = CutoverActivities()

    client = await Client.connect("localhost:7233")

    async with Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[
            ApplyPermissionsForSubtree,
            ApplyPermissionsForBatch,
            CutoverWorkflow,
            AutoFallbackEvaluationWorkflow,
        ],
        activities=[
            activities.read_source_acl,
            activities.resolve_identity,
            activities.translate_ace_activity,
            activities.apply_acl,
            activities.verify_acl,
            activities.write_audit_record,
            activities.list_pending_file_ids,
            cutover_activities.check_cutover_readiness,
            cutover_activities.announce,
            cutover_activities.run_diff_gate,
            cutover_activities.start_cutover_run,
            cutover_activities.finish_cutover_run,
            cutover_activities.evaluate_auto_fallback_activity,
        ],
        activity_executor=ThreadPoolExecutor(max_workers=10),
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        print(f"[{RUN_ID}] Worker started on task queue {TASK_QUEUE!r}. Running demo workflows...\n")
        await run_apply_permissions_for_all_clouds(client, mapping_table.version)
        print()
        await run_cutover_demo(client)
        print()
        await run_auto_fallback_demo(client)

    print(f"\n[{RUN_ID}] Demo complete. Open http://localhost:8233 and filter by 'demo-{RUN_ID}' to inspect.")


if __name__ == "__main__":
    asyncio.run(main())
