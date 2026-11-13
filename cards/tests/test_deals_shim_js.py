"""DEALS_SHIM_JS under node with stubbed browser APIs: the stall gate and what it records.

Skipped where node is not installed (the mini); check_deals_shim.py covers the real Chrome there.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "bin"))
import cards_browser as cb  # noqa: E402

HARNESS = r"""
const [shimPath, host, visibility, drawing] = process.argv.slice(2);
const calls = [];
globalThis.window = globalThis;
globalThis.location = {hostname: host};
globalThis.document = {visibilityState: visibility, readyState: 'loading'};
globalThis.performance = {now: () => Date.now()};
globalThis.requestAnimationFrame = (cb) => (drawing === 'yes' ? setTimeout(() => cb(Date.now()), 5) : 0);
globalThis.cancelAnimationFrame = () => {};
globalThis.IntersectionObserver = class { constructor(cb) { this.cb = cb; } observe() {} unobserve() {} disconnect() {} };
eval(require('fs').readFileSync(shimPath, 'utf8'));
const target = {getBoundingClientRect: () => ({top: 0, height: 10})};
if (window.__cardsShim) {
  new IntersectionObserver((entries) => calls.push(entries[0].isIntersecting)).observe(target);
  requestAnimationFrame(() => calls.push('frame'));
}
setTimeout(() => { console.log(JSON.stringify({shim: window.__cardsShim || null, calls})); process.exit(0); }, 400);
"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class DealsShimJsTests(unittest.TestCase):
    def run_shim(self, host="deals.merchant-rewards.com", visibility="hidden", drawing="no"):
        with tempfile.TemporaryDirectory() as tmp:
            shim, harness = Path(tmp) / "shim.js", Path(tmp) / "harness.js"
            shim.write_text(cb.DEALS_SHIM_JS)
            harness.write_text(HARNESS)
            out = subprocess.run(["node", str(harness), str(shim), host, visibility, drawing],
                                 capture_output=True, text=True, timeout=20, check=True)
        return json.loads(out.stdout)

    def test_hidden_page_is_stalled_at_once_and_reports_elements_visible(self):
        result = self.run_shim(visibility="hidden")
        self.assertEqual((result["shim"]["mode"], result["shim"]["reason"]), ("stalled", "hidden"))
        self.assertEqual(sorted(map(str, result["calls"])), ["True", "frame"])
        self.assertEqual(result["shim"]["fired"], 1)

    def test_visible_page_without_frames_is_stalled_after_the_check(self):
        result = self.run_shim(visibility="visible", drawing="no")
        self.assertEqual((result["shim"]["mode"], result["shim"]["reason"]),
                         ("stalled", "no animation frame in 100 ms"))
        # Observed and requested during the 100 ms check, then handed over when the stall was found.
        self.assertEqual(sorted(map(str, result["calls"])), ["True", "frame"])

    def test_rendering_page_stays_native(self):
        result = self.run_shim(visibility="visible", drawing="yes")
        self.assertEqual((result["shim"]["mode"], result["shim"]["reason"]), ("native", "rendering"))
        self.assertEqual(result["shim"]["fired"], 0)
        self.assertEqual(result["calls"], ["frame"])  # The native frame, not a synthetic observer entry.

    def test_other_hosts_are_untouched(self):
        self.assertEqual(self.run_shim(host="secure.bankofamerica.com")["shim"], None)


if __name__ == "__main__":
    unittest.main()
