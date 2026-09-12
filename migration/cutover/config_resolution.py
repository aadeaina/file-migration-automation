"""Resolves which `CutoverConfig` row governs a given subtree.

`CutoverConfig.scope` is either the literal string `"global"` or a
specific subtree path (per the design doc: "lets different parts of
the estate run different cutover strategies"). The most specific
matching scope wins; `global` is the fallback.
"""

from __future__ import annotations

from migration.models import CutoverConfig


def resolve_cutover_config(subtree_path: str) -> CutoverConfig | None:
    # Any active config whose scope is "global" or an ancestor (or exact
    # match) of this subtree is a candidate; the longest scope string
    # wins as the most specific.
    candidates = [
        c
        for c in CutoverConfig.objects.filter(active=True)
        if c.scope == "global" or subtree_path.startswith(c.scope)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda c: (0 if c.scope == "global" else len(c.scope)))
