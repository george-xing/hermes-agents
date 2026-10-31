import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("health", Path(__file__).parents[1] / "bin/hermes_health.py")
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)

NOW = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)


def completed():
    return {"status": "completed", "started_at": "2026-09-29T11:50:00Z", "issuers": {
        name: {"status": "completed", "sign_out": "verified"} for name in ("chase", "amex")}}


class HealthTests(unittest.TestCase):
    def job(self, **kwargs):
        return {"id": "test", "last_status": "ok", "skills": ["credit-card-offers"],
                "last_run_at": "2026-09-29T12:00:00Z", **kwargs}

    def test_partial_cards_is_failure_even_when_scheduler_says_ok(self):
        journal = completed()
        journal.update(status="partial", failures=["loop incomplete"])
        result = health.assess(self.job(), journal=journal, now=NOW)
        self.assertIn("cards_partial", result["issues"])

    def test_completed_scheduler_and_banks_are_healthy(self):
        self.assertEqual(health.assess(self.job(), journal=completed(), now=NOW)["issues"], [])

    def test_resolved_historical_failure_does_not_trigger_replay(self):
        data = completed()
        data["failures"] = [{"issuer": "chase", "reason": "sign-out not verified", "resolved": True}]
        self.assertEqual(health.card_issues(data), [])

    def test_active_run_does_not_replay_previous_failure(self):
        execution = {"status": "running", "claimed_at": "2026-09-29T11:59:00Z"}
        result = health.assess(self.job(last_status="error"), execution, now=NOW)
        self.assertTrue(result["running"])
        self.assertFalse(result["issues"])
        with self.assertRaisesRegex(ValueError, "already running"):
            health.retry_allowed(result, None, [], "a")

    def test_delivery_failure_does_not_replay_business_actions(self):
        entry = {"role": "cards", "issues": ["delivery_failed"]}
        with self.assertRaisesRegex(ValueError, "delivery"):
            health.retry_allowed(entry, completed(), [], "a")

    def test_missing_stale_and_incomplete_journals_are_detected(self):
        self.assertIn("missing_cards_journal", health.assess(self.job(), now=NOW)["issues"])
        stale = completed()
        stale["started_at"] = "2026-09-28T11:50:00Z"
        self.assertIn("cards_journal_stale", health.assess(self.job(), journal=stale, now=NOW)["issues"])
        broken = completed()
        broken["issuers"].pop("chase")
        self.assertIn("chase_incomplete", health.card_issues(broken))

    def test_latest_journal_uses_file_time_not_inconsistent_timestamp_spelling(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            old = directory / "20260928.json"
            old.write_text(json.dumps({"status": "old"}))
            fresh = directory / "2026-09-29.json"
            fresh.write_text(json.dumps({"status": "fresh"}))
            import os
            os.utime(old, (1, 1))
            self.assertEqual(health.latest_journal(directory)[1]["status"], "fresh")
            fresh.write_text("truncated")
            self.assertEqual(health.latest_journal(directory)[1]["status"], "invalid_journal")

    def test_retries_require_new_code_and_have_daily_cap(self):
        entry = {"role": "twitter", "issues": ["scheduler_failed"]}
        health.retry_allowed(entry, None, [], "a")
        with self.assertRaisesRegex(ValueError, "same code"):
            health.retry_allowed(entry, None, [{"fingerprint": "a"}], "a")
        with self.assertRaisesRegex(ValueError, "three"):
            health.retry_allowed(entry, None, [{"fingerprint": str(i)} for i in range(3)], "new")

    def test_challenges_and_uncertain_clicks_cannot_be_replayed(self):
        entry = {"role": "cards", "issues": ["cards_partial"]}
        for issuer in [{"status": "blocked_mfa"}, {"status": "rejected_login"}, {"pending_offer": "example"}]:
            with self.subTest(issuer=issuer), self.assertRaises(ValueError):
                health.retry_allowed(entry, {"issuers": {"chase": issuer}}, [], "a")

    def test_unknown_jobs_are_monitored_but_not_blindly_replayed(self):
        result = health.assess({"id": "new", "last_status": "error"}, now=NOW)
        self.assertIn("scheduler_failed", result["issues"])
        with self.assertRaisesRegex(ValueError, "Unknown"):
            health.retry_allowed(result, None, [], "a")

    def test_dependency_repair_changes_retry_fingerprint_without_source_edits(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(health, "REPO", Path(directory)), \
             patch.object(health, "HERMES", Path(directory)):
            with patch.object(health, "version", side_effect=health.PackageNotFoundError):
                missing = health.source_fingerprint("agentmail")
            with patch.object(health, "version", return_value="2.0.6"):
                repaired = health.source_fingerprint("agentmail")
                self.assertEqual(repaired, health.source_fingerprint("agentmail"))
            self.assertNotEqual(missing, repaired)

    def test_agentmail_fingerprint_tracks_repo_package_not_hermes_scripts(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as hermes, \
             patch.object(health, "REPO", Path(repo)), patch.object(health, "HERMES", Path(hermes)), \
             patch.object(health, "version", return_value="2.0.6"):
            (Path(repo) / "agentmail/bin").mkdir(parents=True)
            (Path(hermes) / "scripts").mkdir()
            before = health.source_fingerprint("agentmail")
            (Path(hermes) / "scripts/agentmail_reconcile.py").write_text("shim")
            self.assertEqual(before, health.source_fingerprint("agentmail"))
            (Path(repo) / "agentmail/bin/agentmail_reconcile.py").write_text("fixed")
            self.assertNotEqual(before, health.source_fingerprint("agentmail"))
