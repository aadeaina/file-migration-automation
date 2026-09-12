"""Notification channels (Phase 9): a single Slack incoming webhook,
per the brief's "ask if unspecified" -- the user's choice over email or
PagerDuty, since a webhook is the simplest to fake/test without real
infra and this team doesn't yet have PagerDuty wired up.

`NullChannel` is the default everywhere a channel isn't explicitly
configured -- alerts still get logged (see `security/paging.py`), they
just don't leave the process. That's a deliberate, visible choice
rather than a silent no-op.
"""

from __future__ import annotations

import json
import os
import urllib.request

from migration.security.paging import NotificationChannel


class NullChannel:
    def send(self, message: str) -> None:
        return None


class SlackWebhookChannel:
    """Posts to a Slack incoming webhook URL. Reads `SLACK_WEBHOOK_URL`
    from the environment by default -- the URL itself is a bearer
    credential (anyone with it can post to the channel), so it's
    configured the same way any other secret is, not hardcoded."""

    def __init__(self, webhook_url: str | None = None, timeout_seconds: float = 5.0):
        self.webhook_url = webhook_url or os.environ["SLACK_WEBHOOK_URL"]
        self.timeout_seconds = timeout_seconds

    def send(self, message: str) -> None:
        body = json.dumps({"text": message}).encode("utf-8")
        request = urllib.request.Request(
            self.webhook_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            if response.status >= 400:
                raise OSError(f"Slack webhook returned HTTP {response.status}")


__all__ = ["NotificationChannel", "NullChannel", "SlackWebhookChannel"]
