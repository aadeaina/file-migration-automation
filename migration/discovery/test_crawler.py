"""Phase 2 deliverable test: given a test directory tree with varied
permissions (deep inheritance, an explicit deny ACE, a non-resolvable
SID), the crawler produces a complete, accurate manifest.
"""

from __future__ import annotations

from pathlib import Path

from migration.discovery.crawler import walk_tree
from migration.discovery.testing import FakeACLReader
from migration.discovery.types import ACE, Anomaly, FileACL

ADMIN_ACE_EXPLICIT = ACE(
    identity="BUILTIN\\Administrators",
    rights=frozenset({"FullControl"}),
    allow=True,
    inherited=False,
    inherit_to_files=True,
    inherit_to_subfolders=True,
)
ADMIN_ACE_INHERITED = ACE(
    identity="BUILTIN\\Administrators",
    rights=frozenset({"FullControl"}),
    allow=True,
    inherited=True,
    inherit_to_files=True,
    inherit_to_subfolders=True,
)
JANE_ACE_EXPLICIT = ACE(
    identity="DOMAIN\\jane.doe",
    rights=frozenset({"ReadAndExecute"}),
    allow=True,
    inherited=False,
    inherit_to_files=True,
    inherit_to_subfolders=True,
)
JANE_ACE_INHERITED = ACE(
    identity="DOMAIN\\jane.doe",
    rights=frozenset({"ReadAndExecute"}),
    allow=True,
    inherited=True,
    inherit_to_files=True,
    inherit_to_subfolders=True,
)
ORPHANED_SID = "S-1-5-21-1111111111-2222222222-3333333333-9999"


def _build_tree(root: Path) -> dict[str, FileACL]:
    (root / "folder").mkdir()
    (root / "folder" / "sub").mkdir()
    (root / "folder" / "sub" / "deep_file.txt").write_text("deep")
    (root / "folder" / "denied_file.txt").write_text("denied")
    (root / "folder" / "orphan_file.txt").write_text("orphan")
    (root / "folder" / "broken_file.txt").write_text("broken")

    return {
        "folder": FileACL(
            owner="DOMAIN\\admin",
            group=None,
            aces=(ADMIN_ACE_EXPLICIT, JANE_ACE_EXPLICIT),
        ),
        "folder/sub": FileACL(
            owner="DOMAIN\\admin",
            group=None,
            aces=(ADMIN_ACE_INHERITED, JANE_ACE_INHERITED),
        ),
        "folder/sub/deep_file.txt": FileACL(
            owner="DOMAIN\\admin",
            group=None,
            aces=(ADMIN_ACE_INHERITED, JANE_ACE_INHERITED),
        ),
        "folder/denied_file.txt": FileACL(
            owner="DOMAIN\\admin",
            group=None,
            aces=(
                ADMIN_ACE_INHERITED,
                JANE_ACE_INHERITED,
                ACE(
                    identity="DOMAIN\\blocked.user",
                    rights=frozenset({"WriteData"}),
                    allow=False,
                    inherited=False,
                ),
            ),
        ),
        "folder/orphan_file.txt": FileACL(
            owner="DOMAIN\\admin",
            group=None,
            aces=(
                ADMIN_ACE_INHERITED,
                JANE_ACE_INHERITED,
                ACE(
                    identity=ORPHANED_SID,
                    rights=frozenset({"ReadData"}),
                    allow=True,
                    inherited=False,
                ),
            ),
        ),
        "folder/broken_file.txt": FileACL(
            owner="DOMAIN\\admin",
            group=None,
            aces=(ADMIN_ACE_INHERITED,),  # missing jane.doe's expected inherited RX
        ),
    }


def test_crawler_produces_complete_accurate_manifest(tmp_path):
    acls = _build_tree(tmp_path)
    reader = FakeACLReader(acls, root=tmp_path)

    manifest = walk_tree(tmp_path, reader)
    by_path = {e.path: e for e in manifest}

    assert set(by_path) == set(acls)

    # Deep (2-level) inheritance chain that IS intact must not be flagged.
    assert by_path["folder/sub"].anomalies == []
    assert by_path["folder/sub/deep_file.txt"].anomalies == []
    assert by_path["folder/sub/deep_file.txt"].depth == 2

    # Explicit deny ACE is flagged, without false-flagging broken inheritance.
    assert by_path["folder/denied_file.txt"].anomalies == [Anomaly.EXPLICIT_DENY]

    # Non-resolvable SID is flagged.
    assert by_path["folder/orphan_file.txt"].anomalies == [Anomaly.ORPHANED_SID]

    # Missing an ACE that should have propagated down is flagged.
    assert by_path["folder/broken_file.txt"].anomalies == [Anomaly.BROKEN_INHERITANCE]

    # Directory sizes are 0, file sizes reflect real content, depths are correct.
    assert by_path["folder"].is_dir is True
    assert by_path["folder"].depth == 0
    assert by_path["folder/denied_file.txt"].is_dir is False
    assert by_path["folder/denied_file.txt"].size == len("denied")
