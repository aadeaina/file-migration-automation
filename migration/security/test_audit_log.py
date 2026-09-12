import logging

from migration.security.audit_log import log_acl_write
from migration.security.paging import AuthenticationError, page_immediately


def test_acl_write_is_logged_with_identity_path_and_result(caplog):
    with caplog.at_level(logging.INFO, logger="migration.security.acl_write_audit"):
        log_acl_write("svc-migration-gcp", "/filestore/share/file.txt", success=True)

    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "svc-migration-gcp" in message
    assert "/filestore/share/file.txt" in message
    assert "True" in message


def test_failed_acl_write_includes_detail(caplog):
    with caplog.at_level(logging.INFO, logger="migration.security.acl_write_audit"):
        log_acl_write(
            "svc-migration-aws", "/fsx/share/file.txt", success=False, detail="ACE count mismatch"
        )

    message = caplog.records[0].getMessage()
    assert "False" in message
    assert "ACE count mismatch" in message


def test_page_immediately_logs_at_critical(caplog):
    with caplog.at_level(logging.CRITICAL, logger="migration.security.page_immediately"):
        page_immediately("auth_failure", "AWS credential rejected for svc-migration-aws")

    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.CRITICAL
    assert "auth_failure" in caplog.records[0].getMessage()


def test_authentication_error_is_an_oserror_subclass():
    assert issubclass(AuthenticationError, OSError)
