"""Effective-access computation (Phase 6): per identity, which access
*categories* (read/write/execute/delete/change_permissions) it actually
has -- accounting for deny precedence -- rather than a raw ACE list.

Both the NTFS side (source, and AWS/Azure destinations, which carry
ACLs over verbatim) and the NFSv4 ACE4 side (GCP destination) reduce to
the same `EffectiveAccessEntry` shape so the diff itself doesn't need
to know which cloud it's comparing against.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from migration.discovery.types import ACE
from migration.permission_mapping.types import NFSv4Ace

READ = "read"
WRITE = "write"
EXECUTE = "execute"
DELETE = "delete"
CHANGE_PERMISSIONS = "change_permissions"
CATEGORIES = (READ, WRITE, EXECUTE, DELETE, CHANGE_PERMISSIONS)

# Synchronize (NTFS) / ACE4_SYNCHRONIZE has no meaningful category and is
# intentionally left uncategorized -- it doesn't grant any of the 5 access
# categories the brief diffs at.
NTFS_RIGHT_TO_CATEGORY = {
    "ReadData": READ,
    "ReadAttributes": READ,
    "ReadExtendedAttributes": READ,
    "ReadPermissions": READ,
    "WriteData": WRITE,
    "WriteAttributes": WRITE,
    "WriteExtendedAttributes": WRITE,
    "AppendData": WRITE,
    "ExecuteFile": EXECUTE,
    "Delete": DELETE,
    "DeleteSubdirectoriesAndFiles": DELETE,
    # TakeOwnership has no dedicated category in the brief's 5-category
    # scheme; ownership control is treated as a permissions-control
    # right here, same bucket as ChangePermissions.
    "ChangePermissions": CHANGE_PERMISSIONS,
    "TakeOwnership": CHANGE_PERMISSIONS,
}

ACE4_BIT_TO_CATEGORY = {
    "ACE4_READ_DATA": READ,
    "ACE4_LIST_DIRECTORY": READ,
    "ACE4_READ_ATTRIBUTES": READ,
    "ACE4_READ_NAMED_ATTRS": READ,
    "ACE4_READ_ACL": READ,
    "ACE4_WRITE_DATA": WRITE,
    "ACE4_ADD_FILE": WRITE,
    "ACE4_APPEND_DATA": WRITE,
    "ACE4_ADD_SUBDIRECTORY": WRITE,
    "ACE4_WRITE_ATTRIBUTES": WRITE,
    "ACE4_WRITE_NAMED_ATTRS": WRITE,
    "ACE4_EXECUTE": EXECUTE,
    "ACE4_DELETE": DELETE,
    "ACE4_DELETE_CHILD": DELETE,
    "ACE4_WRITE_ACL": CHANGE_PERMISSIONS,
    "ACE4_WRITE_OWNER": CHANGE_PERMISSIONS,
}


@dataclass(frozen=True)
class EffectiveAccessEntry:
    categories: frozenset[str]
    # Whether any ALLOW grant contributing to `categories` was inherited
    # rather than explicit. Only meaningful on the NTFS side -- our NFSv4
    # ACE4 model (round-tripped through nfs4_setfacl/nfs4_getfacl) doesn't
    # carry a distinct "this ACE was inherited" bit, so GCP-side entries
    # always report False here (see `effective_access_from_ace4`).
    any_inherited: bool


EMPTY_ACCESS = EffectiveAccessEntry(categories=frozenset(), any_inherited=False)


def _reduce(
    allow_categories: dict[str, set[str]],
    deny_categories: dict[str, set[str]],
    inherited_allow: dict[str, bool],
) -> dict[str, EffectiveAccessEntry]:
    result = {}
    for identity in set(allow_categories) | set(deny_categories):
        # Deny precedence: a denied category is never granted, regardless
        # of any allow ACE also present for that identity.
        granted = allow_categories.get(identity, set()) - deny_categories.get(identity, set())
        result[identity] = EffectiveAccessEntry(
            categories=frozenset(granted),
            any_inherited=inherited_allow.get(identity, False),
        )
    return result


def effective_access_from_ntfs(aces: list[ACE]) -> dict[str, EffectiveAccessEntry]:
    allow: dict[str, set[str]] = defaultdict(set)
    deny: dict[str, set[str]] = defaultdict(set)
    inherited_allow: dict[str, bool] = defaultdict(bool)

    for ace in aces:
        categories = {NTFS_RIGHT_TO_CATEGORY[r] for r in ace.rights if r in NTFS_RIGHT_TO_CATEGORY}
        if not categories:
            continue
        if ace.allow:
            allow[ace.identity] |= categories
            if ace.inherited:
                inherited_allow[ace.identity] = True
        else:
            deny[ace.identity] |= categories

    return _reduce(allow, deny, inherited_allow)


def effective_access_from_ace4(aces: list[NFSv4Ace]) -> dict[str, EffectiveAccessEntry]:
    allow: dict[str, set[str]] = defaultdict(set)
    deny: dict[str, set[str]] = defaultdict(set)

    for ace in aces:
        categories = {ACE4_BIT_TO_CATEGORY[b] for b in ace.ace4_bits if b in ACE4_BIT_TO_CATEGORY}
        if not categories:
            continue
        if ace.allow:
            allow[ace.identity] |= categories
        else:
            deny[ace.identity] |= categories

    return _reduce(allow, deny, inherited_allow={})
