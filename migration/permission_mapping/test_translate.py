"""Phase 3 deliverable: unit tests covering every mapping row, including
an explicit-deny-ACE case and an inheritance-flag case, verified against
hand-computed expected output.
"""

from __future__ import annotations

import pytest

from migration.discovery.types import ACE
from migration.permission_mapping.loader import load_mapping_table
from migration.permission_mapping.translate import translate_ace

TABLE = load_mapping_table()


def _ace(right: str, **kwargs) -> ACE:
    return ACE(
        identity="DOMAIN\\jane.doe",
        rights=frozenset({right}),
        allow=kwargs.pop("allow", True),
        inherited=kwargs.pop("inherited", False),
        **kwargs,
    )


@pytest.mark.parametrize("row", TABLE.mappings, ids=lambda r: f"{r.ntfs_right}/{r.object_type}")
def test_every_mapping_row_translates_to_its_ace4_bit(row):
    ace = _ace(row.ntfs_right)
    result = translate_ace(ace, row.object_type, TABLE)

    assert result.ace.ace4_bits == frozenset({row.ace4_bit})
    assert result.unmapped_rights == frozenset()

    if row.confidence == "exact":
        assert result.review_flags == ()
    else:
        assert len(result.review_flags) == 1
        flag = result.review_flags[0]
        assert flag.ntfs_right == row.ntfs_right
        assert flag.object_type == row.object_type
        assert flag.confidence == row.confidence
        assert flag.mapping_table_version == TABLE.version


def test_explicit_deny_ace_translates_with_allow_false():
    ace = _ace("ReadData", allow=False)
    result = translate_ace(ace, "file", TABLE)

    assert result.ace.allow is False
    assert result.ace.ace4_bits == frozenset({"ACE4_READ_DATA"})
    assert result.ace.identity == "DOMAIN\\jane.doe"


def test_inheritance_flags_translate_to_ace4_flags():
    ace = _ace(
        "ReadData",
        inherit_to_files=True,
        inherit_to_subfolders=True,
        inherit_only=True,
        no_propagate=True,
    )
    result = translate_ace(ace, "directory", TABLE)

    assert result.ace.ace4_flags == frozenset(
        {
            "ACE4_FILE_INHERIT_ACE",
            "ACE4_DIRECTORY_INHERIT_ACE",
            "ACE4_INHERIT_ONLY_ACE",
            "ACE4_NO_PROPAGATE_INHERIT_ACE",
        }
    )


def test_no_inheritance_flags_set_yields_no_ace4_flags():
    ace = _ace("ReadData")
    result = translate_ace(ace, "file", TABLE)
    assert result.ace.ace4_flags == frozenset()


def test_right_with_no_row_for_object_type_is_unmapped_not_dropped_silently():
    # DeleteSubdirectoriesAndFiles only has a row for "directory" in the
    # mapping table; requesting it against a file should surface as
    # unmapped, not silently vanish.
    ace = _ace("DeleteSubdirectoriesAndFiles")
    result = translate_ace(ace, "file", TABLE)

    assert result.unmapped_rights == frozenset({"DeleteSubdirectoriesAndFiles"})
    assert result.ace.ace4_bits == frozenset()


def test_multiple_rights_on_one_ace_all_translate():
    ace = ACE(
        identity="DOMAIN\\jane.doe",
        rights=frozenset({"ReadData", "ReadAttributes", "ReadPermissions"}),
        allow=True,
        inherited=False,
    )
    result = translate_ace(ace, "file", TABLE)

    assert result.ace.ace4_bits == frozenset(
        {"ACE4_READ_DATA", "ACE4_READ_ATTRIBUTES", "ACE4_READ_ACL"}
    )
    assert result.review_flags == ()
