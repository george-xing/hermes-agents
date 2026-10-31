"""Exercise the documented native login ordering without a browser or secrets."""
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
from cards_journal import Journal


class NativeLoginExampleTests(unittest.TestCase):
    def test_both_issuers_persist_before_click_and_block_duplicate(self):
        text = (ROOT / ".hermes/skills/credit-card-offers/references/native-login.md").read_text()
        source = next(s for s in re.findall(r"```python\n(.*?)```", text, re.S)
                      if "# Submit once" in s)
        code = compile(source, "native-login-example", "exec")
        for issuer in ("chase", "amex"):
            with self.subTest(issuer=issuer), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "run.json"
                journal = Journal(path)
                journal.prepare()
                journal.issuer(issuer, tab_id="test-tab")
                clicks = []

                def js(expression):
                    data = json.loads(path.read_text())
                    self.assertEqual(data["issuers"][issuer]["login_submissions"], 1)
                    self.assertTrue(data["issuers"][issuer]["submission_recorded_at"])
                    clicks.append(expression)
                    return True

                scope = dict(journal_path=path, issuer=issuer, target_id="test-tab",
                             switch_tab=lambda target: None,
                             current_tab=lambda: {"target_id": "test-tab"}, js=js,
                             submit_selector="#observed", frame_selector="",
                             expected_origin="https://example.test")
                with patch.object(sys, "path", sys.path.copy()):
                    exec(code, scope)
                    with self.assertRaises(ValueError):
                        exec(code, scope)
                self.assertEqual(len(clicks), 1)
