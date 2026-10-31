# Cards workflow — Hermes runbook

## Active configuration

- Agent skill: `/Users/pattybot/.hermes/skills/credit-card-offers/SKILL.md`
  (symlink to `~/dotfiles/cards/.hermes/skills/credit-card-offers`).
- Schedule: Hermes cron job `ca128bfd6cbe`, daily at 03:00 America/New_York.
- Delivery: Hermes scheduler sends the final roundup to Telegram chat 7953915703.
- Browser: existing cards Chrome on CDP port 19223, owned by
  `com.pattybot.cards-bot-chrome`. Keep this daemon enabled.
- Credentials: current Chase/Amex/BofA Login items in **AI agents**, resolved through
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
Hermes continues the other issuers and reports a partial result.

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
LaunchAgents were already unloaded and their plists were removed from this
package on 2026-10-31 (the Mac mini keeps the old links in
`~/Library/LaunchAgents.disabled/`). `cards-fire.sh` exits with migration
guidance, so an old caller cannot start the former workflow. Do not re-enable
these jobs or run the Keychain provisioners.

`cards-secret-fill.py`, `cards-auth.py`, `cards-offers.py`, `cards-prefire.sh`,
`cards-keepalive.sh`, the 1Password/Keychain provisioners, the Seatbelt profile,
and the Keychain helper were removed from this repo on 2026-10-31 (recoverable from git history before that commit).
The Keychain-scoped protection applied to the old Codex offers process; the new
Hermes flow instead has the user's explicitly authorized 1Password vault access.

The failed provisioning attempt created a service account whose token was lost.
Revoke that failed `pattybot-cards-mac-mini` account in 1Password. Do not revoke
the separate service account currently used by Hermes.

## Additional cards (2026-10-30)

The same daily job also checks Amazon/Prime Visa within Chase and BofA
Alaska/Atmos BankAmeriDeals. See the Hermes skill's
`references/additional-cards.md` for eligibility, timed-deal exclusions, and
monthly targeted-bonus checks. BofA needs a Login item in AI agents with its
observed official login origin; missing metadata or MFA is reported as setup
needed while the existing issuers continue. No extra schedule is needed.

New runs declare all three required issuers. Bank UI support for BofA and
Amazon card switching requires live verification; unit tests only validate
bookkeeping. The 2026-10-30 live test verified Amazon card switching, four
activations (three using the repaired maintained parser/verifier), and Chase
sign-out. BofA login/navigation/sign-out worked after user MFA, but the offer
feed remained Loading, so BofA activation is still unverified. Amex required
MFA during this test. The daily workflow remains enabled with full coverage.

For a manual API invocation with transient per-run context, use the same
interpreter as `~/.local/bin/hermes`: `~/.hermes/hermes-agent/venv/bin/python`.
The older `.venv` lacks Telegram support and caused a manual test delivery
failure. Prefer the CLI for ordinary runs; do not change the scheduled runtime.
