"""Fakes for monitoring-module tests: no real Slack webhook needed."""

from __future__ import annotations


class FakeChannel:
    def __init__(self):
        self.messages: list[str] = []

    def send(self, message: str) -> None:
        self.messages.append(message)


class FailingChannel:
    def __init__(self, error: Exception):
        self._error = error

    def send(self, message: str) -> None:
        raise self._error


class FakeFiringChannel:
    """Stands in for `AlertmanagerChannel`: records every `.fire()` call
    instead of posting anywhere. Having only `.fire()` (no `.send()`) is
    deliberate -- it's what makes `page_immediately`'s `hasattr(channel,
    "fire")` duck-typing meaningful to test."""

    def __init__(self):
        self.fired: list[tuple[str, str, str, str]] = []

    def fire(self, alertname: str, subject_key: str, summary: str, severity: str) -> None:
        self.fired.append((alertname, subject_key, summary, severity))
