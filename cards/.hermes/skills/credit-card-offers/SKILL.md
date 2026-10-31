---
name: credit-card-offers
description: Log in to Chase and Amex through the existing cards browser using the AI agents 1Password vault, activate available card offers, and return a daily roundup. Use for the scheduled cards job or requests to activate Chase/Amex offers.
metadata:
  hermes:
    category: finance
    tags: [chase, amex, offers, 1password, browser]
---

# Credit Card Offers — Hermes

Hermes owns navigation, login decisions, offer activation, and sign-out. The
user authorized this daily workflow and use of the Chase/Amex Login items in
**AI agents**. The old standalone login script, Keychain token, Codex wrapper,
and deterministic offers fallback are retired and were removed from the repo
(see git history). `cards-fire.sh` only prints migration guidance; do not invoke it.

The 3:00 a.m. America/New_York Hermes cron job delivers your final response to
Telegram automatically. Return the roundup; do not separately send a Telegram
message. A manual chat invocation returns the same roundup to that chat.

## Runtime and credentials

- Attach only to the existing **cards Chrome** at `http://127.0.0.1:19223`.
  The launchd Chrome daemon remains in service. Do not use the user's daily
  Chrome, another port, or a fresh browser profile. Do not close Chrome.
- Use Hermes's managed `browser_exec` in one named cards session for
  navigation, input metadata, the nonsecret username, submission, and offers.
  Verify its actual CDP endpoint is port 19223 before any bank action; do not
  assume a session name alone selects the browser.
- **Use native `browser_vault_list` and `browser_vault_fill` for both issuers.**
  Discover the current 1Password handle from the AI agents login metadata;
  use its returned identifier for the username. Passwords are resolved and
  injected by the native vault tool, never by shell/`op read`, generic typing,
  `cards-secret-fill.py`, or the retired deterministic login driver.
- Pass the observed `target_id` to `browser_vault_fill`; for Chase's observed
  same-origin `#logonbox` login iframe also pass `frame_selector`. Check the
  returned `target_id` matches the tab used for username entry and submission.
  Never fill an unspecified first same-origin tab when multiple tabs exist.
- Native origin binding remains exact. The saved Chase login must include
  `https://secure.chase.com` for its protected login; Amex uses the saved
  `https://www.americanexpress.com` login origin. Do not bypass an origin refusal.
- Keep passwords, tokens and filled input values out of logs and screenshots.
- Existing activation history remains under
  `/Users/pattybot/.claude/skills/credit-card-offers/state/`.

## Run preparation

1. For a requested **dry-run**, inspect CDP connectivity, bank login state,
   and visible offer counts only. Do not fill inputs, submit login, activate
   offers, sign out, send messages, or alter activation state. Report what
   remains untested. `browser_vault_list` can verify native vault metadata access;
   a successful listing does not prove password resolution or authentication.
2. For a live run, check this job is not already running before starting a
   manual invocation. Hermes serializes scheduled runs of the same job.
3. Read `/json` on port 19223 to select each bank's exact tab ID and URL.
   If there are multiple tabs for an issuer, choose the one visibly intended
   for this task and retain its exact tab ID. Prefer that issuer's most recently
   verified journal tab when it is still open on the expected bank origin.
   An exact URL can still match two tabs. Reuse the selected tab; do not create another issuer tab when one
   already exists. If uncertain, report ambiguity.
   If a bank tab is absent, create a tab in this same Chrome via CDP
   `PUT /json/new?<URL>` using the issuer's official destination below.
4. Read `references/runtime.md` with `skill_view(name="credit-card-offers",
   file="references/runtime.md")`. Use its tested journal helper and exercise
   persistence **before** login or any offer click. Create a secret-free journal in
   `state/hermes-runs/<UTC-timestamp>.json` with `status: running`, per-issuer
   status, `activations: []`, and `failures: []`. Record verified activations
   immediately, using atomic replacement. Retain partial progress on failure.

## Attach the managed browser to the selected tab

