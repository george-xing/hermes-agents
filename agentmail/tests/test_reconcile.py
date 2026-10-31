import base64
import hashlib
import hmac
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
import unittest.mock

spec = importlib.util.spec_from_file_location("reconcile", Path(__file__).parents[1] / "bin/agentmail_reconcile.py")
reconcile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reconcile)

SENDER = "owner@example.com"
CUTOFF = datetime(2026, 10, 1, tzinfo=UTC)
PASSING = "dkim=pass header.i=@gmail.com; dmarc=pass header.from=gmail.com"


def message(**overrides):
    fields = {
        "labels": [],
        "timestamp": CUTOFF - timedelta(minutes=5),
        "from_": f"Owner <{SENDER}>",
        "headers": {"Authentication-Results": PASSING},
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class CandidateTests(unittest.TestCase):
    def test_authenticated_unprocessed_message_is_retried(self):
        self.assertTrue(reconcile.authenticated_candidate(message(), CUTOFF, SENDER))

    def test_skips_processed_recent_foreign_auto_and_unauthenticated(self):
        for overrides in (
            {"labels": ["Hermes-Processed"]},
            {"timestamp": CUTOFF + timedelta(minutes=1)},
            {"from_": "someone@example.org"},
            {"headers": {"Authentication-Results": PASSING, "Auto-Submitted": "auto-replied"}},
            {"headers": {"Authentication-Results": "dkim=fail"}},
        ):
            with self.subTest(overrides=overrides):
                self.assertFalse(reconcile.authenticated_candidate(message(**overrides), CUTOFF, SENDER))


class SignatureTests(unittest.TestCase):
    def test_request_carries_valid_svix_signature(self):
        key = b"synthetic-key"
        secret = "whsec_" + base64.b64encode(key).decode()
        request = reconcile.signed_request("msg-1", "inbox@example.com", secret)
        headers = {k.lower(): v for k, v in request.header_items()}
        signed = f"{headers['svix-id']}.{headers['svix-timestamp']}.".encode() + request.data
        expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
        self.assertEqual(headers["svix-signature"], "v1," + expected)
        self.assertIn(b'"inbox_id":"inbox@example.com"', request.data)


class EnvTests(unittest.TestCase):
    def test_missing_inbox_configuration_fails_loudly(self):
        with unittest.mock.patch.dict(reconcile.os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "AGENTMAIL_INBOX_ID"):
                reconcile.main()


if __name__ == "__main__":
    unittest.main()
