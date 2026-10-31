---
name: credit-card-offers
description: Run the Hermes-managed Chase and Amex offer workflow using the AI agents 1Password vault and the existing cards browser.
---

# Credit Card Offers

The active workflow is now owned by Hermes. Read the maintained skill at
`/Users/pattybot/.hermes/skills/credit-card-offers/SKILL.md` for login, offers,
verification, and reporting. Its source is
`/Users/pattybot/dotfiles/cards/.hermes/skills/credit-card-offers/SKILL.md`.

The daily schedule is a Hermes cron job at 03:00 America/New_York. The old
launchd daily job, `cards-fire.sh`, deterministic login/offer scripts, and
Keychain provisioning are retired; the scripts were removed from the repo
(see git history). Do not re-enable or invoke them.

For setup/status, read `references/runbook.md`. For an explicitly requested
live run, identify the cards job with `hermes cron list` and invoke
`hermes cron run JOB_ID` once. This submits bank logins, activates offers,
and delivers the result to Telegram. For a dry-run, use the Hermes skill's
read-only instructions; do not use the old wrapper's dry-run.
