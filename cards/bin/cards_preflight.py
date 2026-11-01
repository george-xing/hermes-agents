"""Cards run preflight: CDP check, orphan cleanup, today's journal, tab list. Prints JSON.

Hermes 0.21.3 runs a cron job's ``script`` before the agent and injects its
stdout into the prompt, but only from ``~/.hermes/scripts/`` (symlinks that
resolve elsewhere are refused). See docs/cron/ for the wrapper. The agent can
also call ``run()`` itself from browser_exec as its first step.

No bank navigation, credentials or clicks: it reads CDP's tab list and the
journal folder, closes stale ``running`` journals and creates the new one.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cards_journal import Journal  # noqa: E402

CDP = "http://127.0.0.1:19223"
RUNS = Path.home() / ".claude/skills/credit-card-offers/state/hermes-runs"
REQUIRED = ("chase", "amex", "bofa")
# A live run writes its journal every few seconds while it works; one silent this long is an orphan.
STALE_AFTER = timedelta(minutes=45)
ORIGINS = {
    "chase": ("https://secure.chase.com", "https://www.chase.com"),
    "amex": ("https://global.americanexpress.com", "https://www.americanexpress.com"),
    "bofa": ("https://www.bankofamerica.com", "https://secure.bankofamerica.com"),
}


def _get(path, fetch):
    return json.loads(fetch(CDP + path))


def _fetch(url):
    with urlopen(url, timeout=5) as response:
        return response.read().decode()


def close_orphans(runs, now):
    closed, errors = [], []
    for path in sorted(runs.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if data.get("status") != "running":
            continue
        stamp = data.get("updated_at") or data.get("started_at")
        try:
            age = now - datetime.fromisoformat(stamp)
        except (TypeError, ValueError):
            age = STALE_AFTER  # No usable timestamp: it cannot be a live run.
        if age < STALE_AFTER:
            continue
        try:
            closed.append({"journal": path.name, "status": Journal(path).close("orphaned run closed by preflight")})
        except (KeyError, ValueError) as exc:
            errors.append({"journal": path.name, "error": str(exc)[:120]})
    return closed, errors


def run(runs=RUNS, fetch=_fetch, now=None):
    now = now or datetime.now(timezone.utc)
    out = {"cdp": {"ok": False}}
    try:
        version = _get("/json/version", fetch)
        tabs = [t for t in _get("/json/list", fetch) if t.get("type") == "page"]
        out["cdp"] = {"ok": True, "port": 19223, "browser": version.get("Browser", "")}
    except Exception as exc:
        out["cdp"]["error"] = type(exc).__name__
        tabs = []
    runs.mkdir(parents=True, exist_ok=True, mode=0o700)
    out["closed_orphans"], errors = close_orphans(runs, now)
    if errors:
        out["orphan_errors"] = errors
    path = runs / (now.strftime("%Y-%m-%dT%H-%M-%SZ") + ".json")
    journal = Journal(path)
    journal.prepare(required_issuers=REQUIRED)
    for name in REQUIRED:
        journal.issuer(name)
    out["journal"] = str(path)
    origin = lambda url: "/".join(url.split("/", 3)[:3])
    # Origins only: paths and queries can carry account identifiers.
    out["tabs"] = [{"target_id": t["id"], "origin": origin(t.get("url", "")),
                    "issuer": next((n for n, o in ORIGINS.items() if origin(t.get("url", "")) in o), None)}
                   for t in tabs]
    out["next"] = ("cdp down: report [CRON_FAILURE] without bank actions" if not out["cdp"]["ok"]
                   else "use this journal path for every Bank(...) call")
    return out


if __name__ == "__main__":
    print(json.dumps(run(), indent=1))
