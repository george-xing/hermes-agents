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

Use `https://global.americanexpress.com/offers/eligible` for Amex and
`https://secure.chase.com/web/auth/dashboard?navKey=reviewMerchantOffers` for
Chase. Record this navigation in the run journal. Inspect the resulting live
form metadata and wait for hydration before filling; if already authenticated,
proceed directly to offers.

## Username

Use the identifier returned by native `browser_vault_list`. The successful
Amex verification used the native HTML input setter plus bubbling events.
Use this on both banks rather than relying on window focus or keyboard input.
Inspect the page first; substitute the actual tab ID and observed selectors.
`frame_selector` is `"#logonbox"` for the observed Chase iframe, and `""` for
the observed top-level Amex form.

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
run's `journal_path` and the selected `issuer` (`chase` or `amex`). Do not omit
the reservation when adapting the example. If reservation raises, do not click;
a previously reserved attempt requires read-only reconciliation, not resubmission.
Never backfill a missing reservation after a click to hide an audit gap.

```python
# Submit once on the same verified bank tab
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path.home() / "dotfiles/cards/bin"))
from cards_journal import Journal
# Reopen this run's existing path: browser_exec variables do not persist.
journal = Journal(journal_path)
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
