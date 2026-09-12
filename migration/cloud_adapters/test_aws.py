from migration.cloud_adapters.aws import AWSAdapter
from migration.cloud_adapters.testing import FakeDestACLReader, FakeTransport
from migration.cloud_adapters.types import FileRecord
from migration.discovery.types import ACE, FileACL

SOURCE_ACL = FileACL(
    owner="DOMAIN\\admin",
    group=None,
    aces=(
        ACE(identity="BUILTIN\\Administrators", rights=frozenset({"FullControl"}), allow=True, inherited=False),
        ACE(identity="DOMAIN\\jane.doe", rights=frozenset({"ReadAndExecute"}), allow=True, inherited=False),
    ),
)


def _record(fsid=1, dest="/fsx/share/file.txt"):
    return FileRecord(
        file_status_id=fsid,
        source_path="/onprem/share/file.txt",
        dest_path=dest,
        is_dir=False,
        source_acl=SOURCE_ACL,
    )


def test_transfer_copies_each_file_and_reports_success():
    transport = FakeTransport()
    adapter = AWSAdapter(transport=transport, dest_acl_reader=FakeDestACLReader({}))

    result = adapter.transfer([_record()])

    assert transport.calls == [("/onprem/share/file.txt", "/fsx/share/file.txt")]
    assert len(result.outcomes) == 1
    assert result.outcomes[0].success is True


def test_transfer_reports_failure_without_raising():
    transport = FakeTransport(fail_dest_paths=frozenset({"/fsx/share/file.txt"}))
    adapter = AWSAdapter(transport=transport, dest_acl_reader=FakeDestACLReader({}))

    result = adapter.transfer([_record()])

    outcome = result.outcomes[0]
    assert outcome.success is False
    assert "simulated copy failure" in outcome.error_message


def test_apply_permissions_succeeds_when_ace_count_matches():
    reader = FakeDestACLReader({"/fsx/share/file.txt": SOURCE_ACL})
    adapter = AWSAdapter(transport=FakeTransport(), dest_acl_reader=reader)

    result = adapter.apply_permissions([_record()], mapping_table_version="n/a")

    outcome = result.outcomes[0]
    assert outcome.success is True
    assert outcome.ace_count_source == 2
    assert outcome.ace_count_dest == 2


def test_apply_permissions_fails_on_ace_count_mismatch():
    dest_acl = FileACL(owner="", group=None, aces=SOURCE_ACL.aces[:1])
    reader = FakeDestACLReader({"/fsx/share/file.txt": dest_acl})
    adapter = AWSAdapter(transport=FakeTransport(), dest_acl_reader=reader)

    result = adapter.apply_permissions([_record()], mapping_table_version="n/a")

    outcome = result.outcomes[0]
    assert outcome.success is False
    assert "mismatch" in outcome.error_message


def test_verify_reruns_the_same_check():
    reader = FakeDestACLReader({"/fsx/share/file.txt": SOURCE_ACL})
    adapter = AWSAdapter(transport=FakeTransport(), dest_acl_reader=reader)

    result = adapter.verify([_record()])

    assert result.outcomes[0].success is True
