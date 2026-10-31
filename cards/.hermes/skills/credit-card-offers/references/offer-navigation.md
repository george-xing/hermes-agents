# Offer navigation and verification

Live UI observations from the previous offers workflow. Authentication,
scheduling, credential handling, and reporting are defined in the Hermes
SKILL.md. Persist every verified activation immediately as that skill directs.

The shell snippets below retain historical DOM observations only. In the
active Hermes workflow, evaluate their JavaScript inside native `browser_exec`
with `js(...)`, after `switch_tab` and `current_tab` verification. Do not run
the URL-substring CDP helper: multiple bank tabs can match, and it does not
share the native session's exact target binding.

In managed `browser_exec`, return only serializable values from `js(...)`.
For presence checks, use `js("!!document.querySelector(selector)")`, not
`bool(js("document.querySelector(selector)"))`: returning a DOM node across CDP
can raise `Object reference chain is too long` after otherwise successful work.
Recover a failed read-only probe without repeating any activation click.

### 2. Activate Amex offers

Amex offers UI (observed 2026-05-14): a single scrolling list of offer "tiles" on `https://global.americanexpress.com/offers/eligible`. Each unactivated tile has an inline `+` icon button. After click, the button disappears from the DOM. Multi-card support is OUT OF SCOPE for v1 — process only the currently-selected card.

**Step 2.1 — Pre-flight.** Use `cdp-eval` to inspect Amex state:

```bash
TARGET_URL_SUBSTRING=americanexpress.com \
EXPRESSION='JSON.stringify({
  url: location.href,
  title: document.title,
  hasPwInput: !!document.querySelector("input[type=password]"),
  cardLabel: (document.querySelector("[data-testid=simple_switcher_display_label]")?.innerText || "").trim().split(/\s+/).join(" "),
  addBtnCount: document.querySelectorAll("[data-testid=merchantOfferListAddButton]").length
})' \
/Users/pattybot/dotfiles/cards/bin/lib/cdp-eval.sh
```

Parse the response value (a JSON string). Branch:
- `hasPwInput=true` or `title` contains "Log In": Amex is logged out → return
  to the Hermes skill's login procedure. A login wall alone is not an MFA or
  credential rejection. Do not screenshot filled credential inputs. Use the
  same observed tab ID in `browser_exec` and native vault fill throughout login
  and offers.
- `url` doesn't include `/offers/eligible`: navigate, wait 5s, re-probe:
  ```bash
  TARGET_URL_SUBSTRING=americanexpress.com EXPRESSION='location.href = "https://global.americanexpress.com/offers/eligible"; "navigating"' /Users/pattybot/dotfiles/cards/bin/lib/cdp-eval.sh
  sleep 5
  ```
- `addBtnCount=0` but session looks valid: page hasn't fully rendered, or the user has activated all offers. Sleep 3s and re-probe once. If still 0, treat as empty (success with 0 activated).

**Step 2.2 — The click loop.** Up to `AMEX_MAX_CLICKS` (default 500):

The Add button has `[data-testid="merchantOfferListAddButton"]` with empty innerText (icon button). To identify *which* offer the next button corresponds to, walk up the parent chain from the button to the per-offer container (the div with `border` in its class). Do not index raw innerText lines directly: a leading NEW badge shifts merchant and deal. Return the selected container's full visible text from the read-only probe, then call `cards_journal.parse_amex_offer(text)` in Python before reserving or clicking. Import it alongside Journal from `~/dotfiles/cards/bin`. It strips known leading badges and rejects incomplete metadata. Inspect its merchant/deal against the live tile before the first click. Use the returned fields for both the reservation key and recorded activation; do not independently reparse them after clicking. If metadata is ambiguous, stop before clicking and inspect the DOM read-only.

**Each iteration** extracts the live offer, clicks once, and verifies the
visible state transition:

a. **Extract the next offer's identity:**
```bash
TARGET_URL_SUBSTRING=americanexpress.com \
EXPRESSION='JSON.stringify((() => {
  const btn = document.querySelector("[data-testid=merchantOfferListAddButton]:not([data-cards-skip])");
  if (!btn) return null;
  let p = btn;
  for (let i = 0; i < 6 && p; i++) {
    const cls = (p.className && p.className.toString) ? p.className.toString() : "";
    if (cls.includes("border")) break;
    p = p.parentElement;
  }
  return { text: (p?.innerText || "").trim() };
})())' \
/Users/pattybot/dotfiles/cards/bin/lib/cdp-eval.sh
```

