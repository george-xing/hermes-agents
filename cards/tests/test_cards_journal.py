import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("cards_journal", Path(__file__).parents[1] / "bin/cards_journal.py")
journal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(journal)


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "hermes-runs/run.json"
        self.j = journal.Journal(self.path)
        self.j.prepare()
        self.j.issuer("chase", tab_id="test")

    def test_login_reservation_survives_restart(self):
        self.j.reserve_login("chase")
        with self.assertRaises(ValueError):
            journal.Journal(self.path).reserve_login("chase")

    def test_uncertain_click_cannot_be_repeated(self):
        self.j.reserve_offer("chase", "chase::example")
        with self.assertRaises(ValueError):
            journal.Journal(self.path).reserve_offer("chase", "chase::example")
        self.assertEqual(self.j.finish(), "partial")

    def test_record_is_idempotent_and_omits_unknown_fields(self):
        for _ in range(2):
            self.j.record("chase", "chase::example", merchant="Example", password="not-a-real-secret")
        data = json.loads(self.path.read_text())
        self.assertEqual(len(data["activations"]), 1)
        self.assertNotIn("password", data["activations"][0])
        self.assertEqual(len(json.loads((Path(self.tmp.name) / "chase-activated.json").read_text())), 1)

    def test_persistence_failure_retains_canonical_verified_record(self):
        (Path(self.tmp.name) / "chase-activated.json").write_text("broken")
        with self.assertRaises(ValueError):
            self.j.record("chase", "chase::example", merchant="Example")
        self.assertEqual(len(json.loads(self.path.read_text())["activations"]), 1)

    def test_completion_requires_both_issuers_and_signout(self):
        self.j.issuer("chase", status="completed", sign_out="verified")
        self.assertEqual(self.j.finish(), "partial")
        self.j.issuer("amex", status="completed", sign_out="verified")
        self.assertEqual(self.j.finish(), "completed")

    def test_failed_transaction_keeps_previous_document(self):
        before = self.path.read_bytes()
        with self.assertRaises(RuntimeError), self.j.transaction() as data:
            data["status"] = "completed"
            raise RuntimeError("interrupted")
        self.assertEqual(self.path.read_bytes(), before)

    def test_new_workflow_requires_bofa_even_before_login(self):
        self.j.prepare(required_issuers=("chase", "amex", "bofa"))
        for name in ("chase", "amex"):
            self.j.issuer(name, status="completed", sign_out="verified")
        self.assertEqual(self.j.finish(), "partial")
        self.j.prepare(required_issuers=("chase", "amex"))
        self.assertEqual(self.j.finish(), "partial")
        self.j.issuer("bofa", status="completed", sign_out="verified")
        self.assertEqual(self.j.finish(), "completed")

    def test_same_chase_offer_on_two_cards_is_recorded_separately(self):
        for card in ("existing-1234", "amazon-5678"):
            key = f"chase::{card}::offer-1"
            self.j.reserve_offer("chase", key)
            self.j.record("chase", key, card_id=card, offer_id="offer-1")
        data = json.loads(self.path.read_text())
        self.assertEqual(len(data["activations"]), 2)
        self.assertEqual({a["card_id"] for a in data["activations"]}, {"existing-1234", "amazon-5678"})

    def test_amazon_card_coverage_is_required_for_completion(self):
        for name in ("chase", "amex"):
            self.j.issuer(name, status="completed", sign_out="verified")
        self.j.issuer("chase", required_cards=["existing", "amazon"], cards={"existing": {"status": "completed"}})
        self.assertEqual(self.j.finish(), "partial")
        self.j.issuer("chase", cards={name: {"status": "completed"} for name in ("existing", "amazon")})
        self.assertEqual(self.j.finish(), "completed")

    def test_bofa_pending_offer_and_signout_require_verification(self):
        for name in ("chase", "amex", "bofa"):
            self.j.issuer(name, status="completed", sign_out="verified")
        self.j.reserve_offer("bofa", "bofa::alaska-1234::offer-1")
        self.assertEqual(self.j.finish(), "partial")
        self.j.record("bofa", "bofa::alaska-1234::offer-1", merchant="Example", expires_at="2026-12-31", offer_type="card_linked")
        self.j.issuer("bofa", sign_out="unverified")
        self.assertEqual(self.j.finish(), "partial")
        self.j.issuer("bofa", sign_out="verified")
        self.assertEqual(self.j.finish(), "completed")
        self.assertTrue((Path(self.tmp.name) / "bofa-activated.json").exists())

    def test_later_verified_signout_resolves_only_its_transient_failure(self):
        with self.j.transaction() as data:
            data["failures"].append({"issuer": "chase", "reason": "sign-out not verified"})
        for name in ("chase", "amex"):
            self.j.issuer(name, status="completed", sign_out="verified")
        self.assertEqual(self.j.finish(), "completed")
        self.assertTrue(json.loads(self.path.read_text())["failures"][0]["resolved"])
        with self.j.transaction() as data:
            data["failures"].append({"issuer": "amex", "reason": "persistence gap"})
        self.assertEqual(self.j.finish(), "partial")
