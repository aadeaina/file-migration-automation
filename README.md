# file-migration-automation

On-prem-to-multicloud file & permission migration automation: migrates
files and NTFS folder/file ACLs from an on-prem Windows file server to
AWS FSx for Windows File Server, Azure Files (AD-joined), and GCP
Filestore (NFSv4.1) simultaneously, preserving permission fidelity to
the extent each destination's model allows.

Implements all 9 phases of the project brief.

## Setup

```bash
docker compose up -d          # local Postgres on :5433
uv run python manage.py migrate
uv run pytest -q
```

`.env` holds local DB connection settings (gitignored).

To also run the Phase 5 workflow (needs the Temporal CLI: `brew install
temporal`):

```bash
temporal server start-dev --db-filename /tmp/temporal-file-migration.db
uv run python -m migration.orchestration.worker      # in another shell
uv run pytest -m temporal_integration -q             # runs the deliverable test
```

## What's built

### Phase 1 — Data model (`migration/models.py`)

The Django models from the design doc, plus explicit `db_table` names
on every managed model so the hand-written `RunSQL` views in
`migrations/0002_create_reporting_views.py` (which hardcode table/column
names like `migration_file_status`) resolve against what Django
actually creates. One addition beyond the original schema:
`MappingReviewQueueEntry` (Phase 3's review queue, see below).

### Phase 2 — Discovery (`migration/discovery/`)

- `types.py` — `ACE`, `FileACL`, `ManifestEntry`.
- `acl_reader.py` — `IcaclsACLReader` parses real `icacls` output;
  `is_orphaned_identity` flags SIDs `icacls` couldn't resolve to a name.
- `crawler.py` — `walk_tree(root, acl_reader)` recursively crawls,
  flagging `orphaned_sid`, `explicit_deny_ace`, and `broken_inheritance`
  (a deliberately simplified one-ancestor-chain inheritance model, not
  a full NTFS propagation engine — see the module docstring).
- `manifest.py` — manifest is JSON (doubles as the raw-ACL store,
  keyed by path) and `load_manifest_into_db` fans each file out to a
  `MigrationFileStatus` row per destination cloud.
- `testing.py` / `FakeACLReader` — lets tests build a real directory
  tree and inject ACLs without needing actual NTFS (macOS/Linux dev
  boxes don't have it).

### Phase 3 — NTFS → NFSv4 mapping (`migration/permission_mapping/`)

- `data/ntfs_to_nfsv4_v1.json` — the versioned, editable mapping table
  (every NTFS right from the brief; `ReadExtendedAttributes` /
  `WriteExtendedAttributes` marked `partial`, `TakeOwnership` marked
  `policy_decision`).
- `translate.py` — `translate_ace(ntfs_ace, object_type, mapping_table)`,
  a pure function (no I/O, no DB) — see its unit tests for every row.
- `review_queue.py` — the one place with I/O: routes any non-`exact`
  confidence mapping to `MappingReviewQueueEntry`, deduped per
  `(ntfs_right, object_type, mapping_table_version)` so it's only
  queued once per pattern.

### Phase 4 — Cloud adapters (`migration/cloud_adapters/`)

- `base.py` — the `CloudAdapter` protocol (`transfer` /
  `apply_permissions` / `verify`).
- `aws.py`, `azure.py` — AD trust means ACLs carry over verbatim, so
  `apply_permissions`/`verify` just re-read the destination ACL and
  compare ACE counts (`verbatim_acl.py`). Byte copy via `Robocopy`
  (`/SEC`) for AWS, `AzCopy` for Azure, both behind a swappable
  `Transport`.
- `gcp.py` — the adapter with real engineering effort per the brief:
  byte copy, then per-ACE `translate_ace` + apply via `nfs4_setfacl`
  (`nfs4_acl.py`), with review-queue routing for non-exact mappings.
- `runner.py` — maps each adapter's `TransferResult` /
  `PermissionResult` / `VerifyResult` back onto `MigrationFileStatus`
  rows; this is the piece Phase 5's Temporal activities will call.

**Design note — no live cloud accounts in this environment.** Every
adapter takes its `Transport` / ACL reader / ACL applier as a
constructor argument, defaulting to the real implementation (shells
out to `robocopy`/`azcopy`/`nfs4_setfacl`, or a plain byte copy for the
GCP transport backed by Storage Transfer Service in production) but
swappable for tests. Tests exercise the adapters' actual logic — batching,
error handling, count bookkeeping, ACE translation, review-queue
dedup — against fakes, per `test_*.py` in `cloud_adapters/`. `boto3`,
`azure-sdk-for-python`, and `google-cloud-filestore` aren't wired in yet
since no sandbox accounts are configured; swapping the CLI-based
transports for direct SDK calls is a drop-in change behind the same
`Transport` protocol once accounts exist.

### Phase 5 — Temporal orchestration (`migration/orchestration/`)

`nats-infra` (a separate broker/worker/scheduler package) was
considered and deliberately not used — see the design discussion for
this project: the brief's requirements (parent/child workflow fan-out
with continue-as-new, targeted re-invocation of only failed files after
a mapping-table fix, and "no secret ever appears in workflow history")
depend on Temporal's durable-execution/replay model, which `nats-infra`
doesn't provide.

- `workflows.py` — `ApplyPermissionsForSubtree` (parent) queries for
  files still needing permission work under a subtree via
  `list_pending_file_ids`, fans batches out to concurrent
  `ApplyPermissionsForBatch` (child) workflows, and `continue_as_new`s
  once a subtree has more candidates than fit in one run.
- `activities.py` — the six per-file activities from the brief:
  `ReadSourceACL`, `ResolveIdentity`, `TranslateACE`, `ApplyACL`,
  `VerifyACL`, `WriteAuditRecord`, as bound methods on
  `MigrationActivities` (constructor-injected adapters/readers, same DI
  shape as Phase 4). `ApplyACL` is idempotent by construction — for
  GCP it always `set_acl`s the complete target ACE list (never
  appends); for AWS/Azure it re-delegates to the adapter's own
  verify-only `apply_permissions`.
- **Targeted re-invocation** falls out of one query rather than a
  separate code path: `list_pending_file_ids` returns anything *not
  yet `applied`/`verified` under the current `mapping_table_version`*,
  which covers a first run (`pending`) and a post-mapping-fix re-run
  (`failed` under an old version) identically. Since the workflow never
  calls `transfer()`, a re-run can never re-copy bytes.
- `dto.py` — plain-list mirrors of the `frozenset`-based internal types,
  needed because Temporal's default JSON data converter can't
  serialize `frozenset`.
- `worker.py` — the worker entrypoint (`UnsandboxedWorkflowRunner`,
  since activities go through the Django ORM).
- `test_workflow_integration.py` — the Phase 5 deliverable: runs a real
  local worker against a fixture, verifies pending → applied →
  verified, then simulates a mapping-table fix and re-run, confirming
  only the affected file is reprocessed and `transfer_status` is
  untouched. Marked `temporal_integration` and excluded from the
  default `pytest -q` run since it needs a live Temporal server.

### Phase 6 — Effective-permission diffing (`migration/diffing/`)

Decoupled from the transfer/permission workflows on purpose — a plain,
idempotent batch function (not a Temporal workflow), runnable
independently against any subtree, per the brief.

- `effective_access.py` — reduces a raw ACE/ACE4 list into *effective
  access categories* (read/write/execute/delete/change_permissions)
  per identity, applying deny precedence (a deny always wins over an
  allow for the same category) rather than doing a raw ACE comparison.
  Also tracks whether any contributing grant was inherited vs explicit
  — the basis for `inheritance_divergence` — though this is only
  meaningful on the NTFS side; NFSv4 ACE4 round-tripped through
  `nfs4_setfacl`/`nfs4_getfacl` doesn't carry that bit in our model.
- `diff.py` — pure `diff_effective_access(source, dest, orphaned)`,
  producing `missing_grant` / `extra_grant` / `inheritance_divergence` /
  `unresolvable_identity` rows with severity (`extra_grant` is
  `critical` — a security regression; the rest are `warning`/`info`).
- `run_diff.py` — the I/O wrapper: reads source/dest ACLs, calls the
  pure diff function, writes `EffectivePermissionDiff` rows. Reruns are
  idempotent — a file's *unreviewed* diffs are recomputed each time,
  but a diff that already carries a review decision (e.g. an approved
  `DiffException`) is left alone as long as the recomputed state
  matches what was reviewed.
- `gate.py` — the sign-off gate is a query against the `SignOffGate`
  view (Phase 1), never a reimplementation of its policy logic in
  Python, per the brief.

### Phase 7 — Cutover (`migration/cutover/`)

- `config_resolution.py` — `resolve_cutover_config(subtree_path)`: the
  most specific active `CutoverConfig` scope wins (`global` is the
  fallback), since a subtree path isn't a foreign key to a config row.
- `lag_stability.py` — whether a scope's shadow sync is stable enough
  to cut over: lag at/below threshold, held for the configured window
  (translated into "how many consecutive checks" via a documented
  assumed check cadence, since `ShadowSyncStatus` doesn't itself store
  the check interval).
- `fallback.py` — both fallback paths read the same
  `SubtreesNeedingFallbackReview` view; `list_subtrees_needing_review`
  is the manual path (surface only), `evaluate_auto_fallback` is the
  opt-in automatic path, guarded against duplicate inserts for a
  subtree already on `hard_freeze`. Config rows are deactivated
  (`active=False`), never deleted or mutated otherwise — append-only,
  per the design doc.
- `workflows.py` / `activities.py` — `CutoverWorkflow(scope, mode)`
  covers both `shadow_write` (readiness check → final delta →
  read-only → catch-up → repoint) and `hard_freeze` (announce →
  final sync → repoint), converging on Phase 6's diff gate before
  marking a `CutoverRun` `completed` or `blocked`.
  `AutoFallbackEvaluationWorkflow` wraps `evaluate_auto_fallback` for
  scheduled/triggered execution. Every action past the readiness check
  (flip read-only, repoint clients, etc.) is a constructor-injected
  `Announcer` callback — there's no real file server here to act on,
  same DI shape as every other phase's untestable-in-this-environment
  side effects.
- `test_workflow_integration.py` — the Phase 7 deliverable: a real
  local worker exercising both modes (including an aborted run when
  lag isn't stable, and a blocked run when the sign-off gate has open
  entries) and both fallback paths, with `ShadowSyncStatus`/
  `LagStabilityCheck` rows seeded directly standing in for the
  mock USN Journal events / `ContinuousShadowSyncWorkflow` output.

### Phase 8 — Security (`migration/security/`)

Secrets manager: a single HashiCorp Vault instance across all three
clouds (the user's choice, over three separate per-cloud native
managers) — one client, one interface, consistent with this
environment having no real per-cloud secrets manager access anyway.

- `secrets.py` — `SecretsClient` protocol; `VaultSecretsClient` (real,
  reads `VAULT_ADDR`/`VAULT_TOKEN`); `NullSecretsClient` (the default —
  fetches nothing, since our transports shell out to CLI tools assumed
  to run under an already-authenticated host identity in this
  environment; explicit rather than a silent Vault stand-in).
- `credentials.py` — **the credential-fetch pattern**: one Vault path
  and one scoped service identity per cloud (`migration/aws` /
  `svc-migration-aws`, etc.) — no shared "migration" credential across
  clouds. `fetch_scoped_credential` is called fresh inside the activity
  that needs it (wired into `apply_acl`'s GCP branch, the actual
  ACL-write call site) and the resulting `ScopedCredential` never
  appears in any dataclass that crosses the Temporal activity boundary
  — that boundary is exactly what's recorded in workflow history, so
  this is what makes "no secret in history" true by construction.
- `audit_log.py` — logs every ACL-write call with the service identity,
  file path, and result, distinct from `MigrationFileStatus`'s own
  file-level state tracking.
- `paging.py` — the page-immediately hook for authentication failures
  (`AuthenticationError`, `page_immediately`); Phase 9 wires this to a
  real notification channel.
- `test_workflow_integration.py` — the Phase 8 deliverable: runs the
  real Phase 5 workflow with a `FakeSecretsClient` returning an
  identifiable fake secret, then pulls the *entire* recorded history
  (parent **and** child batch workflow — the six per-file activities,
  `apply_acl` included, run inside the child) and asserts the secret
  string never appears in the raw bytes. Verified genuine, not vacuous,
  by deliberately leaking the credential into `ApplyACLResult` during
  development and confirming the test failed, then reverting.

### Phase 9 — Monitoring, alerting, runbook

Notification channel: a single Slack incoming webhook (the user's
choice, over email or PagerDuty) — `SLACK_WEBHOOK_URL`, see
`migration/monitoring/channels.py`.

- `page_immediately.py` — two of the four page-immediately conditions
  are poll-based scans over existing tables (`check_for_new_extra_grants`
  over `EffectivePermissionDiff`, `check_for_stuck_retries` over
  `MigrationFileStatus.retry_count`, now actually incremented on an
  `apply_acl` failure), deduplicated via a new `AlertLog` table so the
  same row never re-pages. The other two — an auto-fallback mode change,
  a cloud auth failure — are event-driven and page at their own call
  site (`migration/cutover/activities.py`, `migration/orchestration/activities.py`)
  rather than being polled, since they're already known the instant
  they happen.
- `daily_digest.py` — `missing_grant`/`inheritance_divergence` counts,
  files stuck in `failed` past an age threshold, and unactioned
  `SubtreesNeedingFallbackReview` entries, all read straight off
  existing tables.
- `dashboard.py` — throughput, completion percentage per cloud (via
  `MigrationHealthOverview`), and mapping-table confidence distribution
  — queries only, no alerting.
- `channels.py` — `SlackWebhookChannel` (real, plain `urllib` POST —
  no extra dependency) and `NullChannel` (the default; alerts still
  log at CRITICAL via `security/paging.py` even with no channel wired
  up).
- `test_end_to_end_alert.py` — the Phase 9 deliverable: seeds a real
  `extra_grant` via Phase 6's actual `diff_file` (not a hand-built row),
  runs the page-immediately check through a real `SlackWebhookChannel`
  pointed at a local HTTP server standing in for Slack, and confirms
  the POST actually arrives with the right content — verified genuine
  by temporarily breaking the check's query and confirming the test
  fails, then reverting.
- [`RUNBOOK.md`](RUNBOOK.md) — organized by symptom (sync lag not
  converging, sign-off gate stuck, post-cutover write failures,
  auto-fallback fired), each with the specific table/view to query
  first, plus a full alert-tier reference table.

## Tests

```bash
uv run pytest -q                          # 119 passed (fast, no external deps)
uv run pytest -m temporal_integration -q  # needs `temporal server start-dev` (8 tests)
uvx ruff check migration
```