A named `browser_exec` session can initially be attached to `about:blank`.
This is expected. **Call `switch_tab(target_id)` inside `browser_exec` before
inspecting, navigating, entering a username, or submitting on a bank tab.**
Verify the attachment with `current_tab()`. Use the same named session throughout.

```python
# Attach to the observed bank tab before interacting
target_id = "OBSERVED_TAB_ID"
switch_tab(target_id)
assert current_tab()["target_id"] == target_id
print(js("({origin:location.origin,path:location.pathname})"))
```

`cdp('Target.activateTarget', targetId=...)` only changes Chrome's visible tab;
it does **not** change the browser harness's attached tab. A successful activate
call followed by `about:blank` is not a bank failure. Recover with `switch_tab`
and recheck the attachment before classifying routing as blocked. The native
vault `target_id` must equal this verified `current_tab()["target_id"]`.

## Login: inspect, choose controls, fill, submit once

Official destinations:

- Chase: `https://secure.chase.com/web/auth/dashboard?navKey=reviewMerchantOffers`
- Amex: `https://global.americanexpress.com/offers/eligible`

Navigate each issuer to its protected offers destination and inspect current
page state. If already authenticated, proceed to offers. Otherwise inspect
visible input **metadata only** (type, ID, name, autocomplete, labels) and
login-button text. Chase may render login inside the same-origin `#logonbox`
iframe; inspect that document when present. Choose selectors from the live
page; never assume sample selectors remain valid.
This initial navigation is required even when an existing tab already displays
a login wall. Follow the fresh-document example in `references/native-login.md`;
do not fill a persistent stale login document from an earlier run.

Record `tab_id`, `login_submissions: 0`, and separate `username_fill` and
`password_fill` outcomes in the issuer journal. A rejection visible **before**
this run submits anything is prior page state, not this run's failed fill or
rejected login. Check the preceding journal and actual bank response. A still-open
login wall, a client-side input/click failure, or an inconclusive redirect is
**not a confirmed rejection** and must not become a permanent login prohibition.
The one-submission limit applies per issuer **within the current run**. A new
explicitly user-authorized verification after a workflow fix may submit once;
record that authorization in its journal. A later verified successful login
also resolves older rejection state. Stop on a confirmed unresolved bank
rejection unless the user authorizes another attempt. Do not recommend changing
a password merely because vault fill reported success.

Load references with `skill_view(name="credit-card-offers", file="references/native-login.md")`
and `file="references/offer-navigation.md"`; they belong to the Hermes skill,
not the old `.claude` routing folder. Read [native-login.md](references/native-login.md) before credential entry. Use
its native input setter and input/change events for the observed username
control on both banks, then verify only a boolean that it matches the identifier
returned by `browser_vault_list`. Keep the password in native vault fill.
The same reference shows the verified DOM button click for submission; use it
instead of coordinate clicks for these known login controls.
Then invoke native vault fill with the observed handle and tab:

```json
{"handle":"op:OBSERVED_ITEM_ID","target_id":"OBSERVED_TAB_ID","frame_selector":"#logonbox"}
```

For Amex's top-level form omit `frame_selector`. Before submission require
native `success: true`, one password field filled, and the expected `target_id`.
Switch back to that exact ID in `browser_exec` and verify the expected form and
origin without reading input values. A refusal means inspect routing and form
metadata; never fall back to another credential-reading path.

Recheck the username match and nonempty password as booleans after the native
fill, as described in the login reference. After both successful fills, use `browser_exec` to click the observed
login button **once**, on the same tab ID passed to native vault fill.
Record the submission before executing it so interruption cannot cause a second
click. A Python syntax error before execution is not a bank submission; correct
the syntax and execute the one recorded attempt. A runtime error after the click
requires read-only recovery. A fill receipt proves only input assignment; only the bank response
establishes authentication. Sequential username/password pages may require a
single Next action between the fields. Do not retry a rejected login. Poll
read-only until the protected Offers page is usable; a loading page or an
account dashboard alone is not proof. Allow up to 45 seconds for redirects
and hydration before classifying an unresolved login.
During redirects, a temporarily missing `document.body` or destroyed execution
context calls for another read-only probe within that window, never another
login submission. Use `document.body?.innerText || ''` in status probes.

