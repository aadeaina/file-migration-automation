import json
from unittest.mock import MagicMock, patch

import pytest

from migration.monitoring.alertmanager import AlertmanagerChannel


def test_fire_posts_labeled_alert_to_v2_api():
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__.return_value = mock_response

    with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
        channel = AlertmanagerChannel(base_url="http://alertmanager.test:9093")
        channel.fire("extra_grant", "42", "DOMAIN\\jane has extra access", severity="critical")

    request = mock_urlopen.call_args[0][0]
    assert request.full_url == "http://alertmanager.test:9093/api/v2/alerts"
    body = json.loads(request.data)
    assert body == [
        {
            "labels": {"alertname": "extra_grant", "subject": "42", "severity": "critical"},
            "annotations": {"summary": "DOMAIN\\jane has extra access"},
        }
    ]


def test_fire_never_sets_endsat_so_alertmanager_owns_auto_resolve():
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__.return_value = mock_response

    with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
        channel = AlertmanagerChannel(base_url="http://alertmanager.test:9093")
        channel.fire("stuck_retry", "7", "file stuck", severity="warning")

    body = json.loads(mock_urlopen.call_args[0][0].data)
    assert "endsAt" not in body[0]


def test_fire_raises_on_http_error_status():
    mock_response = MagicMock()
    mock_response.status = 500
    mock_response.__enter__.return_value = mock_response

    with patch("urllib.request.urlopen", return_value=mock_response):
        channel = AlertmanagerChannel(base_url="http://alertmanager.test:9093")
        with pytest.raises(OSError):
            channel.fire("extra_grant", "1", "x")


def test_missing_url_raises_clearly():
    import os

    env = dict(os.environ)
    env.pop("ALERTMANAGER_URL", None)
    with patch.dict(os.environ, env, clear=True), pytest.raises(KeyError):
        AlertmanagerChannel()