If `value` is the JSON string `"null"` (i.e. no more Add buttons remain): exit the loop normally — you've processed everything.

b. **Do not use dedup state to skip a visible Add button.** The live Amex
control is authoritative: if `[data-testid=merchantOfferListAddButton]` is
visible, that offer is not attached to the selected card and must be clicked.
Historical dedup can be stale after an interrupted run or an earlier verifier
bug. Use dedup only to avoid duplicate persisted records; it must never decide
whether to activate a live visible offer.

c. **Click + verify using the Added-to-Card counter.** Amex backfills the
visible list immediately, so the number of Add buttons may stay constant or
even increase after a successful click. Button count is not authoritative.

Before clicking, parse the integer in visible body text matching
`Added to Card (N)`. Give the chosen button a unique temporary marker, scroll
it into view, click it exactly once, then wait the randomized 3–6 seconds.
Poll for up to 15 seconds. Treat the click as verified when any of these occurs:

- `Added to Card (N)` increases;
- the original offer container visibly changes to an added/success state.

A disconnected button alone is inconclusive: a framework rerender can remove
it without activation. Recheck the counter or the same offer's explicit added
state, never repeat the click.

The counter increase is the preferred proof. A stable/increased total Add
button count does **not** mean failure because the list backfills. Once
verified, immediately record `{merchant, deal, card_id: cardLabel}` in memory.
If none of the valid transitions occurs within 15 seconds, screenshot with
`LABEL=amex-verify-fail`, record the partial progress already completed, and
stop the Amex section without retrying that click.

Stop conditions: hit `AMEX_MAX_CLICKS`, ran out of buttons, OR encountered a verification failure.

### 3. Activate Chase offers

For card selection, first read additional-cards.md. Run this loop separately
for the original selected Chase card and Amazon/Prime Visa, verifying the
selected masked card after every return to the hub. Reset counters/visited IDs
per card; reservation and journal keys must include the card label and last four.
If both scopes are the same card, process it once. Never mark Chase complete
until both scopes were checked. The historical loop below describes navigation,
not permission to ignore card identity.

Chase's current flow is hub tile → automatic activation page → hub. Chase can
also log you out of the offers area even when `/dashboard/overview` remains
authenticated, so always validate the offers page itself.

**Step 3.1 — Pre-flight:**

```bash
TARGET_URL_SUBSTRING=chase.com \
EXPRESSION='JSON.stringify({
  url: location.href,
  title: document.title,
  hasPwInput: !!document.querySelector("input[type=password]"),
  bodyLen: (document.body && document.body.innerText || "").length
})' \
/Users/pattybot/dotfiles/cards/bin/lib/cdp-eval.sh
```

If a password field appears (including inside Chase's same-origin `#logonbox`
iframe), the title indicates sign-in, or the login page is still loading,
follow the maintained skill's native login procedure. An ordinary login wall
is not by itself a reason to skip Chase. If this run already submitted a login,
continue read-only verification or report an expired session; do not log in
again. Never screenshot a form containing filled credentials.

Otherwise navigate to the offers hub using Chase's current official nav key:
```bash
TARGET_URL_SUBSTRING=chase.com \
EXPRESSION='location.href = "https://secure.chase.com/web/auth/dashboard?navKey=reviewMerchantOffers"; "navigating"' \
/Users/pattybot/dotfiles/cards/bin/lib/cdp-eval.sh
sleep 8  # Chase SPA hash-routing resolves to /merchantOffers/offer-hub; slow.
```

Re-probe. Expected end state: the title indicates Chase Offers and either at least one visible `[data-testid="commerce-tile"]` exists or the page clearly states that no offers are available. The current UI may duplicate an offer across a featured carousel and the full list, so deduplicate by detail URL/offer ID rather than tile index.

If the page is authenticated but has zero visible tiles, the read-only service below may be used as secondary evidence only:

```bash
TARGET_URL_SUBSTRING=chase.com \
EXPRESSION='fetch("/svc/wr/accounts/l4/dso/v2/offers/list", {credentials:"include"}).then(r => r.json()).then(d => JSON.stringify({code:d.code || null, count:Array.isArray(d.offers) ? d.offers.length : null}))' \
/Users/pattybot/dotfiles/cards/bin/lib/cdp-eval.sh
```