If SMS, push approval, CAPTCHA, device verification, password reset, or
ambiguous MFA appears, stop that issuer and report the action needed. Do not
change authentication settings. Continue the other issuer independently.
Clear filled inputs before taking an authentication screenshot; otherwise
omit the screenshot. Never capture HTML or input values containing credentials.

When a run-specific offers loop fails, debug its DOM expression without
signing out and logging back in as a recovery tactic. After any successful
submission, do not submit another login for that issuer in the same run.
Preserve the existing session until final sign-out or stop on the challenge.

## Offers and persistence

Read [offer-navigation.md](references/offer-navigation.md) for the existing
Chase/Amex UI observations and verification rules. These are examples to adapt
using live DOM inspection, not fixed selectors.

- Activate available offers on the currently selected card. Do not switch
  cards, purchase anything, change account settings, or accept unrelated terms.
- Visible unactivated controls are authoritative; stale dedup history must not
  cause a visible available offer to be skipped.
- Click each offer once; use the observed success state to verify it. A
  failed read-only probe may be retried within the verification window;
  an activation click must not be retried.
- Pause a randomized 3–6 seconds between activations; cap at 500 per issuer.
- This Hermes installation allows 40 agent turns. Batch read-only probes and,
  after inspecting the current DOM, write a temporary run-specific CDP loop
  for offer activation instead of spending several agent turns per offer.
  Import the maintained `cards_journal.Journal` helper from `runtime.md`;
  reserve every click before execution and record every verified iteration
  before the next click. Compile Python loop source before executing it. Keep
  JavaScript expressions inside `js(...)`; Python conditionals use Python
  expressions, never JavaScript regex literals. Do not wait for a
  batch to return: a timeout or final probe error would lose its progress.
  The loop must retain one-click verification, randomized waits, and
  stop-on-failure behavior. It may run as a
  terminal background process with progress polling. Do not reuse the retired
  deterministic driver or include credentials in the temporary script.
  Reserve enough turns to sign out and report; if the budget is nearly spent,
  stop with accurate partial progress instead of claiming full completion.
- Persist every verified activation with `journal.record(...)`; it updates the
  canonical run journal and the existing issuer index without inline shell code.
  Chase key: `chase::<offer_id>`; Amex key: `<card_label>::<merchant>::<deal>`.
- If one issuer is blocked, finish the available issuer and report partial
  success. Keep all completed activations when later work fails.

## Finish and report

After a live run, sign out each successfully authenticated issuer using its
observed Sign out/Log out control and verify the login wall. This is part of
the user-authorized cards workflow. Do not improvise account-setting changes
if the control cannot be found; report incomplete sign-out.

Finalize the journal as `completed`, `partial`, or `failed`, with end time,
per-issuer activation counts, unresolved failures, and sign-out outcomes.
Delivery status belongs to Hermes's cron execution history; do not claim
`telegramOk` or mark a message delivered before the scheduler sends it.

Return one concise roundup, keeping it below 3,800 UTF-16 units:

- Date, Chase count and verified merchant/deal lines.
- Amex selected card and count with verified merchant/deal lines.
- Any blocked issuer, MFA action, unverified activation, cap, or sign-out issue.
- A truthful total. If the list is long, keep totals and representative lines
  with “and N more activated offers”. If none are available, say nothing new.

Always return an outcome, including both-issuer failure. Do not emit `[SILENT]`.
If the journal is anything other than `completed`, put `[CRON_FAILURE]` on
the first line **by itself**, followed by the roundup. This includes partial
activation, unresolved sign-out, persistence gaps, and turn-limit exhaustion.
Reaching an offers page alone is not completion. The daily maintenance job
checks both scheduler status and the journal, preserving successful activations.
