"""Phase 9 deliverable: a working alert firing end to end for the
extra_grant case.

Seeds a real extra_grant via Phase 6's `diff_file` (not a hand-built
`EffectivePermissionDiff` row -- this exercises the actual diff
computation, not just the alert layer in isolation), runs the
page-immediately check through the real `SlackWebhookChannel`, and
confirms an actual HTTP POST reaches a local server standing in for
Slack's webhook endpoint.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from django.test import TestCase

from migration.cloud_adapters.testing import FakeDestACLReader
from migration.diffing.run_diff import diff_file
from migration.discovery.types import ACE, FileACL
from migration.models import DestCloud, MigrationFileStatus, MismatchType
from migration.monitoring.channels import SlackWebhookChannel
from migration.monitoring.page_immediately import check_for_new_extra_grants

SOURCE_PATH = "/onprem/share/payroll.csv"
DEST_PATH = "/fsx/share/payroll.csv"


class _CapturingSlackHandler(BaseHTTPRequestHandler):
    received_bodies: list[bytes] = []

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        self.received_bodies.append(self.rfile.read(length))
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass  # keep test output quiet


class ExtraGrantAlertFiresEndToEndTest(TestCase):
    def setUp(self):
        _CapturingSlackHandler.received_bodies = []
        self.server = HTTPServer(("127.0.0.1", 0), _CapturingSlackHandler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server_thread.join, timeout=2)

    def test_extra_grant_diff_triggers_a_real_slack_post(self):
        row = MigrationFileStatus.objects.create(
            source_path=SOURCE_PATH, dest_cloud=DestCloud.AWS, dest_path=DEST_PATH
        )
        # Source grants nothing to "contractor"; dest somehow does --
        # exactly the security-regression shape extra_grant exists for.
        source_acl = FileACL(owner="", group=None, aces=())
        dest_acl = FileACL(
            owner="",
            group=None,
            aces=(
                ACE(
                    identity="DOMAIN\\contractor",
                    rights=frozenset({"ReadData", "WriteData"}),
                    allow=True,
                    inherited=False,
                ),
            ),
        )
        source_reader = FakeDestACLReader({SOURCE_PATH: source_acl})
        dest_reader = FakeDestACLReader({DEST_PATH: dest_acl})

        created_diffs = diff_file(row, source_reader, dest_reader)
        self.assertEqual(
            [d.mismatch_type for d in created_diffs], [MismatchType.EXTRA_GRANT]
        )

        webhook_url = f"http://127.0.0.1:{self.server.server_port}/services/fake"
        channel = SlackWebhookChannel(webhook_url=webhook_url)

        alerted = check_for_new_extra_grants(channel)

        self.assertEqual(len(alerted), 1)
        self.assertEqual(len(_CapturingSlackHandler.received_bodies), 1)
        payload = json.loads(_CapturingSlackHandler.received_bodies[0])
        self.assertIn("DOMAIN\\contractor", payload["text"])
        self.assertIn("PAGE", payload["text"])
        self.assertIn(SOURCE_PATH, payload["text"])

        # Re-running the check doesn't fire a second POST for the same diff.
        check_for_new_extra_grants(channel)
        self.assertEqual(len(_CapturingSlackHandler.received_bodies), 1)
