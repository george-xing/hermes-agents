# Native login interaction

Use one named `browser_exec` session on cards Chrome port 19223. These are
interaction examples for live, observed controls, not a separate login driver.
Do not retrieve passwords in Python or JavaScript; only `browser_vault_fill`
may resolve and inject them.

`browser_exec` runs **Python**. `js(...)` contains **JavaScript**. Helpers
`switch_tab`, `current_tab`, `goto_url`, and `click_at_xy` exist only in Python.
Never put `current_tab()` or `current_tab?.target_id` inside JavaScript.
Use Python `*args`, not JavaScript `...args`, if unpacking Python arguments.

## Start a fresh login document

At the start of each new run, after choosing the exact existing issuer tab,
**navigate that tab to the protected offers destination before inspecting or
filling the login form**, even if it already shows a login wall. Old persistent
tabs can retain days-old login documents whose submit action has no useful
response. Do not infer freshness from `document.readyState === 'complete'`.
Do this once before credential entry; never navigate/reload after filling.

```python
# Start the current run from the protected offers destination
import time
switch_tab(target_id)
assert current_tab()["target_id"] == target_id
goto_url(protected_offers_url)
time.sleep(5)
print(js("({origin:location.origin,path:location.pathname,ready:document.readyState,documentAgeSeconds:Math.round((Date.now()-performance.timeOrigin)/1000)})"))
```

Use `https://global.americanexpress.com/offers/eligible` for Amex,
`https://secure.chase.com/web/auth/dashboard?navKey=reviewMerchantOffers` for
Chase, and `https://www.bankofamerica.com/` for BofA (its homepage sign-in
widget; see additional-cards.md). Record this navigation in the run journal.
Inspect the resulting live form metadata and wait for hydration before filling;
if already authenticated, proceed directly to offers.

## Make the login fields fill-ready

The cards Chrome normally runs while the mini's console session sits at the
lock screen, so every tab reports `document.visibilityState === "hidden"`.
`browser_vault_fill` skips any input whose computed `display` is `none`,
whose `visibility` is `hidden`, that is disabled/read-only, or that has no
client rects. `offsetWidth`/`getClientRects` alone are **not** a visibility
check: an element under `visibility:hidden` still has a box. Run this block on
the issuer tab after the fresh navigation and before the username entry and
native fill; never navigate between it and the fill. It activates the tab,
then polls up to 15 seconds using the vault inspector's own test.

BofA only: its homepage wraps the sign-in widget in an inner
`#top.sparta-layout.fade-in` container that is revealed only once the page
renders, which never happens in a hidden window (observed 2026-10-31). If the
fields are still hidden after the wait, the block removes `fade-in` from the
container(s) holding the observed fields. That is a presentation class only;
it reads no values and changes no form state. If the fields still fail, stop
that issuer with failure `login fields hidden`; do not retry or force anything else.

```python
# Make the observed login fields pass the native vault visibility test
import json, time
switch_tab(target_id, activate=True)
assert current_tab()["target_id"] == target_id
READY = """((selectors, frameSelector, reveal) => {
  const d = frameSelector ? document.querySelector(frameSelector)?.contentDocument : document;
  if (!d || d.location.origin !== location.origin) return {ready:false, fields:[], revealed:0};
  const w = d.defaultView;
  const fillable = (e) => {
    if (!e || e.disabled || e.readOnly) return false;
    const s = w.getComputedStyle(e);
    return !(s.display === 'none' || s.visibility === 'hidden' || e.getClientRects().length === 0);
  };
  const els = selectors.map((s) => d.querySelector(s));
  let revealed = 0;
  if (reveal && !els.every(fillable)) {
    for (const f of d.querySelectorAll('.fade-in')) {
      if (els.some((e) => e && f.contains(e))) { f.classList.remove('fade-in'); revealed += 1; }
    }
  }
  return {ready: els.every(fillable), fields: els.map(fillable), revealed, visibilityState: d.visibilityState};
})(%s, %s, %s)"""
args = (json.dumps([username_selector, password_selector]), json.dumps(frame_selector))
state = js(READY % (*args, "false"))
for _ in range(15):
    if state["ready"]:
        break
    time.sleep(1)
    state = js(READY % (*args, "false"))
if not state["ready"] and issuer == "bofa":
    state = js(READY % (*args, "true"))
print(state)  # Booleans and counts only
assert state["ready"], "login fields hidden"
```

Record `fields_ready` (and `fade_in_revealed` when nonzero) in the issuer
journal. On assertion failure record status `blocked_fields_hidden`.

## Username

Use the identifier returned by native `browser_vault_list`. The successful
Amex verification used the native HTML input setter plus bubbling events.
Use this on every bank rather than relying on window focus or keyboard input.
Inspect the page first; substitute the actual tab ID and observed selectors.
`frame_selector` is `"#logonbox"` for the observed Chase iframe, and `""` for
the observed top-level Amex form and BofA homepage widget.

