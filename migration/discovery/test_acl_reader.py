from migration.discovery.acl_reader import is_orphaned_identity, parse_icacls_output

SAMPLE_OUTPUT = """\
file.txt BUILTIN\\Administrators:(F)
          NT AUTHORITY\\SYSTEM:(I)(F)
          DOMAIN\\jane.doe:(RX)
          DOMAIN\\blocked.user:(DENY)(WD)
          DOMAIN\\group:(OI)(CI)(M)
          S-1-5-21-1111111111-2222222222-3333333333-9999:(R)

Successfully processed 1 files; Failed processing 0 files.
"""


def test_parses_all_identities_and_rights():
    acl = parse_icacls_output(SAMPLE_OUTPUT)
    by_identity = {ace.identity: ace for ace in acl.aces}

    assert set(by_identity) == {
        "BUILTIN\\Administrators",
        "NT AUTHORITY\\SYSTEM",
        "DOMAIN\\jane.doe",
        "DOMAIN\\blocked.user",
        "DOMAIN\\group",
        "S-1-5-21-1111111111-2222222222-3333333333-9999",
    }

    assert by_identity["BUILTIN\\Administrators"].rights == frozenset({"FullControl"})
    assert by_identity["BUILTIN\\Administrators"].inherited is False

    assert by_identity["NT AUTHORITY\\SYSTEM"].inherited is True

    assert by_identity["DOMAIN\\jane.doe"].rights == frozenset({"ReadAndExecute"})

    deny_ace = by_identity["DOMAIN\\blocked.user"]
    assert deny_ace.allow is False
    assert deny_ace.rights == frozenset({"WriteData"})

    group_ace = by_identity["DOMAIN\\group"]
    assert group_ace.inherit_to_files is True
    assert group_ace.inherit_to_subfolders is True
    assert group_ace.rights == frozenset({"Modify"})


def test_flags_unresolved_sid_as_orphaned():
    acl = parse_icacls_output(SAMPLE_OUTPUT)
    orphaned = [ace for ace in acl.aces if is_orphaned_identity(ace.identity)]
    assert len(orphaned) == 1
    assert orphaned[0].identity == "S-1-5-21-1111111111-2222222222-3333333333-9999"


def test_resolved_identities_are_not_orphaned():
    assert is_orphaned_identity("DOMAIN\\jane.doe") is False
    assert is_orphaned_identity("BUILTIN\\Administrators") is False
