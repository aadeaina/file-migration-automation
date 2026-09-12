"""
Django models for the on-prem-to-multicloud migration automation.

Assumes a PostgreSQL backend (ArrayField and JSONField rely on it).

Views referenced in the design (permission_migration_summary,
sign_off_gate, subtrees_needing_fallback_review,
migration_health_overview) are modeled below as unmanaged models
mapped to database views, created via migrations.RunSQL in
0002_create_reporting_views.py.

Every managed model below sets an explicit snake_case `db_table` so
the raw SQL in that views migration (which hardcodes table/column
names like `migration_file_status`, `effective_permission_diff`)
resolves against the tables Django actually creates, rather than
Django's default `<app_label>_<modelnamelowercase>` naming.
"""

from django.contrib.postgres.fields import ArrayField
from django.db import models


class DestCloud(models.TextChoices):
    AWS = "aws", "AWS"
    AZURE = "azure", "Azure"
    GCP = "gcp", "GCP"


class TransferStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    TRANSFERRING = "transferring", "Transferring"
    TRANSFERRED = "transferred", "Transferred"
    TRANSFER_FAILED = "transfer_failed", "Transfer failed"


class PermissionStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPLYING = "applying", "Applying"
    APPLIED = "applied", "Applied"
    VERIFIED = "verified", "Verified"
    MAPPING_REVIEW_NEEDED = "mapping_review_needed", "Mapping review needed"
    FAILED = "failed", "Failed"


class MismatchType(models.TextChoices):
    MISSING_GRANT = "missing_grant", "Missing grant"
    EXTRA_GRANT = "extra_grant", "Extra grant"
    INHERITANCE_DIVERGENCE = "inheritance_divergence", "Inheritance divergence"
    UNRESOLVABLE_IDENTITY = "unresolvable_identity", "Unresolvable identity"


class DiffSeverity(models.TextChoices):
    CRITICAL = "critical", "Critical"
    WARNING = "warning", "Warning"
    INFO = "info", "Info"


class PolicyAction(models.TextChoices):
    BLOCK = "block", "Block"
    REQUIRE_EXCEPTION = "require_exception", "Require exception"
    LOG_ONLY = "log_only", "Log only"


class CutoverMode(models.TextChoices):
    SHADOW_WRITE = "shadow_write", "Shadow write"
    HARD_FREEZE = "hard_freeze", "Hard freeze"