- If visible `commerce-tile` elements exist, process them regardless of the service result. Chase currently sometimes returns `ACCOUNTS:UnexpectedException` while the page is fully populated.
- `code="SUCCESS"` and `count=0`, with zero visible tiles and an authenticated page: valid empty success.
- Any other zero-tile result: categorize from the visible page (`auth`, `mfa`, `challenge`, or `dom`) and do not guess.

**Step 3.2 — Current direct-activation loop.** Up to
`CHASE_MAX_CLICKS` (default 500):

The July 2026 Chase UI activates an offer when its hub tile is clicked. It
then routes directly to `/merchantOffers/offer-activated/<offer-id>`. There is
no second Add button. Older instructions that inferred "already added" merely
because a hidden alert existed on the detail page are invalid.

On each iteration, inspect the hub and select the first unique tile that:

- has `[data-testid="commerce-tile"]`;
- has a stable `id` beginning with `CDLX:` or `FIGG:`;
- does not have a *visible* descendant
  `[data-testid="offer-tile-alert-container-success"]`;
- does not contain `Success Added` in its `aria-label`; and
- has not already been visited during this run.

The page contains a featured carousel plus the full list, so the same offer
can appear twice. Track visited IDs in memory. Never use tile index as identity.

Before clicking, capture:

- `offer_id = tile.id`;
- merchant and deal from the tile's nonempty text lines, stripping badge lines
  such as `New`, `Exclusive`, `Expiring soon`, and time-remaining lines; and
- the numeric `Added offers` counter from the page text.

Use `cards_journal.parse_chase_offer(tile_text)` before reserving the click.
It handles actual newlines and badges. Do not recreate splitting or reward
parsing inside nested source strings. The 2026-10-30 Prime Visa test exposed
double-escaped newline and regex patterns in generated loops.

For activated-page verification, import
`cards_journal.chase_activation_verified`. Obtain the small read-only probe
`js("({url:location.href,body:document.body?.innerText || ''})")` and pass it,
the captured offer ID, and the verified masked card label to the helper. It
requires the exact activated offer URL, visible Added to card text, and the
correct card label. Do not embed escaped JavaScript word-boundary regexes in
nested Python source. If the helper returns false, continue read-only polling
and the hub fallback below; never repeat the activation click. Validate the
helper against one real confirmation before starting a batch, and checkpoint
every verified activation. A parse failure before reservation is not a click.

Mark the ID visited in memory **before** clicking. Scroll the tile into view,
click exactly once, and wait the randomized 3–6 second cadence.

Verify activation using visible/current state, not mere DOM existence. Success
requires the URL to contain `/offer-activated/<offer_id>` and at least one of:

- a visible success/added marker on the activated page;
- accessible visible text indicating the offer was added; or
- after returning to the hub, the matching tile's `aria-label` contains
  `Success Added`, it has a visible success descendant, or the numeric Added
  counter increased.

Poll the activated page read-only for up to 15 seconds. Use a small,
exception-safe probe; do not make one large selector-heavy expression the
single point of failure. For example, interpolate the captured `offer_id` as a
JSON string (never raw shell text) into an expression shaped like:

```javascript
JSON.stringify((() => {
  try {
    const body = document.body?.innerText || "";
    return {
      url: location.href,
      loaded: !!document.body,
      addedText: /\bAdded to card\b/i.test(body),
      successText: /\bSuccess\b/i.test(body),
      text: body.slice(0, 1200)
    };
  } catch (error) {
    return {url: location.href, probeError: String(error)};
  }
})())
```

A `dom-error`, `probeError`, missing body, or still-loading document from one
read-only probe is **not** an activation failure and is **not** a second click.
Keep polling read-only within the same 15-second verification window. In
particular, if the URL already contains the exact captured offer ID, issue a
minimal body-text probe rather than abandoning verification. If the activated
page never exposes success text, return to the hub once and use the matching
tile/counter evidence above. Never click the offer again.

If verified, record `{merchant, deal, offer_id}` immediately in the in-memory
activated list. Only after the full read-only verification window and hub
fallback both fail should you screenshot, record one failure, and stop the
Chase section without retrying the click.

Return to the hub with the official URL below, wait for visible
`commerce-tile` elements, and repeat:

```bash
TARGET_URL_SUBSTRING=chase.com \
EXPRESSION='location.href = "https://secure.chase.com/web/auth/dashboard?navKey=reviewMerchantOffers"; "navigating"' \
/Users/pattybot/dotfiles/cards/bin/lib/cdp-eval.sh
sleep 5
```

When no unvisited, non-success tile remains, Chase is exhausted successfully.
