"""
Creates the four database views backing the unmanaged reporting models:
PermissionMigrationSummary, SignOffGate, SubtreesNeedingFallbackReview,
and MigrationHealthOverview.

These are plain SQL views -- Django's migration framework has no concept
of CREATE VIEW, so each one is a RunSQL operation with a matching
reverse_sql to DROP it on rollback.

permission_migration_summary and sign_off_gate have no single column
that's naturally unique per row, but their unmanaged models rely on
Django's implicit 'id' primary key (neither model sets primary_key=True
on any field). row_number() OVER () supplies that id column so the ORM
behaves as expected -- it's a synthetic key for read purposes only,
not stable across re-runs of the query, which is fine for reporting
views that are never written back to.
"""

from django.db import migrations

CREATE_PERMISSION_MIGRATION_SUMMARY = """
CREATE VIEW permission_migration_summary AS
SELECT
    row_number() OVER () AS id,
    dest_cloud,
    permission_status,
    mapping_table_version,
    count(*) AS count
FROM migration_file_status
GROUP BY dest_cloud, permission_status, mapping_table_version;
"""

DROP_PERMISSION_MIGRATION_SUMMARY = "DROP VIEW IF EXISTS permission_migration_summary;"

# effective_permission_diff's own primary key column is 'id' (Django's
# default AutoField, not the hand-written 'diff_id' from the original
# design sketch) -- same for diff_exception's 'id'. The FK column name
# on effective_permission_diff pointing at migration_file_status is
# 'file_id', derived from the model's `file = models.ForeignKey(...)`
# field name regardless of what the target table's own pk is called.
CREATE_SIGN_OFF_GATE = """
CREATE VIEW sign_off_gate AS
SELECT
    row_number() OVER () AS id,
    d.file_id,
    d.identity,
    d.mismatch_type,
    sp.action
FROM effective_permission_diff d
JOIN sign_off_policy sp
    ON sp.mismatch_type = d.mismatch_type AND sp.active = true
LEFT JOIN diff_exception e ON e.diff_id = d.id
WHERE d.reviewed = false
  AND (
    sp.action = 'block'
    OR (sp.action = 'require_exception' AND e.id IS NULL)
  );
"""

DROP_SIGN_OFF_GATE = "DROP VIEW IF EXISTS sign_off_gate;"

CREATE_SUBTREES_NEEDING_FALLBACK_REVIEW = """
CREATE VIEW subtrees_needing_fallback_review AS
SELECT
    subtree_path,
    count(*) FILTER (WHERE NOT passed) AS recent_failures,
    max(checked_at) AS last_checked
FROM lag_stability_check
WHERE checked_at > now() - interval '24 hours'
GROUP BY subtree_path
HAVING count(*) FILTER (WHERE NOT passed) >= 5;
"""

DROP_SUBTREES_NEEDING_FALLBACK_REVIEW = "DROP VIEW IF EXISTS subtrees_needing_fallback_review;"

CREATE_MIGRATION_HEALTH_OVERVIEW = """
CREATE VIEW migration_health_overview AS
SELECT
    dest_cloud,
    count(*) FILTER (WHERE transfer_status = 'transferred') AS transferred,
    count(*) FILTER (WHERE permission_status = 'verified') AS permissions_verified,
    count(*) FILTER (WHERE permission_status = 'failed') AS permissions_failed,
    (
        SELECT count(*) FROM effective_permission_diff d
        WHERE d.mismatch_type = 'extra_grant' AND d.reviewed = false
    ) AS open_security_mismatches
FROM migration_file_status
GROUP BY dest_cloud;
"""

DROP_MIGRATION_HEALTH_OVERVIEW = "DROP VIEW IF EXISTS migration_health_overview;"


class Migration(migrations.Migration):

    dependencies = [
        ("migration", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql=CREATE_PERMISSION_MIGRATION_SUMMARY,
            reverse_sql=DROP_PERMISSION_MIGRATION_SUMMARY,
        ),
        migrations.RunSQL(
            sql=CREATE_SIGN_OFF_GATE,
            reverse_sql=DROP_SIGN_OFF_GATE,
        ),
        migrations.RunSQL(
            sql=CREATE_SUBTREES_NEEDING_FALLBACK_REVIEW,
            reverse_sql=DROP_SUBTREES_NEEDING_FALLBACK_REVIEW,
        ),
        migrations.RunSQL(
            sql=CREATE_MIGRATION_HEALTH_OVERVIEW,
            reverse_sql=DROP_MIGRATION_HEALTH_OVERVIEW,
        ),
    ]
