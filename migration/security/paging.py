"""The page-immediately hook -- the funnel every "page now" event in the
system goes through, whether it's an authentication failure here in
Phase 8 or the other three page-immediately conditions Phase 9 defines
(a new extra_grant, a stuck retry, an auto-fallback mode change).

Always logs at CRITICAL on a distinctly-named logger so it's trivially
filterable even with no channel configured. `channel` is optional and
caller-supplied -- this module stays free of any dependency on a
specific notification provider.

Two channel shapes are supported. A plain `NotificationChannel`
(`.send(message)`, e.g. `SlackWebhookChannel`) gets a flat text
message -- no dedup of its own, so `monitoring/page_immediately.py`'s
`AlertLog` table dedups on that path. A `FiringChannel`
(`.fire(alertname, subject_key, summary, severity)`, e.g.
`AlertmanagerChannel`) gets structured, labeled alerts -- Alertmanager
dedups by label fingerprint on its own, so `AlertLog` is bypassed
entirely on that path. `page_immediately` picks whichever the given
channel actually implements.
"""

from __future__ import annotations

import logging
from typing import Protocol

page_logger = logging.getLogger("migration.security.page_immediately")


class NotificationChannel(Protocol):
    def send(self, message: str) -> None: ...


class FiringChannel(Protocol):
    def fire(self, alertname: str, subject_key: str, summary: str, severity: str) -> None: ...


class AuthenticationError(OSError):
    """Raised by a transport/secrets client when a call fails because of
    invalid or expired credentials, as distinct from an ordinary
    transient I/O failure -- callers page on this, not just retry."""


def page_immediately(
    event: str,
    detail: str,
    channel: NotificationChannel | FiringChannel | None = None,
    *,
    subject_key: str = "",
    severity: str = "critical",
) -> None:
    page_logger.critical("PAGE: %s -- %s", event, detail)
    if channel is None:
        return
    if hasattr(channel, "fire"):
        channel.fire(alertname=event, subject_key=subject_key, summary=detail, severity=severity)
    else:
        channel.send(f"🚨 PAGE: {event} -- {detail}")
