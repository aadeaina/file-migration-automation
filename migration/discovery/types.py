"""Data types for the discovery/inventory crawler (Phase 2)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ACE:
    """One NTFS access control entry, as read off the source file server."""

    identity: str
    rights: frozenset[str]
    allow: bool  # False means this is an explicit deny ACE
    inherited: bool
    inherit_to_files: bool = False
    inherit_to_subfolders: bool = False
    inherit_only: bool = False
    no_propagate: bool = False

    def propagates_to(self, *, is_dir: bool) -> bool:
        """Whether this ACE (if held by a directory) would propagate to a
        child of the given kind, per its inheritance flags."""
        if is_dir:
            return self.inherit_to_subfolders
        return self.inherit_to_files


@dataclass(frozen=True)
class FileACL:
    owner: str
    group: str | None
    aces: tuple[ACE, ...]


class Anomaly:
    ORPHANED_SID = "orphaned_sid"
    EXPLICIT_DENY = "explicit_deny_ace"
    BROKEN_INHERITANCE = "broken_inheritance"


@dataclass
class ManifestEntry:
    path: str
    is_dir: bool
    size: int
    depth: int
    acl: FileACL
    anomalies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "is_dir": self.is_dir,
            "size": self.size,
            "depth": self.depth,
            "anomalies": list(self.anomalies),
            "acl": {
                "owner": self.acl.owner,
                "group": self.acl.group,
                "aces": [
                    {
                        "identity": ace.identity,
                        "rights": sorted(ace.rights),
                        "allow": ace.allow,
                        "inherited": ace.inherited,
                        "inherit_to_files": ace.inherit_to_files,
                        "inherit_to_subfolders": ace.inherit_to_subfolders,
                        "inherit_only": ace.inherit_only,
                        "no_propagate": ace.no_propagate,
                    }
                    for ace in self.acl.aces
                ],
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> ManifestEntry:
        acl_data = data["acl"]
        aces = tuple(
            ACE(
                identity=a["identity"],
                rights=frozenset(a["rights"]),
                allow=a["allow"],
                inherited=a["inherited"],
                inherit_to_files=a.get("inherit_to_files", False),
                inherit_to_subfolders=a.get("inherit_to_subfolders", False),
                inherit_only=a.get("inherit_only", False),
                no_propagate=a.get("no_propagate", False),
            )
            for a in acl_data["aces"]
        )
        return cls(
            path=data["path"],
            is_dir=data["is_dir"],
            size=data["size"],
            depth=data["depth"],
            acl=FileACL(owner=acl_data["owner"], group=acl_data.get("group"), aces=aces),
            anomalies=list(data.get("anomalies", [])),
        )
