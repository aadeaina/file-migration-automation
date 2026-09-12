"""JSON-safe data-transfer types for crossing the Temporal activity
boundary. The internal dataclasses in `migration.discovery.types` /
`migration.permission_mapping.types` use `frozenset`, which Temporal's
default (JSON-based) data converter can't serialize -- these mirror
them with plain lists instead, plus the (de)serialization helpers.
"""

from __future__ import annotations

from dataclasses import dataclass

from migration.discovery.types import ACE
from migration.permission_mapping.types import NFSv4Ace


@dataclass
class AceDTO:
    identity: str
    rights: list[str]
    allow: bool
    inherited: bool
    inherit_to_files: bool = False
    inherit_to_subfolders: bool = False
    inherit_only: bool = False
    no_propagate: bool = False


def ace_to_dto(ace: ACE) -> AceDTO:
    return AceDTO(
        identity=ace.identity,
        rights=sorted(ace.rights),
        allow=ace.allow,
        inherited=ace.inherited,
        inherit_to_files=ace.inherit_to_files,
        inherit_to_subfolders=ace.inherit_to_subfolders,
        inherit_only=ace.inherit_only,
        no_propagate=ace.no_propagate,
    )


def dto_to_ace(dto: AceDTO) -> ACE:
    return ACE(
        identity=dto.identity,
        rights=frozenset(dto.rights),
        allow=dto.allow,
        inherited=dto.inherited,
        inherit_to_files=dto.inherit_to_files,
        inherit_to_subfolders=dto.inherit_to_subfolders,
        inherit_only=dto.inherit_only,
        no_propagate=dto.no_propagate,
    )


@dataclass
class NFSv4AceDTO:
    identity: str
    allow: bool
    ace4_bits: list[str]
    ace4_flags: list[str]


def nfsv4ace_to_dto(ace: NFSv4Ace) -> NFSv4AceDTO:
    return NFSv4AceDTO(
        identity=ace.identity,
        allow=ace.allow,
        ace4_bits=sorted(ace.ace4_bits),
        ace4_flags=sorted(ace.ace4_flags),
    )


def dto_to_nfsv4ace(dto: NFSv4AceDTO) -> NFSv4Ace:
    return NFSv4Ace(
        identity=dto.identity,
        allow=dto.allow,
        ace4_bits=frozenset(dto.ace4_bits),
        ace4_flags=frozenset(dto.ace4_flags),
    )
