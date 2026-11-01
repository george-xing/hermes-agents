"""cards_browser against a fake browser_exec harness: no browser, bank or secrets."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "bin"))
import cards_browser as cb  # noqa: E402
import cards_journal  # noqa: E402
import cards_preflight  # noqa: E402


def starts(expr, template):
    return expr.startswith(template.split("%s")[0])


class FakePage:
    """Just enough of a bank tab to answer the helper's JS by template."""

    def __init__(self, issuer, tabs=None):
        self.issuer = issuer
        self.cfg = cb.BANKS[issuer]
        self.now = 0.0
        self.tabs = tabs if tabs is not None else [{"target_id": "T1", "url": self.cfg["destination"]}]
        self.attached = None
        self.activated = []
        self.origin = self.cfg["login_origin"]
        self.ready_after = 0          # READY polls before the fields count as fillable
        self.ready_on_reveal = False
        self.ready_calls = []
        self.username_ok = True
        self.password_ok = True
        self.after_submit = "offers_ready"
        self.submits = []
        self.journal_at_submit = None
        self.offers = False           # offers_ready answer before login
        self.summary_calls = 0
        # Amex
        self.amex_tiles = []          # [(text, verifies)]
        self.amex_counter = 0
        self.amex_clicks = []
        self.amex_last = None
        # Chase
        self.chase_cards = {}         # label -> [(id, text, verifies)]
        self.selected = None
        self.force_selected = None    # Hub shows this card regardless (mismatch)
        self.chase_clicks = []
        self.location = ""
        # Sign-out
        self.sign_out_control = True
        self.after_sign_out = {"origin": self.origin, "body": "Log in", "password_input": True}
        self.signed_out = False

    def harness(self, journal_path=None):
        self.journal_path = journal_path
        return {"js": self.js, "switch_tab": self.switch_tab, "current_tab": self.current_tab,
                "list_tabs": lambda: list(self.tabs), "goto_url": self.goto_url, "new_tab": self.new_tab}

    def sleep(self, seconds):
        self.now += seconds

    def clock(self):
        return self.now

    def switch_tab(self, target, activate=False):
        self.attached = target
        self.activated.append(activate)

    def current_tab(self):
        return {"target_id": self.attached}

    def new_tab(self, url):
        self.tabs.append({"target_id": "NEW", "url": url})
        self.attached = "NEW"
        return "NEW"

    def goto_url(self, url):
        self.location = url

    def js(self, expr):
        cfg = self.cfg
        if expr == "location.origin":
            return self.origin
        if expr == cfg["offers_ready"]:
            return self.offers or bool(self.submits and self.after_submit in ("offers_ready", "signed_in"))
        if starts(expr, cb.READY_JS):
            reveal = expr.rstrip().endswith("true)")
            self.ready_calls.append(reveal)
            ready = (len(self.ready_calls) > self.ready_after) if not reveal else self.ready_on_reveal
            return {"ready": ready, "found": [True, True], "fields": [ready, ready], "revealed": int(reveal)}
        if starts(expr, cb.SET_USERNAME_JS):
            return True
        if starts(expr, cb.CHECK_FIELDS_JS):
            return {"username": self.username_ok, "password": self.password_ok}
        if starts(expr, cb.CLICK_JS):
            self.journal_at_submit = json.loads(Path(self.journal_path).read_text())
            self.submits.append(expr)
            return True
        if starts(expr, cb.LOGIN_PROBE_JS):
            outcome = self.after_submit
            return {"path": "/two-step-verification/verify" if outcome == "mfa" else "/x",
                    "ready": outcome in ("offers_ready", "signed_in"), "captcha": outcome == "captcha",
                    "body": {"rejected": "The User ID or Password is incorrect"}.get(outcome, "Loading")}
        if expr == cb.MFA_DETAILS_JS:
            return {"heading": "Choose a Method to Verify Your Identity", "path": "/two-step-verification/verify",
                    "options": ["Text Message", "Email"], "remember": True}
        if expr == cb.SUMMARY_JS:
            self.summary_calls += 1
            return {"origin": self.origin, "path": "/", "title": "Card 4111111111111111"}
        if starts(expr, cb.SIGN_OUT_CLICK_JS):
            self.signed_out = self.sign_out_control
            return self.sign_out_control
        if expr == cb.SIGN_OUT_PROBE_JS:
            return self.after_sign_out if self.signed_out else {"origin": self.origin, "body": "Offers", "password_input": False}
        return self.amex_js(expr) if self.issuer == "amex" else self.chase_js(expr)

    def amex_js(self, expr):
        if "simple_switcher_display_label" in expr:
            return "Platinum Card® ••••03005"
        if starts(expr, cb.AMEX_NEXT_JS):
            skip = json.loads("[" + expr[expr.rindex("})(") + 3:-1] + "]")[1]
            for i, (text, _) in enumerate(self.amex_tiles):
                key = "Platinum Card® ••••03005::" + "::".join(cards_journal.parse_amex_offer(text).values())
                if key not in skip:
                    self.amex_last = i
                    return {"marker": f"m{i}", "text": text, "added": self.amex_counter}
            return None
        if starts(expr, cb.AMEX_MARK_JS):
            return True
        if starts(expr, cb.AMEX_CLICK_JS):
            i = self.amex_last
            self.amex_clicks.append(i)
            text, verifies = self.amex_tiles[i]
            if verifies:
                self.amex_counter += 1
                self.amex_tiles.pop(i)
            return True
        if starts(expr, cb.AMEX_VERIFY_JS):
            return {"added": self.amex_counter}
        raise AssertionError("unexpected js: " + expr[:80])

    def chase_js(self, expr):
        if expr == "location.href":
            return self.location
        if "select-credit-card-account" in expr and "innerText || ''" in expr and expr.startswith("document"):
            return self.selected or ""
        if expr.startswith("document.querySelector") and expr.endswith("?.click()"):
            return None
        if expr.startswith("[...document.querySelectorAll") and ".map(" in expr:
            return list(self.chase_cards)
        if expr.startswith("[...document.querySelectorAll") and ".find(" in expr:
            label = json.loads(expr[expr.rindex("=== ") + 4:expr.rindex(")?.click()")])
            self.selected = label
            self.location = "https://secure.chase.com/web/auth/dashboard#/dashboard/merchantOffers/offer-hub?accountId=1"
            return None
        if starts(expr, cb.CHASE_HUB_JS):
            skip = json.loads("[" + expr[expr.rindex("})(") + 3:-1] + "]")[3]
            shown = self.force_selected or self.selected
            tiles = [t for t in self.chase_cards.get(shown, []) if t[0] not in skip]
            return {"url": self.location, "selected": shown, "total": len(self.chase_cards.get(shown, [])) or 0,
                    "tile": {"id": tiles[0][0], "text": tiles[0][1]} if tiles else None}
        if expr.startswith("((id) => { const e = document.getElementById(id)"):
            offer_id = json.loads(expr[expr.rindex("})(") + 3:-1])
            self.chase_clicks.append(offer_id)
            tiles = self.chase_cards[self.selected]
            tile = next(t for t in tiles if t[0] == offer_id)
            if tile[2]:
                tiles.remove(tile)
                self.location = ("https://secure.chase.com/web/auth/dashboard#/dashboard/merchantOffers/"
                                 f"offer-activated/{offer_id}")
                self.chase_body = f"Added to card\nPay with {self.selected}"
            else:
                self.chase_body = "Loading"
            return True
        if expr == "({url: location.href, body: document.body?.innerText || ''})":
            return {"url": self.location, "body": getattr(self, "chase_body", "")}
        if starts(expr, cb.CHASE_TILE_DONE_JS):
            return False
        raise AssertionError("unexpected js: " + expr[:80])


class BankTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "hermes-runs/2026-11-02T08-00-00Z.json"
        self.journal = cards_journal.Journal(self.path)
        self.journal.prepare(required_issuers=("chase", "amex", "bofa"))

    def bank(self, page, **kw):
        return cb.Bank(page.issuer, self.path, harness=page.harness(self.path),
                       sleep=page.sleep, clock=page.clock, rand=lambda a, b: a, **kw)

    def logged_in(self, issuer):
        page = FakePage(issuer)
        bank = self.bank(page)
        self.assertEqual(bank.attach()["status"], "attached")
        self.assertEqual(bank.prepare_login("user")["status"], "ready_for_vault_fill")
        self.assertEqual(bank.submit_login("user", "T1")["status"], "offers_ready")
        return page, bank

    def data(self):
        return json.loads(self.path.read_text())


class LoginTests(BankTestCase):
    def test_submit_refuses_without_prepare(self):
        page = FakePage("amex")
        bank = self.bank(page)
        bank.attach()
        result = bank.submit_login("user", "T1")
        self.assertEqual(result["status"], "refused")
        self.assertEqual(page.submits, [])
        self.assertEqual(self.data()["issuers"]["amex"]["login_submissions"], 0)

    def test_submit_reserves_before_the_single_click_and_refuses_a_second_call(self):
        page, bank = self.logged_in("amex")
        self.assertEqual(len(page.submits), 1)
        self.assertEqual(page.journal_at_submit["issuers"]["amex"]["login_submissions"], 1)
        again = self.bank(page).submit_login("user", "T1")
        self.assertEqual(again["status"], "refused")
        self.assertEqual(len(page.submits), 1)

    def test_submit_refuses_mismatched_target_username_or_empty_password(self):
        for change in ("target", "username", "password"):
            with self.subTest(change=change):
                self.setUp()
                page = FakePage("chase")
                bank = self.bank(page)
                bank.attach()
                bank.prepare_login("user")
                page.username_ok = change != "username"
                page.password_ok = change != "password"
                result = bank.submit_login("user", "OTHER" if change == "target" else "T1")
                self.assertEqual(result["status"], "refused")
                self.assertEqual(page.submits, [])

    def test_submit_waits_for_login_spacing_before_reserving(self):
        self.journal.issuer("chase", status="completed", sign_out="verified")
        with self.journal.transaction() as data:
            data["issuers"]["chase"]["submission_recorded_at"] = cards_journal.now()
        page = FakePage("amex")
        bank = self.bank(page)
        bank.attach()
        bank.prepare_login("user")
        # The helper sleeps out the spacing first; the fake clock does not move real time, so the
        # journal still refuses, and nothing is reserved or clicked.
        self.assertEqual(bank.submit_login("user", "T1")["status"], "refused")
        self.assertGreaterEqual(page.now, 60)
        self.assertEqual(page.submits, [])
        self.assertEqual(self.data()["issuers"]["amex"]["login_submissions"], 0)

    def test_prepare_returns_vault_target_and_frame(self):
        page = FakePage("chase")
        bank = self.bank(page)
        bank.attach()
        result = bank.prepare_login("sentinel-identifier")
        self.assertEqual((result["target_id"], result["frame_selector"]), ("T1", "#logonbox"))
        self.assertEqual(page.location, cb.BANKS["chase"]["destination"])
        self.assertNotIn("sentinel-identifier", json.dumps(self.data()) + json.dumps(result))

    def test_fill_ready_activates_and_polls_without_reveal(self):
        page = FakePage("chase")
        page.ready_after = 2
        bank = self.bank(page)
        bank.attach()
        self.assertEqual(bank.prepare_login("user")["status"], "ready_for_vault_fill")
        self.assertIn(True, page.activated)
        self.assertEqual(page.ready_calls, [False, False, False])

    def test_fade_in_reveal_is_bofa_only(self):
        page = FakePage("bofa")
        page.ready_after, page.ready_on_reveal = 99, True
        bank = self.bank(page)
        bank.attach()
        self.assertEqual(bank.prepare_login("user")["status"], "ready_for_vault_fill")
        self.assertEqual(page.ready_calls, [False] * 16 + [True])
        page = FakePage("amex")
        page.ready_after, page.ready_on_reveal = 99, True
        bank = self.bank(page)
        bank.attach()
        self.assertEqual(bank.prepare_login("user")["status"], "blocked_fields_hidden")
        self.assertEqual(self.data()["issuers"]["amex"]["status"], "blocked_fields_hidden")

    def test_mfa_rejected_and_captcha_block_the_issuer(self):
        for outcome, status in (("mfa", "blocked_mfa"), ("rejected", "blocked_rejected"), ("captcha", "blocked_captcha")):
            with self.subTest(outcome=outcome):
                self.setUp()
                page = FakePage("amex")
                page.after_submit = outcome
                bank = self.bank(page)
                bank.attach()
                bank.prepare_login("user")
                self.assertEqual(bank.submit_login("user", "T1")["status"], outcome)
                self.assertEqual(self.data()["issuers"]["amex"]["status"], status)
                self.assertEqual(len(page.submits), 1)

    def test_mfa_details_are_recorded_for_the_roundup(self):
        page = FakePage("amex")
        page.after_submit = "mfa"
        bank = self.bank(page)
        bank.attach()
        bank.prepare_login("user")
        bank.submit_login("user", "T1")
        text = cb.roundup(self.path)
        self.assertIn("Amex needs a one-time verification; see thread.", text)
        self.assertIn("Text Message, Email", text)
        self.assertIn("Tick 'remember this device'", text)

    def test_unclear_login_after_45_seconds(self):
        page = FakePage("chase")
        page.after_submit = "loading"
        bank = self.bank(page)
        bank.attach()
        bank.prepare_login("user")
        result = bank.submit_login("user", "T1")
        self.assertEqual(result["status"], "needs_judgment")
        self.assertGreaterEqual(page.now, cb.LOGIN_POLL_SECONDS)
        self.assertEqual(len(page.submits), 1)

    def test_budget_gate_skips_before_navigation(self):
        page = FakePage("bofa")
        bank = self.bank(page, turn=cards_journal.MAX_TURNS - 5)
        bank.attach()
        self.assertEqual(bank.prepare_login("user")["status"], "skipped_budget")
        self.assertEqual(page.location, "")


