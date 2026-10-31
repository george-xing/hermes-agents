# dotfiles

Personal config for my Mac mini. Each top-level directory is a **package** — a tree of files whose layout mirrors where they belong under `$HOME`. GNU `stow` (`brew install stow`) creates symlinks from `$HOME/...` back into the repo, so edits land in `$HOME` but are version-controlled here.

## Usage

```bash
cd ~/dotfiles
stow -t ~ twitter       # install
stow -t ~ -D twitter    # uninstall
stow -t ~ -R twitter    # reinstall (idempotent refresh after layout changes)
```

`stow` refuses to clobber pre-existing real files in `$HOME` — it'll list them as conflicts and abort. Move them aside first and rerun.

## Packages

- **[`cards/`](cards/README.md)** — Chase/Amex offers through Hermes's native
  browser and 1Password tools, using a dedicated AI agents vault. Includes the
  current skill, atomic activation journal, tests, and migration notes.
- **`twitter/`** — Morning (08:00 ET) and evening (22:00 ET) X digest, bookmarks,
  and search through Hermes, a dedicated Chrome profile, and verified Telegram
  delivery. See `twitter/CLAUDE.md` and its skill runbooks.
- **[`automation/`](automation/README.md)** — Daily checks of Hermes jobs,
  failure diagnosis, tested repairs and bounded reruns. Includes installation,
  health/retry helpers, and the maintenance skill.
- **[`doordash/`](doordash/hermes/README.md)** — DoorDash CLI skills, Hermes
  persona and macOS gateway setup notes, with owner confirmation for checkout.
- **[`telegram/`](telegram/README.md)** — Standalone Telegram receiver that
  answers allowlisted messages with a fresh `claude -p` run, plus its
  LaunchAgent and healthcheck. Tokens and access policy stay local.
- **[`agentmail/`](agentmail/README.md)** — AgentMail webhook reconciliation:
  re-submits authenticated emails that the gateway missed. Installed by copying
  a shim into `~/.hermes/scripts` (Hermes rejects symlinked cron scripts), not
  by `stow`.

These are examples from a personal Mac setup. Adapt machine paths, browser
profiles, delivery targets, and private credential configuration before use.
No live credentials or run history are needed to read the examples.

## Not in the repo

- `**/state/` — runtime state (cookies, last-success markers). Committing cookies would leak authenticated X sessions to anyone who reads the repo. `.gitignore` enforces this.
- `~/.claude/channels/telegram/.env` — Telegram bot token. Managed by the Telegram plugin.
- Log files under `~/Library/Logs/`.

## Adding a new package

1. `mkdir -p dotfiles/<pkg>/<same path structure as $HOME>`
2. Move (don't copy) the files you want tracked into that tree
3. `stow -t ~ <pkg>` — symlinks back to `$HOME`
4. `git add <pkg> && git commit`
