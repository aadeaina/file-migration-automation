"""Alertmanager as the dedup/grouping/silencing backend for
page-immediately alerts, replacing the hand-rolled `AlertLog` dedup
table for any channel that supports it.

The pattern: re-POST every *currently* open issue on each poll,
labeled by (alertname, subject) so repeats fingerprint identically.
Deliberately never set `endsAt` -- Alertmanager treats an alert whose
labels keep getting re-posted as still firing, and auto-resolves it on
its own `resolve_timeout` (default 5m) once we simply stop reporting
it as open. That means our code never needs to track "did this get
fixed" itself; Alertmanager's presence/absence of a fresh POST *is*
that signal. Repeat-notification suppression for an already-firing
alert (so Slack isn't spammed every poll) is Alertmanager's own
`group_interval`/`repeat_interval` config, not anything in this file.
"""

from __future__ import annotations

import json
import os
import urllib.request


class AlertmanagerChannel:
    """Posts to Alertmanager's v2 API. Reads `ALERTMANAGER_URL` from the
    environment by default (e.g. "http://alertmanager:9093")."""

    def __init__(self, base_url: str | None = None, timeout_seconds: float = 5.0):
        self.base_url = (base_url or os.environ["ALERTMANAGER_URL"]).rstrip("/")
        self.timeout_seconds = timeout_seconds

    def fire(self, alertname: str, subject_key: str, summary: str, severity: str = "critical") -> None:
        payload = [
            {
                "labels": {
                    "alertname": alertname,
                    "subject": subject_key or "none",
                    "severity": severity,
                },
                "annotations": {"summary": summary},
            }
        ]
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/v2/alerts",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            if response.status >= 400:
                raise OSError(f"Alertmanager returned HTTP {response.status}")
