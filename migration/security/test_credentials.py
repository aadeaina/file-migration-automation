import pytest

from migration.models import DestCloud
from migration.security.credentials import (
    SERVICE_IDENTITY_BY_CLOUD,
    VAULT_PATH_BY_CLOUD,
    fetch_scoped_credential,
)
from migration.security.testing import FailingSecretsClient, FakeSecretsClient


def test_each_cloud_has_its_own_vault_path_and_service_identity():
    paths = set(VAULT_PATH_BY_CLOUD.values())
    identities = set(SERVICE_IDENTITY_BY_CLOUD.values())
    assert len(paths) == 3
    assert len(identities) == 3


def test_fetches_from_the_correct_cloud_specific_path():
    client = FakeSecretsClient(
        {
            "migration/aws": {"access_key": "AKIA-fake"},
            "migration/gcp": {"service_account_json": "{...}"},
        }
    )

    aws_cred = fetch_scoped_credential(DestCloud.AWS, client)
    assert aws_cred.service_identity == "svc-migration-aws"
    assert aws_cred.env == {"access_key": "AKIA-fake"}
    assert client.calls == ["migration/aws"]

    gcp_cred = fetch_scoped_credential(DestCloud.GCP, client)
    assert gcp_cred.service_identity == "svc-migration-gcp"
    assert client.calls == ["migration/aws", "migration/gcp"]


def test_no_credential_is_shared_across_clouds():
    client = FakeSecretsClient(
        {
            "migration/aws": {"token": "aws-token"},
            "migration/azure": {"token": "azure-token"},
        }
    )
    aws_cred = fetch_scoped_credential(DestCloud.AWS, client)
    azure_cred = fetch_scoped_credential(DestCloud.AZURE, client)

    assert aws_cred.env != azure_cred.env
    assert aws_cred.service_identity != azure_cred.service_identity


def test_secrets_client_failure_propagates_rather_than_returning_empty():
    client = FailingSecretsClient(ConnectionError("vault unreachable"))
    with pytest.raises(ConnectionError):
        fetch_scoped_credential(DestCloud.AWS, client)
