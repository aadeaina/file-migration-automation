"""Manifest (de)serialization and loading into MigrationFileStatus.

The manifest file itself doubles as the "raw-ACL store keyed by file
path" the brief asks for -- each entry already carries its full ACL,
so there's no need for a second parallel store.

JSONL (one JSON object per line), not a single JSON array: at 2M+
files, `json.dumps([...])`/`json.loads()` over one giant array means
holding the entire manifest in memory at once just to read or write
it. JSONL streams -- `write_manifest` never holds more than one entry
at a time, `read_manifest` is a generator.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path

from migration.discovery.types import ManifestEntry
from migration.models import (
    DestCloud,
    MigrationFileStatus,
    PermissionStatus,
    TransferStatus,
)

DEFAULT_DEST_CLOUDS = (DestCloud.AWS, DestCloud.AZURE, DestCloud.GCP)
DEFAULT_LOAD_BATCH_SIZE = 2000


def write_manifest(entries: Iterable[ManifestEntry], out_path: Path | str) -> int:
    """Streams `entries` to `out_path` as JSONL. Returns the count
    written -- `entries` may be a one-shot generator (e.g. `walk_tree`'s
    output), so the caller can't just `len()` it afterward."""
    out_path = Path(out_path)
    count = 0
    with out_path.open("w") as f:
        for entry in entries:
            f.write(json.dumps(entry.to_dict()))
            f.write("\n")
            count += 1
    return count


def read_manifest(in_path: Path | str) -> Iterator[ManifestEntry]:
    """A generator: never holds more than one line/entry in memory."""
    in_path = Path(in_path)
    with in_path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                yield ManifestEntry.from_dict(json.loads(line))


def load_manifest_into_db(
    entries: Iterable[ManifestEntry],
    dest_clouds: tuple[str, ...] = DEFAULT_DEST_CLOUDS,
    batch_size: int = DEFAULT_LOAD_BATCH_SIZE,
    collect_created: bool = True,
) -> list[MigrationFileStatus]:
    """Create one `MigrationFileStatus` row per (file, destination cloud) --
    files fan out to all three clouds simultaneously per the project brief.
    Directories are skipped: they don't get transferred/verified as
    standalone objects, their permissions ride along with the files under
    them.

    Consumes `entries` (which may be a generator streaming off disk) and
    writes in `batch_size`-row chunks via `bulk_create`, rather than
    building every row for the whole manifest in memory before a single
    `bulk_create` call -- at 2M files x 3 clouds that's 6M model
    instances held at once just to issue one (very large) statement.

    `collect_created=False` skips accumulating the return list too, for
    callers processing a manifest too large to hold 6M created objects
    in memory just to discard them.
    """
    created: list[MigrationFileStatus] = []
    buffer: list[MigrationFileStatus] = []

    def flush() -> None:
        if not buffer:
            return
        result = MigrationFileStatus.objects.bulk_create(buffer, batch_size=batch_size)
        if collect_created:
            created.extend(result)
        buffer.clear()

    for entry in entries:
        if entry.is_dir:
            continue
        for dest_cloud in dest_clouds:
            buffer.append(
                MigrationFileStatus(
                    source_path=entry.path,
                    dest_cloud=dest_cloud,
                    dest_path=entry.path,
                    transfer_status=TransferStatus.QUEUED,
                    permission_status=PermissionStatus.PENDING,
                )
            )
            if len(buffer) >= batch_size:
                flush()
    flush()

    return created