class AttachTests(BankTestCase):
    def test_ambiguous_tabs_need_judgment_and_prefer_previous_journal_tab(self):
        tabs = [{"target_id": "A", "url": "https://secure.chase.com/x"},
                {"target_id": "B", "url": "https://www.chase.com/logout"},
                {"target_id": "Z", "url": "https://example.com/"}]
        page = FakePage("chase", tabs=tabs)
        self.assertEqual(self.bank(page).attach()["status"], "needs_judgment")
        old = cards_journal.Journal(self.path.parent / "2026-11-01T08-00-50Z.json")
        old.prepare()
        old.issuer("chase", tab_id="B")
        result = self.bank(page).attach()
        self.assertEqual((result["status"], result["target_id"]), ("attached", "B"))

    def test_absent_tab_is_created_once(self):
        page = FakePage("amex", tabs=[{"target_id": "Z", "url": "https://example.com/"}])
        self.assertTrue(self.bank(page).attach()["created"])
        self.assertFalse(self.bank(page).attach()["created"])

    def test_selector_override_is_recorded_and_flagged(self):
        page = FakePage("amex")
        bank = self.bank(page, overrides={"username": "#newUserId"})
        bank.attach()
        self.assertEqual(self.data()["issuers"]["amex"]["selector_overrides"][0]["value"], "#newUserId")
        self.assertIn("selector override username=#newUserId", cb.roundup(self.path))
        with self.assertRaises(ValueError):
            self.bank(page, overrides={"nonsense": "x"})

    def test_needs_judgment_summary_masks_digits(self):
        page = FakePage("amex")
        bank = self.bank(page)
        bank.attach()
        page.origin = "https://elsewhere.example"
        result = bank.prepare_login("user")
        self.assertEqual(result["status"], "needs_judgment")
        self.assertNotIn("4111111111111111", json.dumps(result))
        self.assertIn("…1111", result["page"]["title"])


