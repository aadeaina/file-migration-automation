"""Loads the versioned NTFS -> NFSv4 mapping table from its JSON file."""

from __future__ import annotations

import json
from pathlib import Path

from migration.permission_mapping.types import (
    InheritanceMapRow,
    MappingRow,
    MappingTable,
)

DEFAULT_MAPPING_TABLE_PATH = Path(__file__).parent / "data" / "ntfs_to_nfsv4_v1.json"


def load_mapping_table(path: Path | str = DEFAULT_MAPPING_TABLE_PATH) -> MappingTable:
    data = json.loads(Path(path).read_text())
    return MappingTable(
        version=data["version"],
        mappings=tuple(MappingRow(**row) for row in data["mappings"]),
        inheritance_map=tuple(InheritanceMapRow(**row) for row in data["inheritance_map"]),
    )
