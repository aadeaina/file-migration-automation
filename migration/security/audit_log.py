"""Logs every ACL-write call with the service identity that made it,
alongside the file path and result -- distinct from the file-level
audit trail already in `MigrationFileStatus` (that table tracks *what
state a file is in*; this logs *who/what actually performed each write
and whether it succeeded*, which matters for a security review even
after the file's own status has moved on).
"""

from __future__ import annotations

import logging

acl_write_logger = logging.getLogger("migration.security.acl_write_audit")


def log_acl_write(
    service_identity: str, dest_path: str, success: bool, detail: str | None = None
) -> None:
    acl_write_logger.info(
        "acl_write identity=%s path=%s success=%s detail=%s",
        service_identity,
        dest_path,
        success,
        detail or "",
    )
