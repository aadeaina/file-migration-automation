import json
from unittest.mock import MagicMock, patch

import pytest

from migration.monitoring.channels import NullChannel, SlackWebhookChannel


def test_null_channel_does_nothing():
    NullChannel().send("anything")  # should not raise


def test_slack_channel_posts_json_body_to_webhook_url():
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__.return_value = mock_response

    with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
        channel = SlackWebhookChannel(webhook_url="https://hooks.slack.test/abc")
        channel.send("hello")

    request = mock_urlopen.call_args[0][0]
    assert request.full_url == "https://hooks.slack.test/abc"
    assert json.loads(request.data) == {"text": "hello"}


def test_slack_channel_raises_on_http_error_status():
    mock_response = MagicMock()
    mock_response.status = 500
    mock_response.__enter__.return_value = mock_response

    with patch("urllib.request.urlopen", return_value=mock_response):
        channel = SlackWebhookChannel(webhook_url="https://hooks.slack.test/abc")
        with pytest.raises(OSError):
            channel.send("hello")


def test_missing_webhook_url_raises_clearly():
    import os

    env = dict(os.environ)
    env.pop("SLACK_WEBHOOK_URL", None)
    with patch.dict(os.environ, env, clear=True), pytest.raises(KeyError):
        SlackWebhookChannel()
