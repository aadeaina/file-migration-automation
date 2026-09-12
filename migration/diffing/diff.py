"""Pure diff computation: compares two `EffectiveAccessEntry` maps and
produces mismatch rows. No I/O, no DB -- `run_diff.py` is the thin
persistence wrapper, same split as Phase 3's `translate_ace`.
"""

from __future__ import annotations

from dataclasses import dataclass

from migration.diffing.effective_access import EMPTY_ACCESS, EffectiveAccessEntry
from migration.models import DiffSeverity, MismatchType

# extra_grant is a security regression (destination grants more than the
# source ever did) -- CRITICAL. missing_grant is a functional/access-loss
# problem -- WARNING. inheritance_divergence doesn't change effective
# access at all, just how it's achieved -- INFO. unresolvable_identity
# means we couldn't even evaluate the identity -- WARNING.
SEVERITY_BY_MISMATCH_TYPE = {
    MismatchType.EXTRA_GRANT: DiffSeverity.CRITICAL,
    MismatchType.MISSING_GRANT: DiffSeverity.WARNING,
    MismatchType.INHERITANCE_DIVERGENCE: DiffSeverity.INFO,
    MismatchType.UNRESOLVABLE_IDENTITY: DiffSeverity.WARNING,
}


@dataclass(frozen=True)
class DiffRow:
    identity: str
    mismatch_type: str
    severity: str
    source_access: list[str]
    dest_access: list[str]


def diff_effective_access(
    source: dict[str, EffectiveAccessEntry],
    dest: dict[str, EffectiveAccessEntry],
    orphaned_identities: frozenset[str] = frozenset(),
) -> list[DiffRow]:
    rows: list[DiffRow] = []

    for identity in sorted(set(source) | set(dest) | orphaned_identities):
        src = source.get(identity, EMPTY_ACCESS)
        dst = dest.get(identity, EMPTY_ACCESS)

        if identity in orphaned_identities:
            rows.append(
                DiffRow(
                    identity=identity,
                    mismatch_type=MismatchType.UNRESOLVABLE_IDENTITY,
                    severity=SEVERITY_BY_MISMATCH_TYPE[MismatchType.UNRESOLVABLE_IDENTITY],
                    source_access=sorted(src.categories),
                    dest_access=sorted(dst.categories),
                )
            )
            continue

        missing = src.categories - dst.categories
        extra = dst.categories - src.categories

        if missing:
            rows.append(
                DiffRow(
                    identity=identity,
                    mismatch_type=MismatchType.MISSING_GRANT,
                    severity=SEVERITY_BY_MISMATCH_TYPE[MismatchType.MISSING_GRANT],
                    source_access=sorted(src.categories),
                    dest_access=sorted(dst.categories),
                )
            )
        if extra:
            rows.append(
                DiffRow(
                    identity=identity,
                    mismatch_type=MismatchType.EXTRA_GRANT,
                    severity=SEVERITY_BY_MISMATCH_TYPE[MismatchType.EXTRA_GRANT],
                    source_access=sorted(src.categories),
                    dest_access=sorted(dst.categories),
                )
            )
        if not missing and not extra and src.any_inherited != dst.any_inherited:
            rows.append(
                DiffRow(
                    identity=identity,
                    mismatch_type=MismatchType.INHERITANCE_DIVERGENCE,
                    severity=SEVERITY_BY_MISMATCH_TYPE[MismatchType.INHERITANCE_DIVERGENCE],
                    source_access=sorted(src.categories),
                    dest_access=sorted(dst.categories),
                )
            )

    return rows