class MigrationFileStatus(models.Model):
    """Per-file tracking across the transfer and permission pipelines."""

    source_path = models.TextField()
    dest_cloud = models.CharField(max_length=10, choices=DestCloud.choices)
    dest_path = models.TextField()

    transfer_status = models.CharField(
        max_length=20,
        choices=TransferStatus.choices,
        default=TransferStatus.QUEUED,
    )
    transfer_checksum_match = models.BooleanField(null=True, blank=True)
    transferred_at = models.DateTimeField(null=True, blank=True)

    permission_status = models.CharField(
        max_length=30,
        choices=PermissionStatus.choices,
        default=PermissionStatus.PENDING,
    )
    permission_applied_at = models.DateTimeField(null=True, blank=True)
    permission_verified_at = models.DateTimeField(null=True, blank=True)
    mapping_table_version = models.CharField(max_length=20, null=True, blank=True)

    ace_count_source = models.IntegerField(null=True, blank=True)
    ace_count_dest = models.IntegerField(null=True, blank=True)
    ace_diff_detail = models.JSONField(null=True, blank=True)

    error_message = models.TextField(null=True, blank=True)
    retry_count = models.PositiveIntegerField(default=0)

    workflow_id = models.CharField(max_length=255, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "migration_file_status"
        indexes = [
            models.Index(fields=["dest_cloud", "permission_status"]),
            models.Index(fields=["source_path"]),
        ]

    def __str__(self):
        return f"{self.source_path} -> {self.dest_cloud} ({self.permission_status})"


class EffectivePermissionDiff(models.Model):
    """Per-identity effective-access comparison between source and destination."""

    file = models.ForeignKey(
        MigrationFileStatus,
        on_delete=models.CASCADE,
        related_name="permission_diffs",
    )
    identity = models.CharField(max_length=255)
    source_access = ArrayField(models.CharField(max_length=50), default=list)
    dest_access = ArrayField(models.CharField(max_length=50), default=list)
    mismatch_type = models.CharField(max_length=30, choices=MismatchType.choices)
    severity = models.CharField(max_length=10, choices=DiffSeverity.choices)
    reviewed = models.BooleanField(default=False)
    resolution_note = models.TextField(null=True, blank=True)
    # Which sign-off policy version was active when this row was evaluated,
    # so the bar moving later doesn't retroactively change what a past
    # pass/fail meant.
    policy_version_at_eval = models.CharField(max_length=20, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "effective_permission_diff"

    def __str__(self):
        return f"{self.file_id}:{self.identity} [{self.mismatch_type}]"


class DiffException(models.Model):
    """A reviewed, approved exception for one specific diff mismatch."""

    diff = models.ForeignKey(
        EffectivePermissionDiff,
        on_delete=models.CASCADE,
        related_name="exceptions",
    )
    approved_by = models.CharField(max_length=255)
    reason = models.TextField()
    approved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "diff_exception"

    def __str__(self):
        return f"Exception for diff {self.diff_id} by {self.approved_by}"


class SignOffPolicy(models.Model):
    """Versioned, editable policy for what blocks stage promotion."""

    policy_version = models.CharField(max_length=20)
    mismatch_type = models.CharField(max_length=30, choices=MismatchType.choices)
    action = models.CharField(max_length=20, choices=PolicyAction.choices)
    active = models.BooleanField(default=True)
    updated_by = models.CharField(max_length=255)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "sign_off_policy"
        indexes = [models.Index(fields=["mismatch_type", "active"])]

    def __str__(self):
        return f"{self.policy_version}: {self.mismatch_type} -> {self.action}"


class CutoverConfig(models.Model):
    """Cutover mode and thresholds, scoped globally or per subtree."""

    scope = models.CharField(max_length=1024)  # 'global' or a specific subtree path
    mode = models.CharField(
        max_length=20, choices=CutoverMode.choices, default=CutoverMode.SHADOW_WRITE
    )
    max_acceptable_lag_seconds = models.PositiveIntegerField(default=300)
    lag_stability_window_minutes = models.PositiveIntegerField(default=30)

    auto_fallback_enabled = models.BooleanField(default=False)
    auto_fallback_failure_threshold = models.PositiveIntegerField(default=5)
    auto_fallback_window_hours = models.PositiveIntegerField(default=24)

    active = models.BooleanField(default=True)
    fallback_reason = models.TextField(null=True, blank=True)
    updated_by = models.CharField(max_length=255)  # username, or 'system:auto-fallback-workflow'
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "cutover_config"
        indexes = [models.Index(fields=["scope", "active"])]

    def __str__(self):
        return f"{self.scope} -> {self.mode}"


class ShadowSyncStatus(models.Model):
    """Latest sync-lag snapshot per subtree, used to gate cutover."""

    subtree_path = models.CharField(max_length=1024, primary_key=True)
    last_sync_completed_at = models.DateTimeField()
    last_sync_lag_seconds = models.PositiveIntegerField()
    pending_change_count = models.PositiveIntegerField()
    consecutive_stable_checks = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "shadow_sync_status"

    def __str__(self):
        return f"{self.subtree_path} (lag={self.last_sync_lag_seconds}s)"


class LagStabilityCheck(models.Model):
    """History of individual lag checks, feeding the fallback-review query."""

    subtree_path = models.CharField(max_length=1024)
    checked_at = models.DateTimeField(auto_now_add=True)
    lag_seconds = models.PositiveIntegerField()
    threshold_seconds = models.PositiveIntegerField()
    passed = models.BooleanField()

    class Meta:
        db_table = "lag_stability_check"
        indexes = [models.Index(fields=["subtree_path", "checked_at"])]

    def __str__(self):
        status = "pass" if self.passed else "fail"
        return f"{self.subtree_path} @ {self.checked_at}: {status}"


# ---------------------------------------------------------------------------
# Unmanaged models mapped to database views.
# Created via migrations.RunSQL in 0002_create_reporting_views.py.
# ---------------------------------------------------------------------------


class PermissionMigrationSummary(models.Model):
    dest_cloud = models.CharField(max_length=10)
    permission_status = models.CharField(max_length=30)
    mapping_table_version = models.CharField(max_length=20, null=True)
    count = models.IntegerField()

    class Meta:
        managed = False
        db_table = "permission_migration_summary"


class SignOffGate(models.Model):
    file = models.ForeignKey(
        MigrationFileStatus, on_delete=models.DO_NOTHING, db_column="file_id"
    )
    identity = models.CharField(max_length=255)
    mismatch_type = models.CharField(max_length=30)
    action = models.CharField(max_length=20)

    class Meta:
        managed = False
        db_table = "sign_off_gate"


class SubtreesNeedingFallbackReview(models.Model):
    subtree_path = models.CharField(max_length=1024, primary_key=True)
    recent_failures = models.IntegerField()
    last_checked = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "subtrees_needing_fallback_review"


class MigrationHealthOverview(models.Model):
    dest_cloud = models.CharField(max_length=10, primary_key=True)
    transferred = models.IntegerField()
    permissions_verified = models.IntegerField()
    permissions_failed = models.IntegerField()
    open_security_mismatches = models.IntegerField()

    class Meta:
        managed = False
        db_table = "migration_health_overview"


# ---------------------------------------------------------------------------
# Mapping-table review queue (Phase 3).
#
# `translate_ace` (migration.permission_mapping.translate) is a pure
# function and never writes here itself -- this table is filled by the
# thin wrapper in migration.permission_mapping.review_queue, which
# dedupes so each (ntfs_right, object_type, mapping_table_version)
# pattern is only queued once, on first encounter.
# ---------------------------------------------------------------------------


class MappingReviewQueueEntry(models.Model):
    ntfs_right = models.CharField(max_length=50)
    object_type = models.CharField(max_length=10)
    confidence = models.CharField(max_length=20)
    mapping_table_version = models.CharField(max_length=20)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    reviewed = models.BooleanField(default=False)

    class Meta:
        db_table = "mapping_review_queue_entry"
        constraints = [
            models.UniqueConstraint(
                fields=["ntfs_right", "object_type", "mapping_table_version"],
                name="unique_mapping_review_pattern",
            )
        ]

    def __str__(self):
        return f"{self.ntfs_right}/{self.object_type} v{self.mapping_table_version} [{self.confidence}]"


# ---------------------------------------------------------------------------
# Cutover run history (Phase 7).
#
# `CutoverConfig` (Phase 1) is the append-only, insert-only *policy*
# table -- mode and thresholds per scope. It has no field for "this
# specific cutover attempt reached this outcome", which the design doc's
# sequence diagram calls "mark subtree cutover_complete". This is that:
# one row per `CutoverWorkflow` execution, distinct from the config
# that governed it.
# ---------------------------------------------------------------------------


class CutoverRunStatus(models.TextChoices):
    IN_PROGRESS = "in_progress", "In progress"
    COMPLETED = "completed", "Completed"
    BLOCKED = "blocked", "Blocked"
    ABORTED = "aborted", "Aborted"


class CutoverRun(models.Model):
    scope = models.CharField(max_length=1024)
    mode = models.CharField(max_length=20, choices=CutoverMode.choices)
    status = models.CharField(
        max_length=20, choices=CutoverRunStatus.choices, default=CutoverRunStatus.IN_PROGRESS
    )
    blocked_reason = models.TextField(null=True, blank=True)
    workflow_id = models.CharField(max_length=255, null=True, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "cutover_run"
        indexes = [models.Index(fields=["scope", "status"])]

    def __str__(self):
        return f"{self.scope} [{self.mode}] -> {self.status}"


# ---------------------------------------------------------------------------
# Alert dedup (Phase 9). Page-immediately checks re-scan existing tables on
# every run (per the brief: "don't build new instrumentation where an
# existing table already has the signal") -- this is what stops the same
# underlying row/event from re-firing a page on every subsequent scan.
# ---------------------------------------------------------------------------


class AlertLog(models.Model):
    alert_type = models.CharField(max_length=50)
    subject_key = models.CharField(max_length=255)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "alert_log"
        constraints = [
            models.UniqueConstraint(
                fields=["alert_type", "subject_key"], name="unique_alert_per_subject"
            )
        ]

    def __str__(self):
        return f"{self.alert_type}:{self.subject_key}"
