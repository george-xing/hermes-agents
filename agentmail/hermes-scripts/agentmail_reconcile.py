#!/usr/bin/env python3
"""Hermes cron shim: run the tracked AgentMail reconciler from ~/dotfiles.

Hermes only runs cron scripts that resolve inside ~/.hermes/scripts, so this
file is copied there, not stowed. The private inbox values come from the local
agentmail_ingress.py beside it, which the AgentMail webhook route already uses.
"""

import os
import sys
from pathlib import Path

from agentmail_ingress import AUTHORIZED_SENDER, INBOX_ID

os.environ.setdefault("AGENTMAIL_INBOX_ID", INBOX_ID)
os.environ.setdefault("AGENTMAIL_AUTHORIZED_SENDER", AUTHORIZED_SENDER)
target = Path.home() / "dotfiles/agentmail/bin/agentmail_reconcile.py"
os.execv(sys.executable, [sys.executable, str(target)])
