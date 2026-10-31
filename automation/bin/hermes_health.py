#!/usr/bin/env python3
"""Check actual scheduled workflow outcomes; test and safely rerun repaired jobs."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[2]
HERMES = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
STATE = REPO / "automation/state"
CARDS_STATE = Path.home() / ".claude/skills/credit-card-offers/state/hermes-runs"
MAINTENANCE_SKILL = "hermes-maintenance"


def timestamp(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def role(job):
    if "credit-card-offers" in job.get("skills", []):
        return "cards"
    return {"twitter_digest_cron.sh": "twitter", "agentmail_reconcile.py": "agentmail"}.get(job.get("script"))


def latest_journal(directory=CARDS_STATE):
    files = list(directory.glob("*.json"))
    if not files:
        return None, None
    candidates = []
    for path in files:
        modified = path.stat().st_mtime
        try:
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                raise ValueError("journal must be an object")
            started = timestamp(data.get("started_at"))
        except (OSError, ValueError, TypeError, AttributeError):
            data, started = {"status": "invalid_journal"}, None
        # Cleanup can touch an older run after its replacement finishes.
        # Missing/corrupt timestamps fall back to mtime, failing closed.
        candidates.append((started.timestamp() if started else modified,
                           modified, str(path), path, data))
    _, _, _, path, data = max(candidates, key=lambda item: item[:3])
    return path, data


def card_issues(journal):
    if journal is None:
        return ["missing_cards_journal"]
    reasons = []
    if journal.get("status") != "completed":
        reasons.append("cards_" + str(journal.get("status", "missing_status")))
    if any(not isinstance(f, dict) or not f.get("resolved") for f in journal.get("failures", [])):
        reasons.append("cards_unresolved_failures")
    for name in sorted(set(journal.get("required_issuers", ("chase", "amex"))) | set(journal.get("issuers", {}))):
        issuer = journal.get("issuers", {}).get(name, {})
        if issuer.get("status") != "completed":
            reasons.append(name + "_incomplete")
        signout = issuer.get("sign_out")
        verified = signout == "verified" or (isinstance(signout, dict) and signout.get("verified_login_wall") is True)
        if not verified:
            reasons.append(name + "_signout_unverified")
        if issuer.get("pending_offer"):
            reasons.append(name + "_uncertain_click")
        if any(issuer.get("cards", {}).get(card, {}).get("status") != "completed"
               for card in issuer.get("required_cards", [])):
            reasons.append(name + "_card_coverage_incomplete")
    return reasons


def assess(job, execution=None, journal=None, now=None):
    now = now or datetime.now(timezone.utc)
    result = {"id": job["id"], "name": job.get("name"), "role": role(job), "issues": []}
    if execution and execution.get("status") in ("claimed", "running"):
        result["running"] = True
        if now - timestamp(execution["claimed_at"]) > timedelta(hours=1):
            result["issues"].append("execution_stuck")
        return result
    if job.get("last_status") not in (None, "ok", "success"):
        result["issues"].append("scheduler_failed")
    if execution and execution.get("status") in ("failed", "unknown"):
        result["issues"].append("execution_" + execution["status"])
    if job.get("last_delivery_error"):
        result["issues"].append("delivery_failed")
    due = timestamp(job.get("next_run_at"))
    if due and (now - due).total_seconds() > 3600:
        result["issues"].append("schedule_overdue")
    if role(job) == "cards" and job.get("last_run_at"):
        result["issues"].extend(card_issues(journal))
        started = timestamp((journal or {}).get("started_at"))
        if started and (timestamp(job["last_run_at"]) - started).total_seconds() > 3600:
            result["issues"].append("cards_journal_stale")
    return result


def snapshot():
    sys.path.insert(0, str(HERMES / "hermes-agent"))
    from cron.jobs import list_jobs
    from cron.executions import latest_executions
    jobs = [j for j in list_jobs() if j.get("enabled", True) and MAINTENANCE_SKILL not in j.get("skills", [])]
    executions = latest_executions([j["id"] for j in jobs])
    path, journal = latest_journal()
    results = [assess(j, executions.get(j["id"]), journal) for j in jobs]
    report = {"jobs": results, "wakeAgent": any(j["issues"] for j in results)}
    if path:
        report["cards_journal"] = str(path)
    heartbeat = HERMES / "cron/ticker_last_success"
    try:
        age = datetime.now(timezone.utc).timestamp() - float(heartbeat.read_text())
        if age > 600:
            report.update(wakeAgent=True, scheduler_issue="ticker_stale")
    except (OSError, ValueError):
        report.update(wakeAgent=True, scheduler_issue="ticker_health_unknown")
    return report


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def source_fingerprint(kind):
    digest = hashlib.sha256()
    roots = [REPO / "automation", REPO / kind]
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file() and not {"state", "__pycache__", ".git"}.intersection(path.parts):
                if path.suffix in (".py", ".sh", ".md", ".txt"):
                    digest.update(str(path).encode())
                    digest.update(path.read_bytes())
    if kind == "twitter":
        client_pid = Path.home() / ".browser-use/twitter-production.pid"
        digest.update(client_pid.read_bytes() if client_pid.exists() else b"client-disconnected")
    if kind == "agentmail":
        try:
            digest.update(version("agentmail").encode())
        except PackageNotFoundError:
            digest.update(b"agentmail-missing")
    return digest.hexdigest()


def retry_allowed(entry, journal, history, fingerprint):
    if entry.get("running"):
        raise ValueError("Job already running; wait for its recorded result")
    if not entry.get("issues"):
        raise ValueError("Job is healthy; no replay needed")
    if entry.get("role") not in ("cards", "twitter", "agentmail"):
        raise ValueError("Unknown job side effects; diagnose and report before adding a retry adapter")
    if entry["issues"] == ["delivery_failed"]:
        raise ValueError("Repair delivery using its existing receipt/outbox; do not rerun business actions")
    if len(history) >= 3:
        raise ValueError("Daily limit of three repair attempts reached")
    if any(h["fingerprint"] == fingerprint for h in history):
        raise ValueError("The same code/runtime has already failed today; make and test a supported repair first")
    if entry["role"] == "cards":
        for issuer in (journal or {}).get("issuers", {}).values():
            status = str(issuer.get("status", "")).lower()
            if any(s in status for s in ("mfa", "captcha", "rejected", "rejection", "challenge", "verification_required")):
                raise ValueError("Bank authentication requires user action; no automatic resubmission")
            if issuer.get("pending_offer"):
                raise ValueError("Uncertain offer click: reconcile live state before another run")


def validate(kind):
    commands = [[sys.executable, "-m", "unittest", "discover", "-s", str(REPO / "automation/tests"), "-q"]]
    if kind in ("cards", "twitter"):
        commands.append([sys.executable, "-m", "unittest", "discover", "-s", str(REPO / kind / "tests"), "-q"])
    elif kind == "agentmail":
        commands.append([sys.executable, str(Path(__file__)), "dependency-check"])
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
        if result.returncode:
            # Tests contain synthetic fixtures; no runtime secrets are loaded.
            print(result.stderr[-8000:], file=sys.stderr)
            raise ValueError("Validation failed; job was not rerun")


def rerun(job_id):
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "retry.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        entry = next((j for j in snapshot()["jobs"] if j["id"] == job_id), None)
        if entry is None:
            raise ValueError("Job absent, disabled, or maintenance itself")
        path = STATE / "retries.json"
        ledger = json.loads(path.read_text()) if path.exists() else {}
        day_key = datetime.now(timezone.utc).date().isoformat() + ":" + job_id
        history = ledger.get(day_key, [])
        fingerprint = source_fingerprint(entry["role"])
        retry_allowed(entry, latest_journal()[1], history, fingerprint)
        validate(entry["role"])
        history.append({"fingerprint": fingerprint, "started_at": datetime.now(timezone.utc).isoformat()})
        ledger[day_key] = history
        atomic_json(path, ledger)  # Charge attempt before launch, even if interrupted.
        log = STATE / (day_key.replace(":", "-") + f"-{len(history)}.log")
        with log.open("w") as output:
            proc = subprocess.Popen([str(Path.home() / ".local/bin/hermes"), "cron", "run", job_id],
                                    stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                proc.wait(timeout=1500)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                raise ValueError("Rerun timed out; reconcile execution ledger before any further attempt")
        after = next(j for j in snapshot()["jobs"] if j["id"] == job_id)
        history[-1].update(returncode=proc.returncode, issues=after["issues"], log=str(log))
        atomic_json(path, ledger)
        print(json.dumps(after))
        return 1 if after["issues"] or proc.returncode else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["check", "retry", "test", "dependency-check"])
    parser.add_argument("target", nargs="?")
    args = parser.parse_args()
    if args.action == "check":
        # Hermes's wake gate parses the final nonempty line as one JSON object.
        try:
            report = snapshot()
        except Exception as error:
            # A broken checker must wake maintenance, not silently suppress it.
            report = {"wakeAgent": True, "checker_error": type(error).__name__}
        print(json.dumps(report))
    elif args.action == "retry":
        return rerun(args.target)
    elif args.action == "test":
        validate(args.target)
        print("Validation passed")
    else:
        import agentmail
        import yaml
        subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
        print("AgentMail imports in the scheduler interpreter")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"health: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
