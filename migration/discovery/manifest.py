"""Manifest (de)serialization and loading into MigrationFileStatus.

The manifest file itself doubles as the "raw-ACL store keyed by file
path" the brief asks for -- each entry already carries its full ACL,
so there's no need for a second parallel store.
"""

from __future__ import annotations

import json
from pathlib import Path

from migration.discovery.types import ManifestEntry
from migration.models import (
    DestCloud,
    MigrationFileStatus,
    PermissionStatus,
    TransferStatus,
)

DEFAULT_DEST_CLOUDS = (DestCloud.AWS, DestCloud.AZURE, DestCloud.GCP)


def write_manifest(entries: list[ManifestEntry], out_path: Path | str) -> None:
    out_path = Path(out_path)
    out_path.write_text(json.dumps([e.to_dict() for e in entries], indent=2))


def read_manifest(in_path: Path | str) -> list[ManifestEntry]:
    in_path = Path(in_path)
    data = json.loads(in_path.read_text())
    return [ManifestEntry.from_dict(d) for d in data]


def load_manifest_into_db(
    entries: list[ManifestEntry],
    dest_clouds: tuple[str, ...] = DEFAULT_DEST_CLOUDS,
) -> list[MigrationFileStatus]:
    """Create one `MigrationFileStatus` row per (file, destination cloud) --
    files fan out to all three clouds simultaneously per the project brief.
    Directories are skipped: they don't get transferred/verified as
    standalone objects, their permissions ride along with the files under
    them.
    """
    rows = [
        MigrationFileStatus(
            source_path=entry.path,
            dest_cloud=dest_cloud,
            dest_path=entry.path,
            transfer_status=TransferStatus.QUEUED,
            permission_status=PermissionStatus.PENDING,
        )
        for entry in entries
        if not entry.is_dir
        for dest_cloud in dest_clouds
    ]
    return MigrationFileStatus.objects.bulk_create(rows)