class AmexActivationTests(BankTestCase):
    def test_verifies_each_click_and_records_counts(self):
        page, bank = self.logged_in("amex")
        page.amex_tiles = [("NEW\nShop A\n10% back", True), ("Shop B\n$5 back", True)]
        result = bank.activate()
        self.assertEqual((result["status"], result["activated"]), ("exhausted", 2))
        self.assertEqual(len(self.data()["activations"]), 2)
        self.assertTrue(self.data()["issuers"]["amex"]["offers_done"])

    def test_stops_on_first_unverified_click(self):
        page, bank = self.logged_in("amex")
        page.amex_tiles = [("Shop A\n10% back", False), ("Shop B\n$5 back", True)]
        result = bank.activate()
        self.assertEqual((result["status"], result["activated"]), ("unverified", 0))
        self.assertEqual(page.amex_clicks, [0])
        self.assertTrue(self.data()["issuers"]["amex"]["pending_offer"])

    def test_never_clicks_an_attempted_key(self):
        page, bank = self.logged_in("amex")
        self.journal.reserve_offer("amex", "Platinum Card® ••••03005::Shop A::10% back")
        page.amex_tiles = [("Shop A\n10% back", True), ("Shop B\n$5 back", True)]
        result = self.bank(page).activate()
        self.assertEqual(page.amex_clicks, [1])
        self.assertEqual((result["activated"], result["uncertain"]), (1, 1))
        amex = self.data()["issuers"]["amex"]
        self.assertNotIn("offers_done", amex)
        self.assertEqual(amex["uncertain_offers"], ["Platinum Card® ••••03005::Shop A::10% back"])

    def test_deadline_stops_the_loop(self):
        page, bank = self.logged_in("amex")
        page.amex_tiles = [(f"Shop {i}\n{i}% back", True) for i in range(200)]
        result = bank.activate()
        self.assertEqual(result["status"], "deadline")
        self.assertGreater(result["activated"], 10)
        self.assertLess(result["activated"], 200)
        self.assertLessEqual(page.now, cards_journal.LOOP_SECONDS + 30)

    def test_cap_stops_before_clicking(self):
        page, bank = self.logged_in("amex")
        with self.journal.transaction() as data:
            data["issuers"]["amex"]["attempted_offers"] = [f"old::{i}" for i in range(cb.MAX_CLICKS)]
        page.amex_tiles = [("Shop A\n10% back", True)]
        self.assertEqual(self.bank(page).activate()["status"], "cap")
        self.assertEqual(page.amex_clicks, [])

    def test_activate_refuses_before_a_verified_login(self):
        page = FakePage("amex")
        bank = self.bank(page)
        bank.attach()
        self.assertEqual(bank.activate()["status"], "refused")


