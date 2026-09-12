from migration.diffing.diff import diff_effective_access
from migration.diffing.effective_access import EffectiveAccessEntry
from migration.models import DiffSeverity, MismatchType


def _entry(categories, any_inherited=False):
    return EffectiveAccessEntry(categories=frozenset(categories), any_inherited=any_inherited)


def test_identical_access_produces_no_rows():
    source = {"DOMAIN\\jane": _entry({"read"})}
    dest = {"DOMAIN\\jane": _entry({"read"})}
    assert diff_effective_access(source, dest) == []


def test_missing_grant_when_dest_lacks_a_source_category():
    source = {"DOMAIN\\jane": _entry({"read", "write"})}
    dest = {"DOMAIN\\jane": _entry({"read"})}
    rows = diff_effective_access(source, dest)
    assert len(rows) == 1
    row = rows[0]
    assert row.mismatch_type == MismatchType.MISSING_GRANT
    assert row.severity == DiffSeverity.WARNING
    assert row.source_access == ["read", "write"]
    assert row.dest_access == ["read"]


def test_extra_grant_when_dest_has_category_source_never_had():
    source = {"DOMAIN\\jane": _entry({"read"})}
    dest = {"DOMAIN\\jane": _entry({"read", "write"})}
    rows = diff_effective_access(source, dest)
    assert len(rows) == 1
    assert rows[0].mismatch_type == MismatchType.EXTRA_GRANT
    assert rows[0].severity == DiffSeverity.CRITICAL


def test_inheritance_divergence_when_access_matches_but_inherited_flag_differs():
    source = {"DOMAIN\\jane": _entry({"read"}, any_inherited=True)}
    dest = {"DOMAIN\\jane": _entry({"read"}, any_inherited=False)}
    rows = diff_effective_access(source, dest)
    assert len(rows) == 1
    assert rows[0].mismatch_type == MismatchType.INHERITANCE_DIVERGENCE
    assert rows[0].severity == DiffSeverity.INFO


def test_unresolvable_identity_short_circuits_other_checks():
    source = {"S-1-5-21-1-2-3-9999": _entry({"read"})}
    dest = {}
    rows = diff_effective_access(source, dest, orphaned_identities=frozenset({"S-1-5-21-1-2-3-9999"}))
    assert len(rows) == 1
    assert rows[0].mismatch_type == MismatchType.UNRESOLVABLE_IDENTITY
    assert rows[0].severity == DiffSeverity.WARNING


def test_identity_only_on_dest_side_is_an_extra_grant():
    source = {}
    dest = {"DOMAIN\\newcomer": _entry({"read"})}
    rows = diff_effective_access(source, dest)
    assert len(rows) == 1
    assert rows[0].mismatch_type == MismatchType.EXTRA_GRANT


def test_can_produce_both_missing_and_extra_for_same_identity():
    source = {"DOMAIN\\jane": _entry({"read", "delete"})}
    dest = {"DOMAIN\\jane": _entry({"read", "write"})}
    rows = diff_effective_access(source, dest)
    types = {r.mismatch_type for r in rows}
    assert types == {MismatchType.MISSING_GRANT, MismatchType.EXTRA_GRANT}
