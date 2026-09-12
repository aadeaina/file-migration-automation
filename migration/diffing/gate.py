"""The sign-off gate: a query against the `sign_off_gate` view (Phase 1),
never a reimplementation of its blocking logic in Python -- the view is
the single source of truth for what blocks stage promotion.
"""

from __future__ import annotations

from migration.models import SignOffGate


def blocked_entries_for_subtree(root_path: str):
    return SignOffGate.objects.filter(file__source_path__startswith=root_path)


def is_subtree_blocked(root_path: str) -> bool:
    return blocked_entries_for_subtree(root_path).exists()
