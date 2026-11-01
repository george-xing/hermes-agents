# Cards cron job: drafts, not applied

The live job (`ca128bfd6cbe`, "Credit card offers — Hermes", 03:00
America/New_York) still runs with `prompt-current.txt` and no pre-run script.
Nothing here changes it until the steps below are run by hand after the
`cards-helpers-refactor` PR is merged and `~/dotfiles` is on that commit.

## Pre-run hook (Hermes 0.21.3)

A cron job's `script` runs before the agent; its stdout is added to the prompt
under "Script Output" (`cron/scheduler_prompt.py`). A last stdout line of JSON
`{"wakeAgent": false}` would skip the agent; the preflight never prints that.
Scripts must resolve inside `~/.hermes/scripts/` after following symlinks
(`cron/scheduler_script.py::_resolve_script_path`), so a symlink into dotfiles
is refused; `.py` runs with Hermes's own Python. The `cron.script_timeout_seconds`
in `~/.hermes/config.yaml` is 30, enough for the preflight.

## Apply

Do this in the same sitting as the merge: the current prompt points the agent
at `references/runtime.md`, which the refactor deletes.

```bash
cp ~/dotfiles/cards/docs/cron/hermes-scripts/cards_preflight.py ~/.hermes/scripts/cards_preflight.py
hermes cron edit ca128bfd6cbe --script cards_preflight.py --prompt "$(cat ~/dotfiles/cards/docs/cron/prompt-new.txt)"
```

The first preflight closes every `running` journal older than 45 minutes as
`partial` (19 historical ones on 2026-11-01).

## Roll back

```bash
hermes cron edit ca128bfd6cbe --script "" --prompt "$(cat ~/dotfiles/cards/docs/cron/prompt-current.txt)"
```

then check out the previous skill (`git -C ~/dotfiles revert <merge commit>`).
