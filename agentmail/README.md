# AgentMail reconciliation

Every 15 minutes Hermes cron runs `agentmail_reconcile.py` (no-agent mode). It
lists recent messages in the AgentMail inbox from the authorized sender, keeps
those that pass DKIM/DMARC, are older than an hour, and lack the
`hermes-processed` label, and re-submits each one to the local Hermes webhook
(`127.0.0.1:8789/webhooks/agentmail`) with a valid Svix signature. Missed or
failed webhook deliveries are therefore retried without duplicating work the
gateway already finished.

## Layout

- `bin/agentmail_reconcile.py`: the tracked reconciler.
- `hermes-scripts/agentmail_reconcile.py`: a small shim that Hermes cron runs.
- `tests/`: synthetic-fixture unit tests.

## Install

**Don't `stow` this package.** Hermes refuses to run cron scripts that resolve
outside `~/.hermes/scripts`, so a symlink would break the job. Copy the shim
instead:

```bash
install -m 700 ~/dotfiles/agentmail/hermes-scripts/agentmail_reconcile.py ~/.hermes/scripts/
```

The shim reads `INBOX_ID` and `AUTHORIZED_SENDER` from the private
`~/.hermes/scripts/agentmail_ingress.py` (the webhook route's ingress filter,
kept local). It exports them as `AGENTMAIL_INBOX_ID` and
`AGENTMAIL_AUTHORIZED_SENDER`, then execs the tracked script with the same
interpreter. The API key and webhook route secret are read from
`~/.hermes/config.yaml`. Nothing private is committed here.

The `agentmail` dependency is pinned in
[`../automation/requirements-agentmail.txt`](../automation/requirements-agentmail.txt)
and must be installed in the Hermes gateway venv.

## Verify

```bash
~/.hermes/hermes-agent/venv/bin/python -m unittest discover -s ~/dotfiles/agentmail/tests
~/.hermes/hermes-agent/venv/bin/python ~/dotfiles/automation/bin/hermes_health.py test agentmail
```
