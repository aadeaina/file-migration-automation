"""Whether a subtree's shadow sync is stable enough to cut over.

`ShadowSyncStatus.consecutive_stable_checks` counts consecutive passing
checks but doesn't itself know the check interval, so translating
`lag_stability_window_minutes` into "how many checks" needs an assumed
cadence -- documented here rather than silently baked in. `LagStabilityCheck`
rows are written on some fixed interval by the (separately running)
`ContinuousShadowSyncWorkflow`; this assumes 5 minutes, a reasonable
default that can be changed here if that workflow's actual cadence differs.
"""

from __future__ import annotations

import math

from migration.models import CutoverConfig, ShadowSyncStatus

ASSUMED_CHECK_INTERVAL_MINUTES = 5


def required_consecutive_checks(lag_stability_window_minutes: int) -> int:
    return max(1, math.ceil(lag_stability_window_minutes / ASSUMED_CHECK_INTERVAL_MINUTES))


def is_scope_lag_stable(scope: str, config: CutoverConfig) -> bool:
    """A scope's lag is stable when every subtree currently being
    shadow-synced under it reports lag at or below the configured
    threshold, held for the configured stability window. No shadow sync
    data at all means "not yet stable" -- cutover can't proceed on the
    absence of evidence."""
    if scope == "global":
        statuses = ShadowSyncStatus.objects.all()
    else:
        statuses = ShadowSyncStatus.objects.filter(subtree_path__startswith=scope)

    statuses = list(statuses)
    if not statuses:
        return False

    required_checks = required_consecutive_checks(config.lag_stability_window_minutes)
    return all(
        s.last_sync_lag_seconds <= config.max_acceptable_lag_seconds
        and s.consecutive_stable_checks >= required_checks
        for s in statuses
    )
