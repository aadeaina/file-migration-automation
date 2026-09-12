"""The credential-fetch pattern (Phase 8), used consistently by all
three adapters: each cloud has its own Vault path and its own scoped
service identity -- no shared "migration" credential across clouds.

`ScopedCredential` is fetched fresh inside the activity that needs it
and passed directly into the same-process call that uses it (e.g. as
subprocess environment variables). It is never put on a long-lived
object, never returned from an activity, and never appears in any
dataclass that crosses the Temporal activity boundary -- that boundary
is exactly what gets recorded in workflow history, so keeping
credentials out of it is what makes "no secret ever appears in
workflow history" true by construction rather than by convention.
"""

from __future__ import annotations

from dataclasses import dataclass

from migration.models import DestCloud
from migration.security.secrets import SecretsClient

# One Vault path and one scoped service identity per cloud -- a
# credential fetched for AWS can never be reused against Azure or GCP,
# and a compromised AWS credential's blast radius is scoped to the AWS
# adapter's own least-privilege IAM role.
VAULT_PATH_BY_CLOUD = {
    DestCloud.AWS: "migration/aws",
    DestCloud.AZURE: "migration/azure",
    DestCloud.GCP: "migration/gcp",
}

SERVICE_IDENTITY_BY_CLOUD = {
    DestCloud.AWS: "svc-migration-aws",
    DestCloud.AZURE: "svc-migration-azure",
    DestCloud.GCP: "svc-migration-gcp",
}


@dataclass(frozen=True)
class ScopedCredential:
    service_identity: str
    env: dict[str, str]


def fetch_scoped_credential(dest_cloud: str, secrets_client: SecretsClient) -> ScopedCredential:
    path = VAULT_PATH_BY_CLOUD[dest_cloud]
    secret_data = secrets_client.get_secret(path)
    return ScopedCredential(
        service_identity=SERVICE_IDENTITY_BY_CLOUD[dest_cloud], env=secret_data
    )