class ChaseActivationTests(BankTestCase):
    EXISTING, AMAZON = "Sapphire Reserve (...8423)", "Prime Visa (...0768)"

    def setUp(self):
        super().setUp()
        old = cards_journal.Journal(self.path.parent / "2026-11-01T08-00-50Z.json")
        old.prepare()
        old.card("chase", "existing", card_id=self.EXISTING, status="completed")

    def chase(self):
        page, bank = self.logged_in("chase")
        page.selected = self.AMAZON  # Chase may remember the last card.
        page.chase_cards = {self.EXISTING: [("CDLX:1", "Shop A\n10% cash back", True)],
                            self.AMAZON: [("FIGG:2", "New\nShop B\n$5 cash back\n3d left", True),
                                          ("FIGG:3", "Shop C\n5% cash back", True)]}
        return page, bank

    def test_both_cards_are_covered_with_card_specific_keys(self):
        page, bank = self.chase()
        result = bank.activate()
        self.assertEqual((result["status"], result["activated"]), ("exhausted", 3))
        chase = self.data()["issuers"]["chase"]
        self.assertEqual({s: c["status"] for s, c in chase["cards"].items()},
                         {"existing": "completed", "amazon": "completed"})
        self.assertIn(f"chase::{self.AMAZON}::FIGG:2", {a["key"] for a in self.data()["activations"]})
        self.assertTrue(chase["offers_done"])

    def test_card_mismatch_stops_before_clicking(self):
        page, bank = self.chase()
        page.force_selected = self.AMAZON
        result = bank.activate(card="existing")
        self.assertEqual(result["status"], "card_mismatch")
        self.assertEqual(page.chase_clicks, [])
        existing = self.data()["issuers"]["chase"]["cards"]["existing"]
        self.assertEqual((existing["status"], existing["scan"]), ("partial", "card_mismatch"))

    def test_unverified_chase_click_stops_and_is_never_repeated(self):
        page, bank = self.chase()
        page.chase_cards[self.EXISTING] = [("CDLX:1", "Shop A\n10% cash back", False),
                                           ("CDLX:4", "Shop D\n10% cash back", True)]
        self.assertEqual(bank.activate()["status"], "unverified")
        result = self.bank(page).activate(card="existing")
        self.assertEqual((result["status"], result["activated"], result["uncertain"]), ("exhausted", 1, 1))
        self.assertEqual(page.chase_clicks, ["CDLX:1", "CDLX:4"])
        # The uncertain click keeps the card from counting as covered.
        self.assertEqual(self.data()["issuers"]["chase"]["cards"]["existing"]["status"], "partial")


