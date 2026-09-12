from migration.diffing.effective_access import (
    effective_access_from_ace4,
    effective_access_from_ntfs,
)
from migration.discovery.types import ACE
from migration.permission_mapping.types import NFSv4Ace


def test_allow_only_grants_categories():
    aces = [
        ACE(identity="DOMAIN\\jane", rights=frozenset({"ReadData", "WriteData"}), allow=True, inherited=False)
    ]
    effective = effective_access_from_ntfs(aces)
    assert effective["DOMAIN\\jane"].categories == frozenset({"read", "write"})


def test_deny_overrides_allow_for_same_category():
    aces = [
        ACE(identity="DOMAIN\\jane", rights=frozenset({"ReadData"}), allow=True, inherited=False),
        ACE(identity="DOMAIN\\jane", rights=frozenset({"ReadData"}), allow=False, inherited=False),
    ]
    effective = effective_access_from_ntfs(aces)
    assert effective["DOMAIN\\jane"].categories == frozenset()


def test_deny_only_affects_denied_category_not_others():
    aces = [
        ACE(identity="DOMAIN\\jane", rights=frozenset({"ReadData", "WriteData"}), allow=True, inherited=False),
        ACE(identity="DOMAIN\\jane", rights=frozenset({"WriteData"}), allow=False, inherited=False),
    ]
    effective = effective_access_from_ntfs(aces)
    assert effective["DOMAIN\\jane"].categories == frozenset({"read"})


def test_any_inherited_true_when_any_allow_ace_is_inherited():
    aces = [
        ACE(identity="DOMAIN\\jane", rights=frozenset({"ReadData"}), allow=True, inherited=True),
        ACE(identity="DOMAIN\\jane", rights=frozenset({"WriteData"}), allow=True, inherited=False),
    ]
    effective = effective_access_from_ntfs(aces)
    assert effective["DOMAIN\\jane"].any_inherited is True


def test_any_inherited_false_when_all_allow_aces_explicit():
    aces = [ACE(identity="DOMAIN\\jane", rights=frozenset({"ReadData"}), allow=True, inherited=False)]
    effective = effective_access_from_ntfs(aces)
    assert effective["DOMAIN\\jane"].any_inherited is False


def test_synchronize_right_contributes_no_category():
    aces = [ACE(identity="DOMAIN\\jane", rights=frozenset({"Synchronize"}), allow=True, inherited=False)]
    effective = effective_access_from_ntfs(aces)
    assert "DOMAIN\\jane" not in effective


def test_ace4_allow_and_deny_precedence():
    aces = [
        NFSv4Ace(identity="DOMAIN\\jane", allow=True, ace4_bits=frozenset({"ACE4_READ_DATA", "ACE4_WRITE_DATA"}), ace4_flags=frozenset()),
        NFSv4Ace(identity="DOMAIN\\jane", allow=False, ace4_bits=frozenset({"ACE4_WRITE_DATA"}), ace4_flags=frozenset()),
    ]
    effective = effective_access_from_ace4(aces)
    assert effective["DOMAIN\\jane"].categories == frozenset({"read"})
    assert effective["DOMAIN\\jane"].any_inherited is False
