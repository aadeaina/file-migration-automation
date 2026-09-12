from migration.cloud_adapters.azure import AzureAdapter
from migration.cloud_adapters.testing import FakeDestACLReader, FakeTransport
from migration.cloud_adapters.types import FileRecord
from migration.discovery.types import ACE, FileACL

SOURCE_ACL = FileACL(
    owner="DOMAIN\\admin",
    group=None,
    aces=(ACE(identity="DOMAIN\\jane.doe", rights=frozenset({"ReadAndExecute"}), allow=True, inherited=False),),
)


def _record(dest="/azfiles/share/file.txt"):
    return FileRecord(
        file_status_id=1,
        source_path="/onprem/share/file.txt",
        dest_path=dest,
        is_dir=False,
        source_acl=SOURCE_ACL,
    )


def test_transfer_apply_permissions_and_verify_end_to_end():
    transport = FakeTransport()
    reader = FakeDestACLReader({"/azfiles/share/file.txt": SOURCE_ACL})
    adapter = AzureAdapter(transport=transport, dest_acl_reader=reader)

    transfer_result = adapter.transfer([_record()])
    assert transfer_result.outcomes[0].success is True
    assert transport.calls == [("/onprem/share/file.txt", "/azfiles/share/file.txt")]

    perm_result = adapter.apply_permissions([_record()], mapping_table_version="n/a")
    assert perm_result.outcomes[0].success is True
    assert perm_result.outcomes[0].ace_count_dest == 1

    verify_result = adapter.verify([_record()])
    assert verify_result.outcomes[0].success is True


def test_transfer_failure_surfaces_as_unsuccessful_outcome_not_exception():
    transport = FakeTransport(fail_dest_paths=frozenset({"/azfiles/share/file.txt"}))
    adapter = AzureAdapter(transport=transport, dest_acl_reader=FakeDestACLReader({}))

    result = adapter.transfer([_record()])

    assert result.outcomes[0].success is False
