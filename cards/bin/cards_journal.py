"""Secret-free, atomic bookkeeping for Hermes's native browser workflow.

This module never opens a browser or reads credentials. Import it from
browser_exec so persistence does not depend on shell heredoc approvals.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".journal-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def parse_amex_offer(text):
    """Parse observed tile text before reserving a click (no bank actions)."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    while lines and lines[0].casefold() in {"new", "exclusive", "expiring soon"}:
        lines.pop(0)
    if len(lines) < 2 or lines[1].casefold() in {"details", "offer details", "add to card", "added to card"}:
        raise ValueError("Amex tile missing merchant/deal; inspect read-only before clicking")
    return {"merchant": lines[0], "deal": lines[1]}


def parse_chase_offer(text):
    """Parse a visible Chase tile before reserving a click."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    lines = [line for line in lines
             if line.casefold() not in {"new", "exclusive", "expiring soon"}
             and not re.fullmatch(r"\d+\s*(?:d|h|days?|hours?)\s+left", line, re.I)]
    if len(lines) < 2 or not re.search(r"(?:\$\d|\d+(?:\.\d+)?%).*\bback\b", lines[1], re.I):
        raise ValueError("Chase tile missing merchant/deal; inspect before reserving or clicking")
    return {"merchant": lines[0], "deal": lines[1]}


def chase_activation_verified(probe, offer_id, card_label):
    """Validate a small read-only DOM probe without nested JS regex escaping."""
    url = unquote(probe.get("url", ""))
    marker = "/offer-activated/"
    if marker not in url:
        return False
    actual_id = url.split(marker, 1)[1].split("?", 1)[0].split("#", 1)[0].rstrip("/")
    body = " ".join(probe.get("body", "").split()).casefold()
    expected_card = " ".join(card_label.split()).casefold()
    return (actual_id == offer_id and bool(expected_card)
            and "added to card" in body and expected_card in body)


def chase_card_selected(probe, card_label):
    """The Choose account control, not page text, proves which card the hub shows."""
    selected = " ".join((probe.get("selected") or "").split()).casefold()
    return bool(card_label) and selected == " ".join(card_label.split()).casefold()


def chase_card_hub(url):
    """Card-scoped hub URL to return to after each activation.

    The generic navKey hub reopens Chase's default card, so on 2026-11-01 every
    Prime Visa iteration after the first failed its card check.
    """
    if "/merchantOffers/offer-hub" not in url or not re.search(r"[?&]accountId=\d+", url):
        raise ValueError("Capture the hub URL after selecting the card; it must carry accountId")
    return url


# Hermes agent.max_turns in ~/.hermes/config.yaml. One turn is one model response,
# however many tool calls it batches.
MAX_TURNS = 80
# Turns one issuer needs end to end: fresh navigation, fill, submit, offers, sign-out, outcome.
ISSUER_TURN_RESERVE = 15
# Hermes ends every tool call after 420s (timeouts.tools.sequential_call) whatever
# browser_exec's timeout_s says, and the daemon keeps running the abandoned code.
# Activation loops stop themselves before that and the next call resumes.
LOOP_SECONDS = 330


# Back-to-back logins at several banks from one browser look automated and coincided with the
# first Amex/BofA identity challenges (2026-10-30), so submits are spaced and serialized.
LOGIN_SPACING_SECONDS = 60


def _login_wait(data, name):
    latest = max((datetime.fromisoformat(other["submission_recorded_at"])
                  for other_name, other in data["issuers"].items()
                  if other_name != name and other.get("submission_recorded_at")), default=None)
    if latest is None:
        return 0.0
    return max(0.0, LOGIN_SPACING_SECONDS - (datetime.now(timezone.utc) - latest).total_seconds())


class Journal:
    def __init__(self, path):
        self.path = Path(path).expanduser()

    @contextmanager
    def transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = json.loads(self.path.read_text()) if self.path.exists() else {
                "started_at": now(), "status": "running", "issuers": {},
                "activations": [], "failures": [],
            }
            yield data
            data["updated_at"] = now()
            atomic_json(self.path, data)

    def prepare(self, required_issuers=None):
        """Exercise the actual persistence path before any bank mutation."""
        if required_issuers is not None:
            required_issuers = list(required_issuers)
            if not required_issuers or any(n not in ("chase", "amex", "bofa") for n in required_issuers):
                raise ValueError("Invalid required issuers")
        with self.transaction() as data:
            if required_issuers is not None:
                # Reopening a run must not silently drop an unfinished issuer.
                data["required_issuers"] = sorted(set(data.get("required_issuers", ("chase", "amex"))) | set(required_issuers))

    def issuer(self, name, **fields):
        if name not in ("chase", "amex", "bofa"):
            raise ValueError("Unknown issuer")
        with self.transaction() as data:
            current = data["issuers"].setdefault(name, {"login_submissions": 0, "status": "running"})
            if "login_submissions" in fields:
                # Declaring the initial zero is harmless; only reserve_login may count a submission.
                if fields.pop("login_submissions") != current.get("login_submissions", 0):
                    raise ValueError("Use reserve_login to record submissions")
            current.update(fields)

    def login_wait_seconds(self, name):
        """Seconds before ``name`` may submit, keeping issuer logins at least LOGIN_SPACING_SECONDS apart."""
        data = json.loads(self.path.read_text()) if self.path.exists() else {"issuers": {}}
        return _login_wait(data, name)

    def reserve_login(self, name):
        with self.transaction() as data:
            issuer = data["issuers"][name]
            if issuer.get("login_submissions", 0):
                raise ValueError("Login submission already reserved for this run")
            # One issuer at a time: an earlier login must have a recorded outcome before the next submit.
            for other_name, other in data["issuers"].items():
                if other_name != name and other.get("login_submissions") and other.get("status", "running") == "running":
                    raise ValueError(f"Record the {other_name} outcome (status) before another issuer's login")
            wait = _login_wait(data, name)
            if wait > 0:
                raise ValueError(f"Wait {wait:.0f}s before this login submit; nothing was reserved or clicked")
            issuer.update(login_submissions=1, submission_recorded_at=now())

    def abandon(self, reason):
        """Close a journal that never reached bank actions (e.g. a failed setup call) without claiming success."""
        with self.transaction() as data:
            if any(i.get("login_submissions") or i.get("attempted_offers") for i in data["issuers"].values()):
                raise ValueError("Journal has bank actions; finish it instead of abandoning")
            data.update(status="abandoned", abandoned_reason=reason, ended_at=now())

    def budget_allows(self, name, turns_used, max_turns=MAX_TURNS, reserve=ISSUER_TURN_RESERVE):
        """Before an issuer's login: record it as skipped when the remaining turns cannot finish it."""
        remaining = max_turns - turns_used
        if remaining >= reserve:
            return True
        self.skip(name, f"{remaining} turns left before {name} login; an issuer needs {reserve}")
        return False

    def skip(self, name, reason, status="skipped_budget"):
        with self.transaction() as data:
            issuer = data["issuers"].setdefault(name, {"login_submissions": 0, "status": "running"})
            if issuer.get("login_submissions"):
                raise ValueError("Login already submitted; record its real outcome instead of skipping")
            issuer.update(status=status, skipped_reason=reason)
            data["failures"].append({"issuer": name, "reason": status, "detail": reason})

    def close(self, reason, unstarted_status="skipped_budget"):
        """Finalize an interrupted run: unfinished issuers become skipped (no login) or partial."""
        with self.transaction() as data:
            if data["status"] != "running":
                raise ValueError(f"Journal is already {data['status']}")
            for name in sorted(set(data.get("required_issuers", ("chase", "amex"))) | set(data["issuers"])):
                issuer = data["issuers"].setdefault(name, {"login_submissions": 0, "status": "running"})
                if issuer.get("status", "running") == "running":
                    issuer["status"] = "partial" if issuer.get("login_submissions") else unstarted_status
                    data["failures"].append({"issuer": name, "reason": issuer["status"], "detail": reason})
        return self.finish()

    def reserve_offer(self, name, key):
        """Persist click intent first. An uncertain click must only be probed."""
        with self.transaction() as data:
            issuer = data["issuers"][name]
            attempted = issuer.setdefault("attempted_offers", [])
            if key in attempted:
                raise ValueError("Offer already attempted; inspect its live state without clicking")
            attempted.append(key)
            issuer["pending_offer"] = key

    def record(self, name, key, **offer):
        # Whitelist nonsecret metadata; callers cannot accidentally dump tool results.
        allowed = {k: offer[k] for k in ("merchant", "deal", "offer_id", "card_id", "proof", "expires_at", "minimum_spend", "reward_cap", "offer_type") if k in offer}
        with self.transaction() as data:
            issuer = data["issuers"][name]
            if issuer.get("pending_offer") not in (None, key):
                raise ValueError("Verified offer does not match reserved click")
            if not any(a.get("key") == key and a.get("issuer") == name for a in data["activations"]):
                data["activations"].append({"issuer": name, "key": key, "verified_at": now(), **allowed})
            issuer.pop("pending_offer", None)
            issuer["activation_count"] = sum(a["issuer"] == name for a in data["activations"])
        # The journal is canonical. A failed legacy-index write cannot erase proof.
        dedup = self.path.parent.parent / f"{name}-activated.json"
        with dedup.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            values = json.loads(dedup.read_text()) if dedup.exists() else []
            if not isinstance(values, list):
                raise ValueError("Invalid legacy activation index")
            if not any(v.get("url") == key for v in values):
                values.append({"url": key, "digestedAt": now()})
                atomic_json(dedup, values)

    def finish(self):
        with self.transaction() as data:
            # A later read-only probe can verify sign-out after a transient
            # redirect. Preserve that history without reporting it unresolved.
            for failure in data["failures"]:
                if (isinstance(failure, dict)
                        and failure.get("reason") == "sign-out not verified"
                        and data["issuers"].get(failure.get("issuer"), {}).get("sign_out") == "verified"):
                    failure.update(resolved=True, resolved_at=now(), proof="subsequent sign_out verified")
            unresolved = any(not isinstance(f, dict) or not f.get("resolved") for f in data["failures"])
            complete = not unresolved and all(
                data["issuers"].get(name, {}).get("status") == "completed"
                and data["issuers"][name].get("sign_out") == "verified"
                and not data["issuers"][name].get("pending_offer")
                and all(data["issuers"][name].get("cards", {}).get(card, {}).get("status") == "completed"
                        for card in data["issuers"][name].get("required_cards", []))
                for name in set(data.get("required_issuers", ("chase", "amex"))) | set(data["issuers"])
            )
            data["status"] = "completed" if complete else "partial"
            data["ended_at"] = now()
        return data["status"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "finish", "abandon", "close"])
    parser.add_argument("path", type=Path)
    parser.add_argument("--reason")
    args = parser.parse_args()
    journal = Journal(args.path)
    if args.action == "abandon":
        journal.abandon(args.reason or "setup failed before bank actions")
        print("abandoned")
    elif args.action == "close":
        print(journal.close(args.reason or "run ended before the journal was finalized"))
    else:
        print(journal.prepare() if args.action == "prepare" else journal.finish())
