# Bookkeeping and recovery

Use this helper inside the same native `browser_exec` session as bank actions.
It does no navigation, credential access or activation. It replaces repeated
shell heredocs that unattended cron rejects, and persists each result as it
happens. Keep the existing cron approval policy.

```python
from pathlib import Path
from datetime import datetime, timezone
import sys
sys.path.insert(0, str(Path.home() / "dotfiles/cards/bin"))
from cards_journal import Journal
stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
journal = Journal(Path.home() / ".claude/skills/credit-card-offers/state/hermes-runs" / (stamp + ".json"))
journal.prepare(required_issuers=("chase", "amex", "bofa"))  # Before bank actions.
journal.issuer("chase", tab_id=chase_target_id)
journal.issuer("amex", tab_id=amex_target_id)
journal.issuer("bofa")  # Required even if credentials/tab are unavailable.
```

Keep this journal path for the whole run. `journal.issuer` accepts
`login_submissions=0` as a harmless declaration but never a nonzero count. If
setup raises before any bank action, call `journal.abandon("<reason>")` on that
journal and start a fresh one; never leave a `running` orphan. Before the
single login submission, call `journal.reserve_login(issuer)`; it requires
every other submitted issuer to have a recorded outcome and 60 seconds since
their submit (`journal.login_wait_seconds(issuer)`). Use `journal.issuer(issuer, **fields)` to
record the fill results, verified offers page, failure reason, or sign-out.
Never include credentials or full tool receipts.

For each observed available offer: calculate its existing dedup key, call
`journal.reserve_offer(issuer, key)`, click once, and verify through read-only
polls. On proof, call
`journal.record(issuer, key, merchant=merchant, deal=deal, offer_id=offer_id)`;
Pass `card_id=card_label` for every issuer; use a masked label with last four,
never a full card number. Chase reservation keys include this card identity.
BofA scope and offer classification are defined in additional-cards.md. This writes the canonical journal before the
legacy index. If index persistence fails, stop further clicks; the verified
record remains available for repair. Do not replay an uncertain click.

Handle each issuer independently but finish its activation loop, sign-out, and
recorded outcome before the next issuer's login submit. With 80 model turns, batch work within each issuer and
reserve turns for all three issuers plus sign-out/finalization. Never omit a
card or issuer and claim completion; persist partial coverage if time runs out.

```python
if not journal.budget_allows("bofa", turns_used):  # Records skipped_budget.
    status = journal.close("turn budget: bofa not started")
```

`budget_allows` refuses an issuer when fewer than `ISSUER_TURN_RESERVE` turns
remain. `skip(issuer, reason)` records any other deliberate skip; both refuse
once that issuer's login was submitted. `close(reason)` marks every issuer
still `running` as `skipped_budget` (no login) or `partial` (after a login),
records why, and returns `finish()`'s status. It refuses a journal that is no
longer `running`. A stuck journal from an interrupted run can be closed from a
terminal with `cards_journal.py close <journal> --reason "<why>"`.

Activation loops end themselves at `cards_journal.LOOP_SECONDS`: Hermes
abandons any tool call after 420 seconds but the daemon keeps running it, so a
timed-out loop must never overlap a new one. Resume with another bounded call.
Use the named native browser session's Python loop to batch work. Test syntax
before any side effects; Python uses `"log in" in title.lower()`, never `/log in/i`.

A read timeout before a mutation can be retried up to three times, reattaching
to the same target with `switch_tab` and using a small serializable probe.
After a click or navigation timeout, first inspect the live target to determine
whether the action happened; retry only reads. Never resubmit a login or offer.
Stop on real rejection, MFA, CAPTCHA, or device verification.

After exhausting available offers and verifying sign-out:
`journal.issuer(issuer, status="completed", sign_out="verified")`.
Use `partial` or a specific blocked status otherwise. End with `journal.finish()`;
it requires every declared/present issuer completed and verified sign-out.
Older journals without required_issuers retain the Chase/Amex baseline.
An interrupted journal remains partial/running and wakes daily maintenance.
Sign-out redirects can hydrate slowly too: poll the login wall read-only before
recording failure. A later verified sign-out resolves the exact transient
`sign-out not verified` journal failure during `finish()`, preserving its history.
