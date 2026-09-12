"""Types for the NTFS -> NFSv4 ACE4 mapping table and translation result."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Confidence = Literal["exact", "partial", "policy_decision"]
ObjectType = Literal["file", "directory"]


@dataclass(frozen=True)
class MappingRow:
    ntfs_right: str
    object_type: ObjectType
    ace4_bit: str
    confidence: Confidence


@dataclass(frozen=True)
class InheritanceMapRow:
    ntfs_flag: str
    ace4_flag: str


@dataclass(frozen=True)
class MappingTable:
    version: str
    mappings: tuple[MappingRow, ...]
    inheritance_map: tuple[InheritanceMapRow, ...]

    def rows_for(self, ntfs_right: str, object_type: ObjectType) -> list[MappingRow]:
        return [
            row
            for row in self.mappings
            if row.ntfs_right == ntfs_right and row.object_type == object_type
        ]

    def ace4_inheritance_flag(self, ntfs_flag: str) -> str | None:
        for row in self.inheritance_map:
            if row.ntfs_flag == ntfs_flag:
                return row.ace4_flag
        return None


@dataclass(frozen=True)
class NFSv4Ace:
    identity: str
    allow: bool
    ace4_bits: frozenset[str]
    ace4_flags: frozenset[str]


@dataclass(frozen=True)
class ReviewFlag:
    """Signals that a mapping row applied during translation needs human
    review before being trusted -- anything not `exact` confidence."""

    ntfs_right: str
    object_type: ObjectType
    confidence: Confidence
    mapping_table_version: str


@dataclass(frozen=True)
class TranslationResult:
    ace: NFSv4Ace
    review_flags: tuple[ReviewFlag, ...]
    unmapped_rights: frozenset[str]
