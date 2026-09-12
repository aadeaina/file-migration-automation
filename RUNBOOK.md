# Migration runbook

Organized by symptom. Each entry names the table/view to query first,
then what it usually means and what to do about it.

## Sync lag not converging

**Symptom**: `shadow_write` cutover keeps aborting with "shadow sync
lag not yet stable", or the daily digest keeps listing the same
subtree under "needing fallback review."

**Query first**:

```sql
SELECT * FROM shadow_sync_status WHERE subtree_path LIKE '<scope>%';
SELECT * FROM lag_stability_check
WHERE subtree_path LIKE '<scope>%'
ORDER BY checked_at DESC LIMIT 20;
```

- `shadow_sync_status.last_sync_lag_seconds` vs. the governing
  `cutover_config.max_acceptable_lag_seconds` for that scope
  (`migration/cutover/config_resolution.py` resolves which config row
  actually governs a given subtree — most specific scope wins).
- `shadow_sync_status.consecutive_stable_checks` vs. the required count
  derived from `cutover_config.lag_stability_window_minutes`
  (`migration/cutover/lag_stability.py:required_consecutive_checks` —
  assumes a 5-minute check cadence; if `ContinuousShadowSyncWorkflow`'s
  actual interval differs, that assumption needs updating there).

**What it usually means**: either the source is generating changes
faster than the shadow sync can apply them (check
`pending_change_count`), or the sync workflow itself is stuck/erroring
(check its own Temporal execution).

**Fix**: address the underlying sync throughput/error first. If the
subtree has been unstable for a while and needs to move forward
anyway, see "auto-fallback fired" below, or manually insert a
`hard_freeze` `cutover_config` row (`insert_hard_freeze_config` in
`migration/cutover/fallback.py`) to skip the shadow-sync path
entirely for that scope.

## Sign-off gate stuck

**Symptom**: `CutoverWorkflow` completes every other step but returns
`status="blocked"`, or `sign_off_gate` keeps showing entries that never
clear.

**Query first**:

```sql
SELECT * FROM sign_off_gate WHERE file_id IN (
    SELECT id FROM migration_file_status WHERE source_path LIKE '<scope>%'
);
```

- Per row: `mismatch_type` and `action` tell you *why* it's blocking
  (`action='block'` always blocks; `action='require_exception'` blocks
  only until a `diff_exception` row exists for that `effective_permission_diff`).
- Cross-reference `effective_permission_diff` for the full detail
  (`source_access`/`dest_access`/`severity`) and `sign_off_policy` for
  the active policy per `mismatch_type` (`active=true`).

**What it usually means**: either a real permission mismatch needs
fixing (re-run the affected file(s) through `ApplyPermissionsForSubtree`
after correcting the mapping table, if it's a translation issue — see
Phase 3's mapping table review queue, `mapping_review_queue_entry`),
or the mismatch is expected/acceptable and needs a reviewed exception
(`diff_exception`, with `approved_by` and `reason`) rather than a
policy change.

**Fix**: never edit `effective_permission_diff.reviewed` directly to
force a row through — insert a `diff_exception` for `require_exception`
rows, or fix the underlying permission and re-run
`migration.diffing.run_diff.diff_file` for that file (unreviewed diffs
are recomputed automatically; already-reviewed ones are left alone as
long as nothing's changed since review).

## Post-cutover write failures

**Symptom**: clients report write failures against the destination
after a `CutoverWorkflow` reported `completed`.

**Query first**:

```sql
SELECT * FROM cutover_run WHERE scope = '<scope>' ORDER BY started_at DESC LIMIT 5;
SELECT * FROM cutover_config WHERE scope = '<scope>' AND active = true;
```

- Confirm the `cutover_run` actually reached `completed` (not
  `blocked`/`aborted` — clients shouldn't have been repointed if it
  didn't) and check `workflow_id` to pull the full Temporal execution
  history for that run.
- Check the acl_write audit log (`migration.security.acl_write_audit`
  logger) for the affected path/identity — was the destination ACL
  actually applied correctly, or did `apply_acl` report
  `unmapped_rights` that got silently accepted?

**What it usually means**: a permission that looked fine on the source
didn't survive translation (GCP) or wasn't actually verbatim (AWS/Azure)
— check `migration_file_status.ace_diff_detail` for that file, and
`effective_permission_diff` for anything that should have blocked the
gate but didn't (a policy set to `log_only` won't block, by design —
confirm that's intentional for the mismatch type involved).

**Fix**: this is a data-integrity issue, not a config one — don't
re-run cutover. Diagnose via `diff_file` for the specific file(s)
affected, fix the mapping table or re-apply permissions
(`ApplyPermissionsForSubtree` scoped to that file), then re-verify.

## Auto-fallback fired

**Symptom**: a `page_immediately` alert titled `auto_fallback_triggered`
fired, or a subtree unexpectedly switched to `hard_freeze`.

**Query first**:

```sql
SELECT * FROM cutover_config WHERE scope = '<scope>' ORDER BY updated_at DESC;
SELECT * FROM subtrees_needing_fallback_review WHERE subtree_path = '<scope>';
```

- The new `cutover_config` row's `updated_by` will read
  `system:auto-fallback-workflow` and `fallback_reason` will cite the
  failure count that triggered it (`migration/cutover/fallback.py:evaluate_auto_fallback`).
  The previous config row for that exact scope is deactivated
  (`active=false`), not deleted — its history is still queryable.
- Confirm this scope genuinely had `auto_fallback_enabled=true` set
  beforehand (if not, something inserted that flag without
  authorization — treat as a config-integrity incident, not a sync
  issue).

**What it usually means**: the same lag instability as "sync lag not
converging" above, but for a scope that opted into automatic handling
instead of waiting for a human.

**Fix**: this is *not* an error to undo reflexively — hard_freeze is a
safe, working state. Investigate the underlying lag instability (see
above) before considering a manual switch back to `shadow_write` for
that scope, and only do so once the root cause is fixed.

---

## Alert reference

| Tier | Condition | Source |
|---|---|---|
| Page immediately | New `extra_grant` row | `effective_permission_diff` (`migration.monitoring.page_immediately.check_for_new_extra_grants`) |
| Page immediately | File stuck retrying past threshold | `migration_file_status.retry_count` (`check_for_stuck_retries`) |
| Page immediately | Auto-fallback mode change | fired at the moment `evaluate_auto_fallback` inserts a row (`migration/cutover/activities.py`) |
| Page immediately | Cloud API auth failure | fired at the moment `fetch_scoped_credential` fails (`migration/orchestration/activities.py`) |
| Daily digest | `missing_grant`/`inheritance_divergence` counts, stuck-failed files, unactioned fallback subtrees | `migration.monitoring.daily_digest.build_daily_digest` |
| Dashboard only | Throughput, completion % per cloud, mapping confidence distribution | `migration.monitoring.dashboard` |

Dedup: with Alertmanager configured (`ALERTMANAGER_URL`), it owns
dedup/grouping/silencing/auto-resolve by label fingerprint — page
checks simply re-fire every currently-open issue each run. Without it,
`alert_log` is the fallback (a plain Slack webhook has no native
dedup), gating so the same underlying row only pages once. See
`migration/monitoring/alertmanager.py` and `channels.py`.
