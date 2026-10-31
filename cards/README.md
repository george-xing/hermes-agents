# Hermes + 1Password card offers example

Hermes uses a dedicated Chrome profile to activate available Chase (including Amazon Visa), Amex, and Bank of America Alaska/Atmos
offers, records each verified activation, signs out, and delivers a roundup.
The active implementation is the [Hermes skill](.hermes/skills/credit-card-offers/SKILL.md).
The former deterministic login/offer scripts, keepalive and Keychain
provisioning path were removed from this repo on 2026-10-31 (recoverable from git history before that commit).

The credential path is:

```text
1Password service account → dedicated AI agents vault
                         → native browser_vault_list / browser_vault_fill
                         → exact bank origin + selected browser tab
```

Use a service account restricted to reading the dedicated vault. Store its
`OP_SERVICE_ACCOUNT_TOKEN` in the private Hermes environment, never this repo.
Put the bank Login items in that vault and include their actual login origins
in 1Password. Native fill binds the password to the selected tab; Chase's
same-origin login iframe also needs its observed frame selector. Neither the
model nor the bookkeeping helper needs the password value.

To adapt the example:

1. Install Hermes with native `browser_exec`, `browser_vault_list` and
   `browser_vault_fill` support, plus 1Password CLI. Verify those tools exist in
   your installation; they are prerequisites, not provided by this dotfiles repo.
2. Configure your service account and bank origins privately. Use a separate
   persistent Chrome profile and loopback CDP port (this setup uses 19223).
3. Link `cards/.hermes/skills/credit-card-offers` into your Hermes skills folder.
   Adapt the machine paths in the skill/runbook and Chrome launchd plist before
   installing them. Keep runtime state private and gitignored.
4. Dry-run the skill to verify browser routing and vault metadata. Then perform
   one live run, inspecting login, activation, journal and sign-out results.
5. Schedule the skill with Hermes cron and enable `browser`, `terminal`, `file`
   and `skills` toolsets. This installation runs at 03:00 New York time.
6. Install [daily maintenance](../automation/README.md) after validating the jobs.

The agent selects actions from the live page. The small
[`cards_journal.py`](bin/cards_journal.py) helper only handles atomic bookkeeping:
login/click reservations, immediate verified records, and truthful completion.
It does not log in, retrieve credentials, or drive the bank UI. Uncertain clicks
are inspected without repeating them; real MFA and rejected logins stop the
affected issuer. Partial outcomes remain failures visible to daily maintenance.

Tests: `python3 -m unittest discover -s cards/tests`. Private journals, cookies,
tokens and logs are excluded from Git.
