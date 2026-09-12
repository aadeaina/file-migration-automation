"""Phase 3: the pure NTFS ACE -> NFSv4 ACE4 translation function.

No I/O here by design (per the brief) so it's trivially unit-testable:
callers that want mapping-review-queue side effects (routing anything
non-"exact" to a review queue on first encounter) wrap this function
rather than have it reach into the database itself -- see
`migration.permission_mapping.review_queue`.
"""

from __future__ import annotations

from migration.discovery.types import ACE
from migration.permission_mapping.types import (
    MappingTable,
    NFSv4Ace,
    ObjectType,
    ReviewFlag,
    TranslationResult,
)

_INHERITANCE_FLAGS_ON_ACE = (
    ("ObjectInherit", "inherit_to_files"),
    ("ContainerInherit", "inherit_to_subfolders"),
    ("InheritOnly", "inherit_only"),
    ("NoPropagateInherit", "no_propagate"),
)


def translate_ace(
    ntfs_ace: ACE,
    object_type: ObjectType,
    mapping_table: MappingTable,
) -> TranslationResult:
    """Translate one NTFS ACE into its NFSv4 ACE4 equivalent using
    `mapping_table`. Deny ACEs translate like allow ACEs (the ace4_bits
    carry the same meaning either way) -- `NFSv4Ace.allow` carries the
    allow/deny distinction through.

    Any right with no row for (right, object_type) in the table is
    reported in `unmapped_rights` rather than silently dropped or
    raising, since a caller may reasonably want to treat "no mapping
    exists" itself as a review-worthy condition.
    """
    ace4_bits: set[str] = set()
    review_flags: list[ReviewFlag] = []
    unmapped: set[str] = set()

    for right in ntfs_ace.rights:
        rows = mapping_table.rows_for(right, object_type)
        if not rows:
            unmapped.add(right)
            continue
        for row in rows:
            ace4_bits.add(row.ace4_bit)
            if row.confidence != "exact":
                review_flags.append(
                    ReviewFlag(
                        ntfs_right=row.ntfs_right,
                        object_type=row.object_type,
                        confidence=row.confidence,
                        mapping_table_version=mapping_table.version,
                    )
                )

    ace4_flags: set[str] = set()
    for ntfs_flag, ace_attr in _INHERITANCE_FLAGS_ON_ACE:
        if getattr(ntfs_ace, ace_attr):
            ace4_flag = mapping_table.ace4_inheritance_flag(ntfs_flag)
            if ace4_flag:
                ace4_flags.add(ace4_flag)

    nfsv4_ace = NFSv4Ace(
        identity=ntfs_ace.identity,
        allow=ntfs_ace.allow,
        ace4_bits=frozenset(ace4_bits),
        ace4_flags=frozenset(ace4_flags),
    )

    return TranslationResult(
        ace=nfsv4_ace,
        review_flags=tuple(review_flags),
        unmapped_rights=frozenset(unmapped),
    )