```python
# Enter the native vault identifier in the observed control
import json
switch_tab(target_id)
assert current_tab()["target_id"] == target_id
result = js("""((selector, identifier, frameSelector) => {
  const d = frameSelector ? document.querySelector(frameSelector)?.contentDocument : document;
  if (!d || d.location.origin !== location.origin) return {filled:false};
  const input = d.querySelector(selector);
  if (!input || input.type === 'password') return {filled:false};
  const w = d.defaultView;
  Object.getOwnPropertyDescriptor(w.HTMLInputElement.prototype, 'value').set.call(input, identifier);
  input.dispatchEvent(new w.Event('input', {bubbles:true}));
  input.dispatchEvent(new w.Event('change', {bubbles:true}));
  return {filled:input.value === identifier};
})(%s, %s, %s)""" % (json.dumps(username_selector), json.dumps(identifier), json.dumps(frame_selector)))
assert result["filled"]
print(result)  # Boolean only, no input values
```

Then call native `browser_vault_fill` with the observed handle, exact `target_id`,
and Chase `frame_selector` when applicable. Require success, exactly one password
field, and the expected tab in its receipt. Never fill passwords with this snippet.

After password fill, recheck that the username still equals the vault identifier
and that the observed password field is nonempty; return **booleans only**, never
input values. A framework rerender can change the username after an earlier
successful entry. If the username no longer matches, reapply the nonsecret
identifier with the setter above and verify it before the first submission.
Do not submit with a mismatched or empty username. Do not refill or read back
the password through a generic tool.

## Submit once

Re-select the exact tab, verify the bank origin and observed login controls, and
record the single submission attempt in the run journal **before** this click.
Use a DOM click on the observed submit control. This is the method that reached
Amex's authenticated offers page in the verified native run. Do not improvise
coordinate clicks on a bank form or treat a click-call return as bank acceptance.
Keep reservation and click in the **same browser_exec call**, using the existing
run's `journal_path` and the selected `issuer` (`chase`, `amex` or `bofa`). Do not omit
the reservation when adapting the example. If reservation raises, do not click;
a previously reserved attempt requires read-only reconciliation, not resubmission.
Never backfill a missing reservation after a click to hide an audit gap.

`reserve_login` also enforces one issuer at a time: it refuses while another
issuer that submitted is still `status: running` (record its outcome first:
completed after sign-out, or a blocked status), and it refuses within 60
seconds of another issuer's submit. The block sleeps for the remaining spacing
first. A spacing or ordering refusal happens before any click; fix the cause
and rerun the same block.

```python
# Submit once on the same verified bank tab
import json
import time
from pathlib import Path
import sys
sys.path.insert(0, str(Path.home() / "dotfiles/cards/bin"))
from cards_journal import Journal
# Reopen this run's existing path: browser_exec variables do not persist.
journal = Journal(journal_path)
time.sleep(journal.login_wait_seconds(issuer))  # Keep issuer logins >= 60s apart.
switch_tab(target_id)
assert current_tab()["target_id"] == target_id
journal.reserve_login(issuer)  # Durable reservation MUST succeed before js/click.
clicked = js("""((selector, frameSelector, expectedOrigin) => {
  if (location.origin !== expectedOrigin) return false;
  const d = frameSelector ? document.querySelector(frameSelector)?.contentDocument : document;
  if (!d || d.location.origin !== expectedOrigin) return false;
  const button = d.querySelector(selector);
  if (!button || button.disabled) return false;
  button.click();
  return true;
})(%s, %s, %s)""" % (json.dumps(submit_selector), json.dumps(frame_selector), json.dumps(expected_origin)))
assert clicked
```

Poll read-only for up to 45 seconds with `document.body?.innerText || ''` while
redirects hydrate. A probe error does not justify another submission. Confirm
the actual protected offers page; stop on rejection, verification, or CAPTCHA.

## Identity verification pages

Observed challenges: Amex
`/en-us/account/two-step-verification/verify` "Choose a Method to Verify Your
Identity" (Text Message / Email), and BofA
`secure.bankofamerica.com/auth/signon/#/authcode` "Get authorization code"
(Text message / Phone call). Never choose a method, request a code, or call
`browser_vault_enter_code` in a scheduled run: a 3 a.m. text or call cannot be
answered and repeated requests raise the bank's risk score. Record, without
personal details, `mfa={"page": "<issuer> <page heading>", "path": location.pathname,
"options": [visible method names], "remember_device_option": <bool>}`, where
the boolean is true only if the page visibly offers "Remember this device" /
"Trust this browser" or similar. Set status `blocked_mfa` and move on.