class SignOutTests(BankTestCase):
    def test_classifier(self):
        self.assertTrue(cb.signed_out({"origin": "https://www.chase.com",
                                       "body": "You have successfully signed out"}, "chase"))
        self.assertTrue(cb.signed_out({"origin": "https://www.americanexpress.com", "body": "Log In",
                                       "password_input": True}, "amex"))
        self.assertFalse(cb.signed_out({"origin": "https://www.americanexpress.com", "body": "Offers"}, "amex"))
        self.assertFalse(cb.signed_out({"origin": "https://example.com", "body": "Log In", "password_input": True}, "amex"))
        self.assertFalse(cb.signed_out({"origin": "https://secure.chase.com", "body": "Loading",
                                        "password_input": True}, "chase"))

    def test_sign_out_completes_only_after_offers(self):
        page, bank = self.logged_in("amex")
        page.amex_tiles = []
        bank.activate()
        self.assertEqual(bank.sign_out()["status"], "verified")
        amex = self.data()["issuers"]["amex"]
        self.assertEqual((amex["status"], amex["sign_out"]), ("completed", "verified"))

    def test_unverified_sign_out_is_partial(self):
        page, bank = self.logged_in("amex")
        page.after_sign_out = {"origin": page.origin, "body": "Offers", "password_input": False}
        self.assertEqual(bank.sign_out()["status"], "unverified")
        self.assertEqual(self.data()["issuers"]["amex"]["sign_out"], "unverified")

    def test_missing_control_needs_judgment(self):
        page, bank = self.logged_in("chase")
        page.sign_out_control = False
        self.assertEqual(bank.sign_out()["status"], "needs_judgment")


class RoundupTests(BankTestCase):
    def test_partial_run_starts_with_cron_failure_and_stays_under_limit(self):
        for i in range(400):
            key = f"chase::Prime Visa (...0768)::FIGG:{i}"
            self.journal.issuer("chase")
            self.journal.reserve_offer("chase", key)
            self.journal.record("chase", key, merchant=f"Merchant number {i} with a long name",
                                deal="10% cash back", card_id="Prime Visa (...0768)")
        self.journal.skip("bofa", "turn budget")
        text = cb.roundup(self.path)
        self.assertEqual(text.splitlines()[0], "[CRON_FAILURE]")
        self.assertLess(cb.utf16_len(text), cb.LIMIT)
        self.assertRegex(text, r"and \d+ more activated offers")
        self.assertIn("Total: 400 activated.", text)
        self.assertIn("BofA was skipped: not enough turns left; no login was submitted.", text)
        self.assertEqual(self.data()["status"], "partial")

    def test_completed_run_has_no_failure_marker(self):
        for name in ("chase", "amex", "bofa"):
            self.journal.issuer(name, status="completed", sign_out="verified")
        text = cb.roundup(self.path)
        self.assertFalse(text.startswith("[CRON_FAILURE]"))
        self.assertIn("nothing new", text)

    def test_calls_are_logged_with_turns(self):
        page = FakePage("amex")
        self.bank(page, turn=3).attach()
        data = self.data()
        self.assertEqual((data["calls"][-1]["method"], data["calls"][-1]["turn"], data["turns_used"]), ("attach", 3, 3))


