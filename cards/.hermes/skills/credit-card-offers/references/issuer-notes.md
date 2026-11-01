# Issuer notes for `needs_judgment`

Read this only when a helper returns `needs_judgment`. Its `page` summary holds
the origin, path, title, headings, input metadata and button texts (no values).
Inspect further with small read-only `js(...)` probes that return plain values:
`js("!!document.querySelector(sel)")`, never a DOM node, and
`document.body?.innerText || ''` because redirects can briefly remove the body.
Then either retry the helper with a selector `overrides={...}` or record the
issuer's outcome with `journal.fail(issuer, reason, status=...)` and move on.
Selectors and URLs live in `BANKS` in `cards_browser.py`, with observed dates.

## Login and tabs

- Several bank tabs: pick the one on the issuer's origin that this task uses
  (the horse emoji marks the agent's tab) and call `attach(tab_id=...)`.
- Login page on the wrong origin: the vault binds exactly. Chase must log in on
  `https://secure.chase.com` (its form is in the same-origin `#logonbox`
  iframe), Amex on `https://www.americanexpress.com`, BofA on the
  `https://www.bankofamerica.com` homepage widget, never `secure.`. Do not add
  vault origins or guess deep links.
- BofA homepage (2026-10-31): `#oid`, `#pass`, `#secure-signin-submit`, no
  form element, no iframe. After the first login it shows saved-user-ID mode
  ("Log in with a different user ID"); do not switch modes or use passkeys.
  Its widget sits in a `.fade-in` container that never renders while the mini
  is at the lock screen; `prepare_login` removes that class once and otherwise
  reports `blocked_fields_hidden`.
- A rejection already on screen **before** this run submitted is old page
  state, not this run's rejection.
- Unclear after submit: the login may still be hydrating, or the bank may show
  a page the classifier does not know. Probe read-only. Offers page → call
  `journal.issuer(issuer, offers_page_verified=True)` and continue. Identity
  check → record it as below. Never submit again.

## Identity verification (observed)

- Amex `/en-us/account/two-step-verification/verify`, "Choose a Method to
  Verify Your Identity" (Text Message / Email).
- BofA `secure.bankofamerica.com/auth/signon/#/authcode`, "Get authorization
  code" (Text message / Phone call).

Record `journal.fail(issuer, "mfa", status="blocked_mfa", mfa={"page": ...,
"path": ..., "options": [...], "remember_device_option": bool})` without
personal details. The roundup turns it into the user's action line.

## Offers

- Chase (2026-11-01): hub tiles `[data-testid=commerce-tile]` with ids
  `CDLX:`/`FIGG:`; clicking a tile activates it and routes to
  `/merchantOffers/offer-activated/<id>`. The featured carousel repeats tiles.
  The card is the `[data-testid=select-credit-card-account]` control; each card
  has its own `offer-hub?accountId=` route, and the generic `navKey` URL
  reopens Chase's default card. Chase can drop the offers area while
  `/dashboard/overview` still works. With zero tiles on an authenticated page,
  `fetch("/svc/wr/accounts/l4/dso/v2/offers/list", {credentials:"include"})`
  returning `SUCCESS` with no offers is valid empty evidence; visible tiles win.
- Amex (2026-11-01): `[data-testid=merchantOfferListAddButton]` icon buttons;
  the visible list backfills after a click, so only the "Added to Card (N)"
  counter proves an activation. A leading NEW badge is not the merchant.
- A tile the parser rejects (`unparsed_tile`) stops the loop before any click;
  report it rather than hand-clicking.

## Sign-out

- Chase `#brand_bar_sign_in_out` may land on the public logout page; its
  "You have successfully signed out" text is valid proof.
- Amex `#gnav_logout` returns to the login wall.
- BofA's control was never observed; the helper clicks the visible "Log Out" /
  "Sign Out" text. If it is missing, find it read-only and pass
  `overrides={"sign_out": "#observed"}`.
