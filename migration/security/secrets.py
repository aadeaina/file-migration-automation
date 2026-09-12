"""The secrets-manager abstraction (Phase 8): a single HashiCorp Vault
instance across all three clouds, per the brief's "single Vault
instance if you want one system across all three" option.

`SecretsClient` is a Protocol so activities never depend on the real
Vault client directly -- `VaultSecretsClient` is the production
implementation, `NullSecretsClient` a safe default that fetches
nothing (used where no cloud auth is actually needed, so existing
callers aren't forced to configure Vault), and `security/testing.py`
holds the fake used in tests.
"""

from __future__ import annotations

import os
from typing import Protocol


class SecretsClient(Protocol):
    def get_secret(self, path: str) -> dict[str, str]:
        """Fetch the current version of a KV secret at `path`. Raises on
        any failure to authenticate or reach Vault -- callers treat that
        as a page-immediately event (see `paging.py`), not a retryable
        no-op."""
        ...


class NullSecretsClient:
    """Fetches nothing. The default for adapters/activities that don't
    need cloud-API credentials in this environment (our transports shell
    out to CLI tools assumed to run under an already-authenticated host
    identity) -- explicit, not a silent stand-in for a real Vault."""

    def get_secret(self, path: str) -> dict[str, str]:
        return {}


class VaultSecretsClient:
    """Production implementation: HashiCorp Vault's KV v2 secrets engine.

    Reads `VAULT_ADDR`/`VAULT_TOKEN` from the environment by default --
    in production the token itself would come from the worker host's own
    identity (AppRole, Kubernetes auth, etc.), never a long-lived static
    token checked into config.
    """

    def __init__(self, addr: str | None = None, token: str | None = None, client=None):
        self._client = client or self._build_client(addr, token)

    def _build_client(self, addr: str | None, token: str | None):
        import hvac

        return hvac.Client(
            url=addr or os.environ["VAULT_ADDR"], token=token or os.environ["VAULT_TOKEN"]
        )

    def get_secret(self, path: str) -> dict[str, str]:
        response = self._client.secrets.kv.v2.read_secret_version(path=path)
        return response["data"]["data"]
