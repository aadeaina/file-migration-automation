"""The page-immediately hook -- the funnel every "page now" event in the
system goes through, whether it's an authentication failure here in
Phase 8 or the other three page-immediately conditions Phase 9 defines
(a new extra_grant, a stuck retry, an auto-fallback mode change).

Always logs at CRITICAL on a distinctly-named logger so it's trivially
filterable even with no channel configured. `channel` (a Phase 9
`NotificationChannel`) is optional and caller-supplied -- this module
stays free of any dependency on a specific notification provider.
"""

from __future__ import annotations

import logging
from typing import Protocol

page_logger = logging.getLogger("migration.security.page_immediately")


class NotificationChannel(Protocol):
    def send(self, message: str) -> None: ...


class AuthenticationError(OSError):
    """Raised by a transport/secrets client when a call fails because of
    invalid or expired credentials, as distinct from an ordinary
    transient I/O failure -- callers page on this, not just retry."""


def page_immediately(event: str, detail: str, channel: NotificationChannel | None = None) -> None:
    page_logger.critical("PAGE: %s -- %s", event, detail)
    if channel is not None:
        channel.send(f"🚨 PAGE: {event} -- {detail}")
