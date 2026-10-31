# Cards workflow — Hermes runbook

## Active configuration

- Agent skill: `/Users/pattybot/.hermes/skills/credit-card-offers/SKILL.md`
  (symlink to `~/dotfiles/cards/.hermes/skills/credit-card-offers`).
- Schedule: Hermes cron job `ca128bfd6cbe`, daily at 03:00 America/New_York.
- Delivery: Hermes scheduler sends the final roundup to Telegram chat 7953915703.
- Browser: existing cards Chrome on CDP port 19223, owned by
  `com.pattybot.cards-bot-chrome`. Keep this daemon enabled.
- Credentials: current Chase/Amex Login items in **AI agents**, resolved through
  Hermes's `OP_SERVICE_ACCOUNT_TOKEN`. No cards-specific Keychain token is used.
- Native credentials: discover handles with `browser_vault_list`; inject only
  through `browser_vault_fill` with the exact observed `target_id`. Chase's
  same-origin login iframe also requires `frame_selector: "#logonbox"`.
  The saved Chase item must allow `https://secure.chase.com`; Amex must allow
  `https://www.americanexpress.com`. Preserve exact-origin checking.
- Cron toolsets must include `browser` alongside `terminal`, `file`, and `skills`.
  Never route passwords through the former `cards-secret-fill.py` helper.
- History: existing `~/.claude/skills/credit-card-offers/state/*-activated.json`;
  new secret-free per-run journals in `state/hermes-runs/`. Hermes cron history
  is the authority for message delivery and job execution status.

## Check without logging in

```bash
hermes cron list
hermes cron status
```

Ask Hermes for a dry-run with the cards skill to inspect browser state and
native vault metadata. Dry-run must not fill inputs or mutate bank state.
Metadata access alone does not prove password resolution or authentication.

## Manual live run

Find the job ID with `hermes cron list`, then run `hermes cron run JOB_ID`
once. This performs the complete authorized bank-login/offer/sign-out workflow
and sends the roundup. Do not launch another run while it is active.
Pause/resume with `hermes cron pause JOB_ID` / `hermes cron resume JOB_ID`.
MFA, device verification, CAPTCHA, and rejected logins require human attention;
Hermes continues the other issuer and reports a partial result.

## Daily repair and verification

The `hermes-maintenance` job runs at 09:00 America/New_York. It checks every
enabled Hermes job plus the cards journal, tests supported source repairs,
and reruns through the existing jobs with a three-attempt daily limit. Check
`hermes cron list` for its current ID and status. See
`~/dotfiles/automation/README.md` for installation and manual checks.

The active cards workflow imports `cards/bin/cards_journal.py` in native
`browser_exec`. This tests persistence before bank mutations and checkpoints
each verified activation without inline shell snippets. Use the Hermes skill's
`references/runtime.md`; partial outcomes carry `[CRON_FAILURE]` on a separate
first line so the scheduler and maintenance agree about completion.

## Retired components

The `com.pattybot.credit-card-offers` and `com.pattybot.cards-keepalive`
LaunchAgents were booted out and their plists removed from this package on
2026-10-31 (the Mac mini keeps the old links in `~/Library/LaunchAgents.disabled/`).
`cards-fire.sh` exits with migration guidance, so an old caller cannot start the
former workflow. Do not re-enable these jobs or run the Keychain provisioners.

`cards-secret-fill.py`, `cards-auth.py`, `cards-offers.py`, the Seatbelt profile, and the Keychain helper
are retained as historical implementation files, not dependencies of this flow.
The Keychain-scoped protection applied to the old Codex offers process; the new
Hermes flow instead has the user's explicitly authorized 1Password vault access.

The failed provisioning attempt created a service account whose token was lost.
Revoke that failed `pattybot-cards-mac-mini` account in 1Password. Do not revoke
the separate service account currently used by Hermes.
