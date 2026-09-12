"""Byte-copy transports: the production implementations shell out to the
tool named for each cloud in the brief. Kept behind a `Transport`
protocol so adapters (and their tests) don't depend on those binaries
being installed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol


class Transport(Protocol):
    def copy(self, source_path: str, dest_path: str) -> None:
        """Copy one file, preserving permissions where the tool supports
        it. Raise on failure."""
        ...


class RobocopyTransport:
    """AWS: Robocopy `/SEC` (copies data, attributes, and NTFS ACLs) into
    FSx for Windows File Server. Real DataSync tasks are the other
    brief-sanctioned option but operate at the task/location level
    rather than per file, which doesn't fit this per-batch interface as
    cleanly."""

    def copy(self, source_path: str, dest_path: str) -> None:
        src = Path(source_path)
        dst = Path(dest_path)
        result = subprocess.run(
            ["robocopy", str(src.parent), str(dst.parent), src.name, "/SEC", "/NFL", "/NDL"],
            capture_output=True,
            text=True,
        )
        # Robocopy's exit codes are a bitmask; 0-7 are all "succeeded to
        # some degree", 8+ means at least one failure.
        if result.returncode >= 8:
            raise OSError(f"robocopy failed ({result.returncode}): {result.stdout}")


class AzCopyTransport:
    """Azure: AzCopy with SMB permission-preservation flags into Azure Files."""

    def copy(self, source_path: str, dest_path: str) -> None:
        result = subprocess.run(
            [
                "azcopy",
                "copy",
                source_path,
                dest_path,
                "--preserve-smb-permissions=true",
                "--preserve-smb-info=true",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise OSError(f"azcopy failed ({result.returncode}): {result.stdout}")


class StorageTransferTransport:
    """GCP: byte copy ahead of the separate ACE-translation step. Backed
    in production by Storage Transfer Service (POSIX-to-POSIX, via the
    on-prem transfer agent pushing to the Filestore mount); exercised
    here through this same per-file interface for consistency with the
    other two adapters."""

    def copy(self, source_path: str, dest_path: str) -> None:
        import shutil

        shutil.copy2(source_path, dest_path)
