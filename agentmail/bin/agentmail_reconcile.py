#!/usr/bin/env python3
"""Retry authenticated post-cutover AgentMail messages not marked processed.

Private values are read at runtime, never from this file:
- AGENTMAIL_INBOX_ID / AGENTMAIL_AUTHORIZED_SENDER from the environment (the
  Hermes shim in ../hermes-scripts sets them from the local ingress module);
- the AgentMail API key and webhook route secret from Hermes's config.yaml.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr
from pathlib import Path
from typing import Any

import yaml
from agentmail import AgentMail


HERMES_CONFIG = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "config.yaml"
CUTOVER_AFTER = datetime(2026, 7, 29, 14, 18, tzinfo=UTC)
RETRY_GRACE = timedelta(hours=1)
LOCAL_WEBHOOK = "http://127.0.0.1:8789/webhooks/agentmail"


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def load_config() -> dict[str, Any]:
    return yaml.safe_load(HERMES_CONFIG.read_text(encoding="utf-8")) or {}


def load_api_key() -> str:
    try:
        value = (
            load_config()
            .get("mcp_servers", {})
            .get("agentmail", {})
            .get("env", {})
            .get("AGENTMAIL_API_KEY", "")
        )
    except Exception:
        value = ""
    if not isinstance(value, str) or not value.strip():
        # Matches the ingress behavior: stay quiet when the credential is absent.
        print("AgentMail reconcile skipped: credential unavailable", file=sys.stderr)
        print("[SILENT]")
        raise SystemExit(0)
    return value.strip()


def route_secret() -> str:
    value = (
        load_config()
        .get("platforms", {})
        .get("webhook", {})
        .get("extra", {})
        .get("routes", {})
        .get("agentmail", {})
        .get("secret", "")
    )
    if not isinstance(value, str) or not value:
        raise RuntimeError("AgentMail webhook secret unavailable")
    return value


def header_map(raw_headers: Any) -> dict[str, str]:
    if not isinstance(raw_headers, dict):
        return {}
    return {str(key).strip().lower(): str(value) for key, value in raw_headers.items()}


def authenticated_candidate(message: object, cutoff: datetime, authorized_sender: str) -> bool:
    labels = {str(label).strip().lower() for label in (message.labels or [])}
    if "hermes-processed" in labels:
        return False
    if message.timestamp > cutoff:
        return False
    if parseaddr(str(message.from_ or ""))[1].strip().lower() != authorized_sender:
        return False

    headers = header_map(message.headers)
    auto_submitted = headers.get("auto-submitted", "").strip().lower()
    if auto_submitted and auto_submitted != "no":
        return False
    authentication = headers.get("authentication-results", "").lower()
    return (
        "dkim=pass" in authentication
        and "header.i=@gmail.com" in authentication
        and "dmarc=pass" in authentication
        and "header.from=gmail.com" in authentication
    )


def signed_request(message_id: str, inbox_id: str, secret: str) -> urllib.request.Request:
    body = json.dumps(
        {
            "event_type": "message.received",
            "message": {"inbox_id": inbox_id, "message_id": message_id},
        },
        separators=(",", ":"),
    ).encode()
    delivery_id = "reconcile_" + hashlib.sha256(message_id.encode()).hexdigest()[:32]
    timestamp = str(int(time.time()))
    if secret.startswith("whsec_"):
        key = base64.b64decode(secret.removeprefix("whsec_"), validate=True)
    else:
        key = secret.encode()
    signed = delivery_id.encode() + b"." + timestamp.encode() + b"." + body
    signature = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return urllib.request.Request(
        LOCAL_WEBHOOK,
        data=body,
        headers={
            "Content-Type": "application/json",
            "svix-id": delivery_id,
            "svix-timestamp": timestamp,
            "svix-signature": "v1," + signature,
        },
    )


def main() -> None:
    inbox_id = required_env("AGENTMAIL_INBOX_ID")
    authorized_sender = required_env("AGENTMAIL_AUTHORIZED_SENDER").lower()
    client = AgentMail(api_key=load_api_key(), timeout=20)
    cutoff = datetime.now(UTC) - RETRY_GRACE
    response = client.inboxes.messages.list(
        inbox_id,
        limit=100,
        after=CUTOVER_AFTER,
        ascending=True,
        include_unauthenticated=True,
        from_=[authorized_sender],
    )
    secret = route_secret()
    submitted = 0
    for item in response.messages:
        message = client.inboxes.messages.get(inbox_id, item.message_id)
        if not authenticated_candidate(message, cutoff, authorized_sender):
            continue
        request = signed_request(message.message_id, inbox_id, secret)
        try:
            with urllib.request.urlopen(request, timeout=15) as result:
                if result.status not in (200, 202):
                    raise RuntimeError(f"unexpected webhook status {result.status}")
        except urllib.error.URLError as exc:
            raise RuntimeError("local AgentMail retry submission failed") from exc
        submitted += 1

    if submitted:
        print(f"AgentMail reconciliation re-submitted {submitted} message(s).", file=sys.stderr)


if __name__ == "__main__":
    main()
