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