class PreflightTests(unittest.TestCase):
    def test_closes_only_stale_running_journals_and_creates_todays(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "hermes-runs"
            now = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)
            stale = cards_journal.Journal(runs / "2026-11-01T08-00-50Z.json")
            stale.prepare(required_issuers=("chase", "amex", "bofa"))
            stale.issuer("chase")
            stale.reserve_login("chase")
            raw = json.loads(stale.path.read_text())
            raw["updated_at"] = (now - timedelta(hours=24)).isoformat()
            stale.path.write_text(json.dumps(raw))
            live = cards_journal.Journal(runs / "2026-11-02T07-50-00Z.json")
            live.prepare()
            raw = json.loads(live.path.read_text())
            raw["updated_at"] = (now - timedelta(minutes=5)).isoformat()
            live.path.write_text(json.dumps(raw))
            (runs / "legacy.json").write_text(json.dumps({"status": "running"}))
            pages = {"/json/version": {"Browser": "Chrome/1"},
                     "/json/list": [{"id": "C1", "type": "page", "url": "https://secure.chase.com/web?accountId=123456789"},
                                    {"id": "X", "type": "service_worker", "url": "https://x"}]}
            out = cards_preflight.run(runs=runs, fetch=lambda url: json.dumps(pages[url[len(cards_preflight.CDP):]]),
                                      now=now)
            self.assertTrue(out["cdp"]["ok"])
            self.assertEqual([c["journal"] for c in out["closed_orphans"]], ["2026-11-01T08-00-50Z.json"])
            self.assertEqual(json.loads(stale.path.read_text())["issuers"]["chase"]["status"], "partial")
            self.assertEqual(json.loads(live.path.read_text())["status"], "running")
            self.assertEqual(out["orphan_errors"][0]["journal"], "legacy.json")
            self.assertEqual(out["tabs"], [{"target_id": "C1", "origin": "https://secure.chase.com", "issuer": "chase"}])
            new = json.loads(Path(out["journal"]).read_text())
            self.assertEqual((new["status"], sorted(new["issuers"])), ("running", ["amex", "bofa", "chase"]))

    def test_cdp_down_still_creates_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            def down(url):
                raise OSError("refused")
            out = cards_preflight.run(runs=Path(tmp), fetch=down)
            self.assertFalse(out["cdp"]["ok"])
            self.assertIn("cdp down", out["next"])


class JournalAdditionsTests(BankTestCase):
    def test_every_partial_issuer_gets_one_failure_entry(self):
        self.journal.issuer("chase", status="completed", sign_out="verified")
        self.assertEqual(self.journal.finish(), "partial")
        self.assertEqual(self.journal.finish(), "partial")
        failures = self.data()["failures"]
        self.assertEqual(sorted(f["issuer"] for f in failures), ["amex", "bofa"])
        for name in ("amex", "bofa"):
            self.journal.issuer(name, status="completed", sign_out="verified")
        self.assertEqual(self.journal.finish(), "completed")
        self.assertTrue(all(f["resolved"] for f in self.data()["failures"]))

    def test_card_merges_scopes(self):
        self.journal.card("chase", "existing", card_id="A (...1111)", status="completed")
        self.journal.card("chase", "amazon", status="running")
        self.journal.card("chase", "amazon", status="completed")
        cards = self.data()["issuers"]["chase"]["cards"]
        self.assertEqual(cards, {"existing": {"card_id": "A (...1111)", "status": "completed"},
                                 "amazon": {"status": "completed"}})

    def test_log_call_keeps_only_small_whitelisted_detail(self):
        self.journal.log_call("amex", "activate", "deadline", turn=9, stop="deadline", password="x")
        call = self.data()["calls"][-1]
        self.assertNotIn("password", call)
        self.assertEqual(call["stop"], "deadline")


if __name__ == "__main__":
    unittest.main()
