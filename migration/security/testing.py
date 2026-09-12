"""Fakes for security-module tests: no real Vault needed."""

from __future__ import annotations


class FakeSecretsClient:
    def __init__(self, secrets_by_path: dict[str, dict[str, str]]):
        self._secrets = secrets_by_path
        self.calls: list[str] = []

    def get_secret(self, path: str) -> dict[str, str]:
        self.calls.append(path)
        if path not in self._secrets:
            raise KeyError(f"no secret configured at {path}")
        return self._secrets[path]


class FailingSecretsClient:
    def __init__(self, error: Exception):
        self._error = error

    def get_secret(self, path: str) -> dict[str, str]:
        raise self._error
