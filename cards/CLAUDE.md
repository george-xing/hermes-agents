# Cards automation

The active cards workflow is Hermes-managed. Read
`.hermes/skills/credit-card-offers/SKILL.md` for browser login, activation,
persistence, sign-out, and reporting, and
`.claude/skills/credit-card-offers/references/runbook.md` for operations.

Hermes runs the daily 03:00 America/New_York cron job and sends its final
roundup to Telegram. The existing cards Chrome daemon remains on port 19223.
Its turn limit is the global `agent.max_turns` in `~/.hermes/config.yaml`
(not in this repo; raised 40 → 80 on 2026-11-01 for every Hermes job, backup
in `~/consolidate-backup-2026-10-31/`). Hermes also ends each tool call after
420 s (`timeouts.tools.sequential_call`); `cards_journal` mirrors both.
Hermes accesses the Chase/Amex/BofA items in the **AI agents** 1Password vault using
its existing service-account token through native `browser_vault_list` and
`browser_vault_fill`. Bind each fill to the observed tab ID; Chase's same-origin
login iframe also needs `frame_selector`. Hermes enters the returned nonsecret
identifier and submits once. `cards-secret-fill.py` and the other legacy
login/offer scripts were removed from this repo on 2026-10-31 (recoverable from git history before that commit).
Do not expose credentials in model context, tool output, arguments, or images.

The former `cards-fire.sh` launchd job, deterministic login/offer driver,
Keychain provisioners, and keepalive are retired. `cards-fire.sh` exits with
migration guidance. Do not restore the old schedule or run both workflows.

The stowed Claude and Codex skill is a routing entry to the Hermes skill.
`~/.hermes/skills/credit-card-offers` links to the `.hermes` skill in this package.
Existing activation history remains under the stowed skill's `state/` folder;
this private state is gitignored. New per-run journals go to `state/hermes-runs/`.

Steps with one right answer live in `bin/cards_browser.py` (tab attach, fresh
login page and username, single reserved submit, Chase/Amex activation loops,
sign-out, roundup); its `BANKS` table holds every selector and URL with the
date it was observed. The agent handles only `needs_judgment` results, BofA
offer classification and the monthly bonus review. Preserve the
single-submission login rule, single-click activation verification,
randomized 3–6 second cadence, partial progress, and explicit sign-out. Never use a different Chrome profile. MFA and challenges
stop the affected issuer. Dry-runs make no bank mutations or outbound reports.

Validation: request a Hermes dry-run with this skill for native vault metadata
access and browser state. This does not prove authentication. Native fill and
tab/frame isolation tests live in Hermes's `tests/tools/test_browser_vault*.py`;
run them through its `scripts/run_tests.sh`. The cards helpers are tested
offline against a fake browser harness: `python3 -m unittest discover -s cards/tests`.
