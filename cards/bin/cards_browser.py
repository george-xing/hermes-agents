"""Deterministic bank steps for the cards workflow, run inside Hermes browser_exec.

Each call is stateless: everything it needs between calls lives in the run
journal (cards_journal). Bind the browser_exec helpers explicitly so tests can
pass a fake::

    bank = Bank("chase", journal_path, harness=globals(), turn=7)
    print(bank.attach())

Every method returns a small dict with a ``status``. Results never contain
passwords, input values, or card digits beyond the masked label the bank shows.
Passwords are only ever filled by native ``browser_vault_fill`` between
``prepare_login`` and ``submit_login``.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import random
import re
import time
from urllib.parse import urlsplit

import cards_journal
from cards_journal import Journal

# One row per bank. "observed" dates come from the live runs that last used
# each value; None means the control was never observed and the agent decides.
BANKS = {
    "chase": {
        "name": "Chase",
        "observed": "2026-11-01",
        "destination": "https://secure.chase.com/web/auth/dashboard?navKey=reviewMerchantOffers",
        "tab_origins": ("https://secure.chase.com", "https://www.chase.com"),
        "login_origin": "https://secure.chase.com",
        "frame": "#logonbox",
        "username": "#userId-input-field-input",
        "password": "#password-input-field-input",
        "submit": "#signin-button",
        "sign_out": "#brand_bar_sign_in_out",
        "card_switcher": "[data-testid=select-credit-card-account]",
        "card_option": "[role=option]",
        "tile": "[data-testid=commerce-tile]",
        "tile_success": "[data-testid=offer-tile-alert-container-success]",
        "offers_ready": ("(() => location.href.includes('merchantOffers') && "
                         "(document.querySelectorAll('[data-testid=commerce-tile]').length > 0 || "
                         "/no (available )?offers/i.test(document.body?.innerText || '')))()"),
    },
    "amex": {
        "name": "Amex",
        "observed": "2026-11-01",
        "destination": "https://global.americanexpress.com/offers/eligible",
        "tab_origins": ("https://global.americanexpress.com", "https://www.americanexpress.com"),
        "login_origin": "https://www.americanexpress.com",
        "frame": "",
        "username": "#eliloUserID",
        "password": "#eliloPassword",
        "submit": "#loginSubmit",
        "sign_out": "#gnav_logout",
        "card_label": "[data-testid=simple_switcher_display_label]",
        "add_button": "[data-testid=merchantOfferListAddButton]",
        "offers_ready": ("(() => location.pathname.startsWith('/offers') && "
                         "(!!document.querySelector('[data-testid=merchantOfferListAddButton]') || "
                         "/Added to Card \\(\\d+\\)/.test(document.body?.innerText || '')))()"),
    },
    "bofa": {
        "name": "BofA",
        "observed": "2026-10-31",
        # Homepage widget only: the saved Login allows www.bankofamerica.com, never secure.
        "destination": "https://www.bankofamerica.com/",
        "tab_origins": ("https://www.bankofamerica.com", "https://secure.bankofamerica.com"),
        "login_origin": "https://www.bankofamerica.com",
        "frame": "",
        "username": "#oid",
        "password": "#pass",
        "submit": "#secure-signin-submit",
        "reveal_fade_in": True,
        "sign_out": None,  # Never observed; the visible "Log Out"/"Sign Out" text control is used.
        # The Deals site has no sign-out; return here first (agent-observed 2026-11-05).
        "accounts_url": "https://secure.bankofamerica.com/myaccounts/brain/redirect.go?target=accountsoverview&request_locale=en-us",
        # The Deals route is still undiscovered, so a signed-in account page is the end of login.
        "offers_ready": ("(() => location.origin === 'https://secure.bankofamerica.com' && "
                         "!location.pathname.includes('/auth/') && "
                         "/(log|sign) ?(out|off)/i.test(document.body?.innerText || ''))()"),
    },
}

MAX_CLICKS = 500
LOGIN_POLL_SECONDS = 45
VERIFY_SECONDS = 15
SIGN_OUT_SECONDS = 40
FILL_READY_SECONDS = 15
INTERSTITIAL_SECONDS = 10
DEALS_SECONDS = 45
MAX_INTERSTITIAL_CLICKS = 3  # Per issuer and run: a pop-up that keeps coming back is the user's to look at.

MFA_TEXT = re.compile(r"verify your identity|confirm your identity|two-step verification|one-time (pass)?code"
                      r"|authorization code|security code|we don.t recognize (this|your) (device|computer)", re.I)
MFA_PATHS = ("/two-step-verification", "/authcode", "/identity")
REJECTED_TEXT = re.compile(r"(user ?id|username) or password (is|was|are|you entered) (incorrect|invalid|not)"
                           r"|doesn.t match our records|does not match our records|can.t find that username"
                           r"|information you entered (doesn.t|does not) match|incorrect password", re.I)
CAPTCHA_TEXT = re.compile(r"captcha|verify you are (a )?human|i.m not a robot", re.I)
SIGNED_OUT_TEXT = re.compile(r"(successfully|you.ve|you have) (been )?(signed|logged) (out|off)", re.I)
# BofA's Log Out lands on an offers page that says neither "signed out" nor shows a login form (2026-11-13).
BOFA_SIGNED_OUT = ("https://secure.bankofamerica.com", "/secure-offers-saso/",
                   re.compile(r"thanks for choosing bank of america", re.I))


def mask(text):
    """Keep at most the last four digits of any long digit run (card or account numbers)."""
    return re.sub(r"\d{8,}", lambda m: "…" + m.group(0)[-4:], str(text))


def utf16_len(text):
    return len(text.encode("utf-16-le")) // 2


def login_outcome(probe, issuer):
    """Classify one read-only probe after the login click; None means keep polling."""
    if probe.get("ready"):
        return "signed_in" if issuer == "bofa" else "offers_ready"
    body = probe.get("body") or ""
    if probe.get("captcha") or CAPTCHA_TEXT.search(body):
        return "captcha"
    if any(p in (probe.get("path") or "") for p in MFA_PATHS) or MFA_TEXT.search(body):
        return "mfa"
    if REJECTED_TEXT.search(body):
        return "rejected"
    return None


def signed_out(probe, issuer):
    """Positive evidence of a login wall. A missing password input alone is not proof."""
    body = probe.get("body") or ""
    if SIGNED_OUT_TEXT.search(body):  # Includes Chase's public logout page.
        return True
    origin = probe.get("origin") or ""
    if issuer == "bofa" and _bofa_signed_out_page(probe):
        return True
    return bool(probe.get("password_input")) and origin in BANKS[issuer]["tab_origins"] and bool(
        re.search(r"\b(log ?in|sign ?in)\b", body, re.I))


def _bofa_signed_out_page(probe):
    origin, path, text = BOFA_SIGNED_OUT
    return (probe.get("origin") == origin and (probe.get("path") or "").startswith(path)
            and bool(text.search((probe.get("title") or "") + "\n" + (probe.get("body") or ""))))


def _bare_url(url):
    """Origin and path only: query strings and fragments can carry tokens."""
    parts = urlsplit(url or "")
    return f"{parts.scheme}://{parts.netloc}{parts.path}"[:200] if parts.scheme else (url or "")[:40]


def _js_args(*values):
    return ", ".join(json.dumps(v) for v in values)


# Read-only page summary for needs_judgment: metadata only, never input values.
SUMMARY_JS = """(() => {
  const text = (e) => (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 60);
  const seen = (e) => e.getClientRects().length > 0;
  return {
    origin: location.origin, path: location.pathname, title: document.title.slice(0, 80),
    headings: [...document.querySelectorAll('h1,h2')].filter(seen).map(text).filter(Boolean).slice(0, 4),
    inputs: [...document.querySelectorAll('input')].filter(seen).slice(0, 8)
      .map((e) => ({type: e.type, id: e.id, name: e.name})),
    buttons: [...document.querySelectorAll('button,a[role=button],input[type=submit]')].filter(seen)
      .map((e) => (e.innerText || e.getAttribute('aria-label') || '').trim().slice(0, 40)).filter(Boolean).slice(0, 8),
    frames: [...document.querySelectorAll('iframe')].map((f) => f.id).filter(Boolean).slice(0, 4)
  };
})()"""

# The same visibility test browser_vault_fill applies to inputs.
READY_JS = """((selectors, frameSelector, reveal) => {
  const d = frameSelector ? document.querySelector(frameSelector)?.contentDocument : document;
  if (!d || d.location.origin !== location.origin) return {ready: false, found: [false, false], fields: [false, false], revealed: 0};
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
  return {ready: els.every(fillable), found: els.map((e) => !!e), fields: els.map(fillable), revealed};
})(%s)"""

SET_USERNAME_JS = """((selector, identifier, frameSelector) => {
  const d = frameSelector ? document.querySelector(frameSelector)?.contentDocument : document;
  if (!d || d.location.origin !== location.origin) return false;
  const input = d.querySelector(selector);
  if (!input || input.type === 'password') return false;
  const w = d.defaultView;
  Object.getOwnPropertyDescriptor(w.HTMLInputElement.prototype, 'value').set.call(input, identifier);
  input.dispatchEvent(new w.Event('input', {bubbles: true}));
  input.dispatchEvent(new w.Event('change', {bubbles: true}));
  return input.value === identifier;
})(%s)"""

# Booleans only: the password is checked for presence, never read back.
CHECK_FIELDS_JS = """((user, pass, identifier, frameSelector) => {
  const d = frameSelector ? document.querySelector(frameSelector)?.contentDocument : document;
  if (!d || d.location.origin !== location.origin) return {username: false, password: false};
  const u = d.querySelector(user), p = d.querySelector(pass);
  return {username: !!u && u.value === identifier, password: !!p && p.type === 'password' && p.value.length > 0};
})(%s)"""

CLICK_JS = """((selector, frameSelector, expectedOrigin) => {
  if (location.origin !== expectedOrigin) return false;
  const d = frameSelector ? document.querySelector(frameSelector)?.contentDocument : document;
  if (!d || d.location.origin !== expectedOrigin) return false;
  const button = d.querySelector(selector);
  if (!button || button.disabled) return false;
  button.click();
  return true;
})(%s)"""

# Formatted with the bank's offers_ready expression (inlined: eval could trip a page CSP).
LOGIN_PROBE_JS = """((frameSelector) => {
  const body = document.body?.innerText || '';
  const d = frameSelector ? document.querySelector(frameSelector)?.contentDocument : null;
  const frameBody = d ? (d.body?.innerText || '') : '';
  return {path: location.pathname, ready: !!(%s), body: (body + '\\n' + frameBody).slice(0, 3000),
          captcha: !!document.querySelector('iframe[src*=captcha],[class*=captcha],[id*=captcha]')};
})(%s)"""

MFA_DETAILS_JS = """(() => {
  const seen = (e) => e.getClientRects().length > 0;
  const heading = [...document.querySelectorAll('h1,h2')].find(seen)?.innerText.trim().slice(0, 80) || document.title.slice(0, 80);
  const options = [...document.querySelectorAll('label,button,[role=radio]')].filter(seen)
    .map((e) => (e.innerText || '').trim().split('\\n')[0].slice(0, 30))
    .filter((t) => /text|sms|email|call|phone|push|app/i.test(t)).slice(0, 5);
  const body = document.body?.innerText || '';
  return {heading, path: location.pathname, options,
          remember: /remember (this|my) (device|computer|browser)|trust this (device|browser)/i.test(body)};
})()"""

SIGN_OUT_CLICK_JS = """((selector) => {
  const seen = (e) => e && e.getClientRects().length > 0;
  let e = selector ? document.querySelector(selector) : null;
  if (!seen(e) || !/(sign|log) ?(out|off)/i.test(e.innerText || e.getAttribute('aria-label') || '')) {
    e = [...document.querySelectorAll('a,button,[role=button]')].find((x) => seen(x) && /^\\s*(sign|log)\\s*(out|off)\\s*$/i.test(x.innerText || ''));
  }
  if (!e) return false;
  e.click();
  return true;
})(%s)"""

SIGN_OUT_PROBE_JS = """(() => {
  const frame = document.querySelector('#logonbox')?.contentDocument;
  return {origin: location.origin, path: location.pathname, title: document.title.slice(0, 120),
          body: (document.body?.innerText || '').slice(0, 2000),
          password_input: !!document.querySelector('input[type=password]') || !!frame?.querySelector('input[type=password]')};
})()"""

# Visible modal pop-ups (outermost only), each marked so the click and the check hit the same one.
# Hidden dialogs stay in BofA's DOM (session timeout, promos), so rendering is checked, not presence.
INTERSTITIAL_PROBE_JS = """(() => {
  const seen = (e) => {
    if (!e || e.getClientRects().length === 0) return false;
    const s = getComputedStyle(e);
    return s.display !== 'none' && s.visibility !== 'hidden';
  };
  const text = (e, n) => (e.innerText || e.getAttribute('aria-label') || e.value || '').trim().replace(/\\s+/g, ' ').slice(0, n);
  const all = [...document.querySelectorAll('[role=dialog],[role=alertdialog],[aria-modal=true]')].filter(seen);
  return all.filter((d) => !all.some((o) => o !== d && o.contains(d))).slice(0, 3).map((d, i) => {
    const marker = 'cards-dialog-' + i + '-' + Math.random().toString(36).slice(2);
    d.setAttribute('data-cards-dialog', marker);
    const named = document.getElementById(d.getAttribute('aria-labelledby') || '');
    const heads = [...d.querySelectorAll('h1,h2,h3,[role=heading]')].filter(seen).map((h) => text(h, 100)).filter(Boolean);
    const title = (named && text(named, 100)) || heads.join(' — ') || text(d, 100);
    const buttons = [...d.querySelectorAll('button,a,[role=button],input[type=button],input[type=submit]')].filter(seen);
    buttons.forEach((b, j) => b.setAttribute('data-cards-button', marker + '-' + j));
    return {marker, title: title.slice(0, 160), buttons: buttons.map((b) => text(b, 40))};
  });
})()"""

INTERSTITIAL_CLICK_JS = """((marker, index) => {
  const b = document.querySelector('[data-cards-button="' + marker + '-' + index + '"]');
  if (!b) return false;
  b.click();
  return true;
})(%s)"""

INTERSTITIAL_GONE_JS = """((marker) => {
  const d = document.querySelector('[data-cards-dialog="' + marker + '"]');
  if (!d || d.getClientRects().length === 0) return true;
  const s = getComputedStyle(d);
  return s.display === 'none' || s.visibility === 'hidden';
})(%s)"""

# BofA's own links into the Deals site (absolute hrefs; cards_journal.bofa_deals_href vets them).
DEALS_LINKS_JS = """[...document.querySelectorAll('a')]
  .filter((a) => /^(cash back deals|view all deals)$/i.test((a.innerText || '').trim())).map((a) => a.href)"""

DEALS_PROBE_JS = """(() => {
  const body = document.body?.innerText || '';
  const tiles = document.querySelectorAll('[data-testid^="offerTile-"],[data-testid^="gridUpsideOfferTile-"],'
    + '[data-testid^="small-upside-offer-"]').length;
  const loading = (body.match(/^Loading (?!more offers).+$/gm) || []).map((l) => l.slice(8).trim()).slice(0, 12);
  return {origin: location.origin, tiles, loading,
          empty: /no offers found|don.t have any activated offers/i.test(body),
          expired: /session (has )?expired/i.test(body)};
})()"""

# Installed only by open_deals() for one navigation. The Deals site fetches each offer section only
# once an IntersectionObserver reports it on screen, and a window behind the lock screen never renders,
# so neither those callbacks nor requestAnimationFrame ever run (diagnosed 2026-11-10). On that host and
# only while the page is hidden, observed elements are reported as visible and frames come from a timer.
DEALS_SHIM_JS = """(() => {
  if (location.hostname !== 'deals.merchant-rewards.com') return;
  const hidden = () => document.visibilityState === 'hidden';
  const stats = window.__cardsShim = {readyState: document.readyState, hiddenAtInstall: hidden(),
                                      observers: 0, observed: 0, fired: 0, frames: 0};
  const NativeObserver = window.IntersectionObserver;
  if (NativeObserver) {
    window.IntersectionObserver = class extends NativeObserver {
      constructor(callback, options) {
        super(callback, options);
        stats.observers += 1;
        this.cardsCallback = callback;
        this.cardsTargets = new Set();
      }
      observe(target) {
        super.observe(target);
        this.cardsTargets.add(target);
        stats.observed += 1;
        if (!hidden()) return;
        setTimeout(() => {
          if (!this.cardsTargets.has(target)) return;
          stats.fired += 1;
          const rect = target.getBoundingClientRect();
          this.cardsCallback([{target, isIntersecting: true, intersectionRatio: 1, boundingClientRect: rect,
                               intersectionRect: rect, rootBounds: null, time: performance.now()}], this);
        }, 0);
      }
      unobserve(target) { this.cardsTargets.delete(target); super.unobserve(target); }
      disconnect() { this.cardsTargets.clear(); super.disconnect(); }
    };
  }
  const nativeFrame = window.requestAnimationFrame.bind(window);
  const nativeCancel = window.cancelAnimationFrame.bind(window);
  const timers = new Set();
  window.requestAnimationFrame = (callback) => {
    if (!hidden()) return nativeFrame(callback);
    const id = setTimeout(() => { timers.delete(id); stats.frames += 1; callback(performance.now()); }, 16);
    timers.add(id);
    return id;
  };
  window.cancelAnimationFrame = (id) => (timers.delete(id) ? clearTimeout(id) : nativeCancel(id));
})()"""

# Per-frame diagnostics when the Deals page does not load: no cookies, input values or query strings.
DEALS_FRAME_JS = """(() => {
  const loading = [...document.querySelectorAll('body *')]
    .filter((e) => e.children.length === 0 && /^Loading /.test((e.textContent || '').trim())).slice(0, 12)
    .map((e) => {
      const r = e.getBoundingClientRect(), section = e.closest('[data-testid^="section-"]');
      return {text: e.textContent.trim().slice(0, 60), section: section ? section.getAttribute('data-testid').slice(0, 60) : null,
              top: Math.round(r.top), height: Math.round(r.height), rendered: e.getClientRects().length > 0,
              busy: !!e.closest('[aria-busy="true"]')};
    });
  return {origin: location.origin, path: location.pathname, visibility: document.visibilityState, hidden: document.hidden,
          readyState: document.readyState, shim: window.__cardsShim || null,
          observerPatched: !!window.IntersectionObserver && /cardsCallback/.test(String(window.IntersectionObserver)),
          iframes: [...document.querySelectorAll('iframe')].map((f) => (f.src || '').split(/[?#]/)[0].slice(0, 120)).slice(0, 8),
          loading};
})()"""

AMEX_NEXT_JS = """((button, skip) => {
  const btn = [...document.querySelectorAll(button)].find((b) => !skip.includes(b.getAttribute('data-cards-key') || ''));
  if (!btn) return null;
  let p = btn;
  for (let i = 0; i < 6 && p; i++) {
    const cls = (p.className && p.className.toString) ? p.className.toString() : '';
    if (cls.includes('border')) break;
    p = p.parentElement;
  }
  const marker = 'cards-' + Math.random().toString(36).slice(2);
  btn.setAttribute('data-cards-target', marker);
  const counter = (document.body?.innerText || '').match(/Added to Card \\((\\d+)\\)/);
  return {marker, text: (p?.innerText || '').trim(), added: counter ? Number(counter[1]) : null};
})(%s)"""

AMEX_MARK_JS = """((marker, key) => {
  const b = document.querySelector('[data-cards-target="' + marker + '"]');
  if (b) b.setAttribute('data-cards-key', key);
  return !!b;
})(%s)"""

AMEX_CLICK_JS = """((marker) => {
  const b = document.querySelector('[data-cards-target="' + marker + '"]');
  if (!b) return false;
  b.scrollIntoView({block: 'center'});
  b.click();
  return true;
})(%s)"""

AMEX_VERIFY_JS = """((marker) => {
  const counter = (document.body?.innerText || '').match(/Added to Card \\((\\d+)\\)/);
  return {added: counter ? Number(counter[1]) : null};
})(%s)"""

CHASE_HUB_JS = """((tile, success, switcher, skip) => {
  const tiles = [...document.querySelectorAll(tile)];
  const next = tiles.find((e) => /^(CDLX:|FIGG:)/.test(e.id) && !skip.includes(e.id)
    && !(e.getAttribute('aria-label') || '').includes('Success Added')
    && ![...e.querySelectorAll(success)].some((s) => s.getClientRects().length > 0));
  return {url: location.href, selected: document.querySelector(switcher)?.innerText || null,
          total: tiles.length, tile: next ? {id: next.id, text: next.innerText} : null};
})(%s)"""

CHASE_TILE_DONE_JS = """((tile, success, id) => {
  const e = [...document.querySelectorAll(tile)].find((t) => t.id === id);
  return !!e && ((e.getAttribute('aria-label') || '').includes('Success Added')
    || [...e.querySelectorAll(success)].some((s) => s.getClientRects().length > 0));
})(%s)"""


class Bank:
    def __init__(self, issuer, journal_path, harness, turn=None, overrides=None,
                 sleep=time.sleep, clock=time.monotonic, rand=random.uniform):
        if issuer not in BANKS:
            raise ValueError("Unknown issuer")
        self.issuer = issuer
        self.journal = Journal(journal_path)
        self.turn = turn
        self.sleep, self.clock, self.rand = sleep, clock, rand
        self.cfg = dict(BANKS[issuer])
        self.overrides = dict(overrides or {})
        unknown = set(self.overrides) - set(self.cfg)
        if unknown:
            raise ValueError(f"Unknown selector override(s): {sorted(unknown)}")
        self.cfg.update(self.overrides)
        get = harness.get if isinstance(harness, dict) else lambda name: getattr(harness, name, None)
        self._js, self._switch_tab, self._current_tab = get("js"), get("switch_tab"), get("current_tab")
        self._list_tabs, self._goto_url, self._new_tab = get("list_tabs"), get("goto_url"), get("new_tab")
        self._cdp, self._drain_events = get("cdp"), get("drain_events")

    # -- bookkeeping -------------------------------------------------------------------------
    def _state(self):
        data = self.journal.read() if self.journal.path.exists() else {"issuers": {}}
        return data["issuers"].get(self.issuer, {})

    def _done(self, method, result):
        if self.overrides:
            with self.journal.transaction() as data:
                issuer = data["issuers"].setdefault(self.issuer, {"login_submissions": 0, "status": "running"})
                used = issuer.setdefault("selector_overrides", [])
                for key, value in sorted(self.overrides.items()):
                    entry = {"method": method, "key": key, "value": str(value)[:120]}
                    if entry not in used:
                        used.append(entry)
        detail = {k: result[k] for k in ("stop", "reason", "card") if k in result}
        if "activated" in result:
            detail["count"] = result["activated"]
        self.journal.log_call(self.issuer, method, result["status"], turn=self.turn, **detail)
        return result

    def _judgment(self, method, reason):
        try:
            page = self._js(SUMMARY_JS)
            page = json.loads(mask(json.dumps(page)))
        except Exception as exc:  # A failed summary must not hide the reason.
            page = {"error": type(exc).__name__}
        return self._done(method, {"status": "needs_judgment", "reason": reason, "page": page})

    def _attached(self):
        target = self._state().get("tab_id")
        if not target:
            return None
        self._switch_tab(target)
        return target if self._current_tab()["target_id"] == target else None

    def _origin(self):
        return self._js("location.origin")

    # -- attach --------------------------------------------------------------------------------
    def attach(self, tab_id=None):
        """Select the bank tab by exact origin, preferring this run's or the last verified tab."""
        tabs = self._list_tabs()
        origin_of = lambda url: "/".join(url.split("/", 3)[:3])
        candidates = [t for t in tabs if origin_of(t.get("url", "")) in self.cfg["tab_origins"]]
        ids = {t["target_id"] for t in tabs}
        current = self._state().get("tab_id")
        previous = self._previous_tab()
        created = False
        if tab_id:
            if tab_id not in ids:
                return self._done("attach", {"status": "needs_judgment", "reason": "tab_id not open"})
            target = tab_id
        elif current in ids:
            target = current  # Chosen or created earlier in this run, whatever it shows now.
        elif previous in {t["target_id"] for t in candidates}:
            target = previous
        elif len(candidates) == 1:
            target = candidates[0]["target_id"]
        elif not candidates:
            target = self._new_tab("about:blank")
            created = True
        else:
            return self._done("attach", {"status": "needs_judgment", "reason": "several bank tabs; pass tab_id",
                                         "tabs": [{"target_id": t["target_id"], "url": mask(origin_of(t["url"])),
                                                   "title": mask(t.get("title", ""))[:60]} for t in candidates]})
        self._switch_tab(target)
        if self._current_tab()["target_id"] != target:
            return self._judgment("attach", "switch_tab did not attach to the bank tab")
        self.journal.issuer(self.issuer, tab_id=target)
        return self._done("attach", {"status": "attached", "target_id": target, "created": created})

    def _previous_tab(self):
        for path in sorted(self.journal.path.parent.glob("*.json"), reverse=True):
            if path == self.journal.path:
                continue
            try:
                issuer = json.loads(path.read_text()).get("issuers", {}).get(self.issuer, {})
            except (OSError, ValueError):
                continue
            if issuer.get("tab_id"):
                return issuer["tab_id"]
        return None

    # -- login ---------------------------------------------------------------------------------
    def prepare_login(self, identifier):
        """Fresh navigation, fill-ready wait, username entry. Returns the native vault fill target."""
        state = self._state()
        if state.get("login_submissions"):
            return self._done("prepare_login", {"status": "already_submitted"})
        if self.turn is not None and not self.journal.budget_allows(self.issuer, self.turn):
            return self._done("prepare_login", {"status": "skipped_budget"})
        target = self._attached()
        if not target:
            return self._done("prepare_login", {"status": "needs_judgment", "reason": "call attach() first"})
        # Old persistent tabs keep days-old login documents, so always start fresh.
        self._goto_url(self.cfg["destination"])
        self.sleep(5)
        if self._ready():
            self.journal.issuer(self.issuer, fresh_navigation=True, offers_page_verified=True)
            return self._done("prepare_login", {"status": "already_authenticated", "target_id": target})
        if self._origin() != self.cfg["login_origin"]:
            return self._judgment("prepare_login", f"login page is not on {self.cfg['login_origin']}")
        self._switch_tab(target, activate=True)
        if self._current_tab()["target_id"] != target:
            return self._judgment("prepare_login", "lost the bank tab while activating it")
        fields = self._fill_ready()
        if not any(fields["found"]):
            return self._judgment("prepare_login", "login selectors matched nothing")
        if not fields["ready"]:
            self.journal.fail(self.issuer, "login fields hidden", status="blocked_fields_hidden",
                              fresh_navigation=True, fields_ready=False)
            return self._done("prepare_login", {"status": "blocked_fields_hidden"})
        filled = self._js(SET_USERNAME_JS % _js_args(self.cfg["username"], identifier, self.cfg["frame"]))
        if not filled:
            return self._judgment("prepare_login", "username control did not accept the identifier")
        self.journal.issuer(self.issuer, fresh_navigation=True, fields_ready=True, username_fill=True,
                            fade_in_revealed=fields["revealed"], login_prepared_at=cards_journal.now(),
                            prepared_target=target)
        return self._done("prepare_login", {"status": "ready_for_vault_fill", "target_id": target,
                                            "frame_selector": self.cfg["frame"]})

    def _fill_ready(self):
        args = _js_args([self.cfg["username"], self.cfg["password"]], self.cfg["frame"])
        state = self._js(READY_JS % (args + ", false"))
        for _ in range(FILL_READY_SECONDS):
            if state["ready"]:
                break
            self.sleep(1)
            state = self._js(READY_JS % (args + ", false"))
        # BofA's widget sits in a fade-in container that never renders in a hidden window.
        if not state["ready"] and self.cfg.get("reveal_fade_in"):
            state = self._js(READY_JS % (args + ", true"))
        return state

    def _ready(self):
        try:
            return bool(self._js(self.cfg["offers_ready"]))
        except Exception:
            return False

    def submit_login(self, identifier, vault_target_id):
        """One DOM click, reserved in the journal first, then up to 45 s of read-only polling."""
        state = self._state()
        refuse = lambda reason: self._done("submit_login", {"status": "refused", "reason": reason})
        if not state.get("login_prepared_at"):
            return refuse("prepare_login has not run for this issuer in this run")
        if state.get("login_submissions"):
            return refuse("login already submitted in this run; never resubmit")
        target = self._attached()
        if not target or target != state.get("prepared_target") or vault_target_id != target:
            return refuse("vault fill target does not match the prepared tab")
        args = _js_args(self.cfg["username"], self.cfg["password"], identifier, self.cfg["frame"])
        check = self._js(CHECK_FIELDS_JS % args)
        if not check["username"]:
            # A framework rerender can drop the username after the vault fill; reapply once.
            self._js(SET_USERNAME_JS % _js_args(self.cfg["username"], identifier, self.cfg["frame"]))
            check = self._js(CHECK_FIELDS_JS % args)
        if not (check["username"] and check["password"]):
            return refuse("username mismatch or empty password")
        self.journal.issuer(self.issuer, password_fill=True)
        wait = self.journal.login_wait_seconds(self.issuer)
        if wait:
            self.sleep(wait)
        try:
            self.journal.reserve_login(self.issuer)  # Durable before the click, or no click at all.
        except ValueError as exc:
            return refuse(str(exc))
        clicked = self._js(CLICK_JS % _js_args(self.cfg["submit"], self.cfg["frame"], self.cfg["login_origin"]))
        if not clicked:
            self.journal.fail(self.issuer, "login click not confirmed", login_result="unclear")
            return self._done("submit_login", {"status": "unclear", "reason": "submit control not clicked"})
        outcome, deadline = None, self.clock() + LOGIN_POLL_SECONDS
        while outcome is None and self.clock() < deadline:
            self.sleep(3)
            try:  # Redirects can destroy the context; that is another probe, never another click.
                probe = self._js(LOGIN_PROBE_JS % (self.cfg["offers_ready"], _js_args(self.cfg["frame"])))
            except Exception:
                continue
            outcome = login_outcome(probe, self.issuer)
        outcome = outcome or "unclear"
        if outcome in ("offers_ready", "signed_in"):
            self.journal.issuer(self.issuer, login_result=outcome, offers_page_verified=outcome == "offers_ready")
            return self._done("submit_login", {"status": outcome})
        if outcome == "mfa":
            details = self._js(MFA_DETAILS_JS)
            mfa = {"page": f"{self.cfg['name']} {details['heading']}", "path": details["path"],
                   "options": [mask(o) for o in details["options"]], "remember_device_option": details["remember"]}
            self.journal.fail(self.issuer, "mfa", status="blocked_mfa", login_result="mfa", mfa=mfa)
            return self._done("submit_login", {"status": "mfa", "mfa": mfa})
        if outcome in ("rejected", "captcha"):
            self.journal.fail(self.issuer, outcome, status=f"blocked_{outcome}", login_result=outcome)
            return self._done("submit_login", {"status": outcome})
        self.journal.issuer(self.issuer, login_result="unclear")
        return self._judgment("submit_login", "no offers page, challenge or rejection after 45 s")

    # -- offers --------------------------------------------------------------------------------
    def activate(self, card=None):
        """Click each visible offer once and verify it, until exhausted, deadline, cap or a failure."""
        if self.issuer == "bofa":
            return self._done("activate", {"status": "needs_judgment",
                                           "reason": "BofA offers are classified by the agent (additional-cards.md)"})
        state = self._state()
        if not (state.get("offers_page_verified") or state.get("login_result") == "offers_ready"):
            return self._done("activate", {"status": "refused", "reason": "offers page not verified in this run"})
        if not self._attached():
            return self._judgment("activate", "bank tab is not attached")
        if not self._ready():
            self._goto_url(self.cfg["destination"])
            for _ in range(10):
                self.sleep(2)
                if self._ready():
                    break
            else:
                return self._judgment("activate", "offers page did not load")
        deadline = self.clock() + cards_journal.LOOP_SECONDS
        if self.issuer == "amex":
            result = self._activate_amex(deadline)
        else:
            result = self._activate_chase(card, deadline)
        return self._done("activate", result)

    def _attempted(self):
        return set(self._state().get("attempted_offers", []))

    def _verified_keys(self):
        return {a["key"] for a in self.journal.read()["activations"] if a["issuer"] == self.issuer}

    def _pause_ok(self, deadline):
        if self.clock() >= deadline:
            return "deadline"
        if len(self._attempted()) >= MAX_CLICKS:
            return "cap"
        return None

    def _activate_amex(self, deadline):
        label = mask((self._js("document.querySelector(%s)?.innerText || ''" % json.dumps(self.cfg["card_label"]))
                      or "").strip().replace("\n", " "))
        if not label:
            return {"status": "needs_judgment", "reason": "selected Amex card label not found", "activated": 0}
        self.journal.issuer("amex", card_id=label)
        activated, skipped, stop = 0, [], None
        while stop is None:
            stop = self._pause_ok(deadline)
            if stop:
                break
            nxt = self._js(AMEX_NEXT_JS % _js_args(self.cfg["add_button"], skipped))
            if not nxt:
                stop = "exhausted"
                break
            try:
                offer = cards_journal.parse_amex_offer(nxt["text"])
            except ValueError:
                stop = "unparsed_tile"
                break
            if nxt["added"] is None:
                stop = "no_counter"  # "Added to Card (N)" is the proof; without it nothing is clicked.
                break
            key = f"{label}::{offer['merchant']}::{offer['deal']}"
            self._js(AMEX_MARK_JS % _js_args(nxt["marker"], key))
            if key in self._attempted():
                skipped.append(key)  # An earlier click on this key was never verified: probe only.
                continue
            self.journal.reserve_offer("amex", key)
            if not self._js(AMEX_CLICK_JS % _js_args(nxt["marker"])):
                stop = "click_not_confirmed"
                break
            self.sleep(self.rand(3, 6))
            if not self._poll(lambda: (self._js(AMEX_VERIFY_JS % _js_args(nxt["marker"]))["added"] or 0) > nxt["added"]):
                stop = "unverified"
                break
            self.journal.record("amex", key, merchant=offer["merchant"], deal=offer["deal"], card_id=label)
            activated += 1
        if stop == "exhausted" and not skipped:
            self.journal.issuer("amex", offers_done=True)
        return {"status": stop, "stop": stop, "activated": activated, "card": label,
                "uncertain": len([k for k in skipped if k not in self._verified_keys()])}

    def _poll(self, check, seconds=VERIFY_SECONDS):
        end = self.clock() + seconds
        while True:
            try:
                if check():
                    return True
            except Exception:
                pass  # A failed read-only probe is retried; the click never is.
            if self.clock() >= end:
                return False
            self.sleep(1)

    def _chase_labels(self):
        """Masked labels for the existing card (baseline from earlier runs) and Amazon/Prime Visa."""
        labels = {scope: info.get("card_id") for scope, info in self._state().get("cards", {}).items()}
        if not labels.get("existing"):
            for path in sorted(self.journal.path.parent.glob("*.json"), reverse=True):
                try:
                    cards = json.loads(path.read_text()).get("issuers", {}).get("chase", {}).get("cards", {})
                except (OSError, ValueError):
                    continue
                if cards.get("existing", {}).get("card_id"):
                    labels["existing"] = cards["existing"]["card_id"]
                    break
        return labels

    def _selected(self):
        return " ".join((self._js("document.querySelector(%s)?.innerText || ''"
                                  % json.dumps(self.cfg["card_switcher"])) or "").split())

    def _hub(self):
        try:
            return cards_journal.chase_card_hub(self._js("location.href"))
        except ValueError:
            return None

    def _select_card(self, label):
        """Pick the card in Choose account and return its accountId hub (the generic URL reopens the default card)."""
        if self._selected() == label and self._hub():
            return self._hub()
        self._js("document.querySelector(%s)?.click()" % json.dumps(self.cfg["card_switcher"]))
        self.sleep(1)
        options = self._js("[...document.querySelectorAll(%s)].map((e) => e.innerText.trim())"
                           % json.dumps(self.cfg["card_option"])) or []
        if sum(" ".join(o.split()) == label for o in options) != 1:
            return None
        self._js("[...document.querySelectorAll(%s)].find((e) => e.innerText.trim() === %s)?.click()"
                 % (json.dumps(self.cfg["card_option"]), json.dumps(label)))
        if not self._poll(lambda: self._selected() == label and self._hub(), seconds=8):
            return None
        return self._hub()

    def _amazon_label(self):
        switcher = self.cfg["card_switcher"]
        self._js("document.querySelector(%s)?.click()" % json.dumps(switcher))
        self.sleep(1)
        options = self._js("[...document.querySelectorAll(%s)].map((e) => e.innerText.trim())"
                           % json.dumps(self.cfg["card_option"])) or []
        self._js("document.querySelector(%s)?.click()" % json.dumps(switcher))  # Close the list again.
        matches = [" ".join(o.split()) for o in options if re.search(r"amazon|prime visa", o, re.I)]
        return matches[0] if len(matches) == 1 else None

    def _activate_chase(self, card, deadline):
        self.journal.issuer("chase", required_cards=["existing", "amazon"])
        coverage = self._state().get("cards", {})
        scopes = [card] if card else [s for s in ("existing", "amazon")
                                      if coverage.get(s, {}).get("status") != "completed"]
        labels = self._chase_labels()
        total, last = 0, {"status": "exhausted", "stop": "exhausted"}
        for scope in scopes:
            if scope not in ("existing", "amazon"):
                raise ValueError("card must be 'existing' or 'amazon'")
            label = labels.get(scope)
            if scope == "existing" and not label:
                # First run only: the card Chase shows by default becomes the baseline.
                current = self._selected()
                label = current if current and not re.search(r"amazon|prime visa", current, re.I) else None
            if scope == "amazon" and not label:
                label = self._amazon_label()
            if not label:
                self.journal.card("chase", scope, status="blocked", reason="card not found or ambiguous")
                last = {"status": "card_not_found", "stop": "card_not_found", "card": scope}
                break
            label = mask(label)
            if scope == "amazon" and label == labels.get("existing"):
                self.journal.card("chase", "amazon", status="completed", card_id=label, note="same card as existing")
                continue
            hub = self._select_card(label)
            if not hub:
                self.journal.card("chase", scope, status="blocked", card_id=label, reason="card could not be selected")
                last = {"status": "card_mismatch", "stop": "card_mismatch", "card": label}
                break
            self.journal.card("chase", scope, status="running", card_id=label)
            count, stop = self._chase_loop(label, hub, deadline)
            total += count
            verified = self._verified_keys()
            mine = sum(1 for k in verified if k.startswith(f"chase::{label}::"))
            uncertain = sum(1 for k in self._attempted() if k.startswith(f"chase::{label}::") and k not in verified)
            covered = stop == "exhausted" and not uncertain
            self.journal.card("chase", scope, status="completed" if covered else "partial",
                              activations=mine, scan=stop, uncertain=uncertain)
            last = {"status": stop, "stop": stop, "card": label, "uncertain": uncertain}
            if stop != "exhausted":
                break
        coverage = self._state().get("cards", {})
        if all(coverage.get(s, {}).get("status") == "completed" for s in ("existing", "amazon")):
            self.journal.issuer("chase", offers_done=True)
        return {**last, "activated": total}

    def _chase_loop(self, label, hub, deadline):
        count = 0
        while True:
            stop = self._pause_ok(deadline)
            if stop:
                return count, stop
            skip = [k.rsplit("::", 1)[1] for k in self._attempted() if k.startswith(f"chase::{label}::")]
            state = None
            for _ in range(12):
                try:
                    state = self._js(CHASE_HUB_JS % _js_args(self.cfg["tile"], self.cfg["tile_success"],
                                                             self.cfg["card_switcher"], skip))
                    if state["total"] and cards_journal.chase_card_selected(state, label):
                        break
                except Exception:
                    pass
                self.sleep(1)
            if not state:
                return count, "hub_not_loaded"
            if not state["total"]:  # Only an explicit "no offers" page counts as an empty card.
                return count, "exhausted" if self._ready() else "hub_not_loaded"
            if not cards_journal.chase_card_selected(state, label):
                return count, "card_mismatch"
            tile = state["tile"]
            if not tile:
                return count, "exhausted"
            try:
                offer = cards_journal.parse_chase_offer(tile["text"])
            except ValueError:
                return count, "unparsed_tile"
            key = f"chase::{label}::{tile['id']}"
            self.journal.reserve_offer("chase", key)
            if not self._js("((id) => { const e = document.getElementById(id); if (!e) return false; "
                            "e.scrollIntoView({block: 'center'}); e.click(); return true; })(%s)" % json.dumps(tile["id"])):
                return count, "click_not_confirmed"
            self.sleep(self.rand(3, 6))
            ok = self._poll(lambda: cards_journal.chase_activation_verified(
                self._js("({url: location.href, body: document.body?.innerText || ''})"), tile["id"], label))
            self._goto_url(hub)
            self.sleep(3)
            if not ok:  # Hub fallback: the tile itself shows the success state.
                ok = self._poll(lambda: self._js(CHASE_TILE_DONE_JS % _js_args(
                    self.cfg["tile"], self.cfg["tile_success"], tile["id"])), seconds=10)
            if not ok:
                return count, "unverified"
            self.journal.record("chase", key, merchant=offer["merchant"], deal=offer["deal"],
                                offer_id=tile["id"], card_id=label)
            count += 1

    def activate_one(self, key, selector, verified_js, **meta):
        """Generic click-once for agent-classified offers (BofA): reserve, click, verify, record."""
        if not self._attached():
            return self._judgment("activate_one", "bank tab is not attached")
        if key in self._attempted():
            return self._done("activate_one", {"status": "refused", "reason": "already attempted; probe only"})
        if not self._js("!!document.querySelector(%s)" % json.dumps(selector)):
            return self._judgment("activate_one", "offer control not found")
        self.journal.reserve_offer(self.issuer, key)
        if not self._js("(() => { const e = document.querySelector(%s); if (!e) return false; e.click(); return true; })()"
                        % json.dumps(selector)):
            return self._done("activate_one", {"status": "click_not_confirmed"})
        self.sleep(self.rand(3, 6))
        if not self._poll(lambda: bool(self._js(verified_js))):
            return self._done("activate_one", {"status": "unverified"})
        self.journal.record(self.issuer, key, **meta)
        return self._done("activate_one", {"status": "verified"})

    # -- pop-ups -------------------------------------------------------------------------------
    def clear_interstitial(self):
        """Dismiss known, non-mutating pop-ups (BofA's profile review) with their dismiss control.

        Read-only when nothing is showing. An unknown pop-up, or one without a safe dismiss
        control, is never clicked: the issuer is recorded as blocked and the agent signs out.
        """
        state = self._state()
        if not state.get("login_submissions"):
            return self._done("clear_interstitial", {"status": "refused", "reason": "no login in this run"})
        if not self._attached():
            return self._judgment("clear_interstitial", "bank tab is not attached")
        dismissed = []
        while True:
            dialogs = self._js(INTERSTITIAL_PROBE_JS) or []
            if not dialogs:
                return self._done("clear_interstitial", {"status": "dismissed" if dismissed else "clear",
                                                         "dismissed": dismissed})
            choices = []
            for dialog in dialogs:
                title, buttons = mask(dialog["title"]), [mask(b) for b in dialog["buttons"]]
                choices.append((dialog, title, buttons, *cards_journal.choose_interstitial_action(title, buttons)))
            blocked = next((c for c in choices if c[3] == "blocked"), None)
            if blocked:
                return self._blocked_interstitial(blocked[1], blocked[2], blocked[4])
            dialog, title, buttons, _, label, action = choices[0]
            clicks = self._state().get("interstitial_clicks", 0)
            if clicks >= MAX_INTERSTITIAL_CLICKS:
                return self._blocked_interstitial(title, buttons, f"still showing after {clicks} dismissals")
            self.journal.issuer(self.issuer, interstitial_clicks=clicks + 1)  # Durable before the click.
            index = [" ".join(b.split()) for b in buttons].index(label)
            if not self._js(INTERSTITIAL_CLICK_JS % _js_args(dialog["marker"], index)):
                return self._blocked_interstitial(title, buttons, f"{label!r} could not be clicked")
            if not self._poll(lambda: self._js(INTERSTITIAL_GONE_JS % _js_args(dialog["marker"])),
                              seconds=INTERSTITIAL_SECONDS):
                return self._blocked_interstitial(title, buttons, f"still showing after {label!r}")
            entry = {"title": title, "action": action, "label": label, "dismissed": True, "at": cards_journal.now()}
            self.journal.issuer(self.issuer, interstitial=entry,
                                interstitials=self._state().get("interstitials", []) + [entry])
            dismissed.append({"title": title, "action": action})

    def _blocked_interstitial(self, title, buttons, reason):
        info = {"title": title, "buttons": buttons[:8], "reason": reason, "dismissed": False}
        self.journal.fail(self.issuer, "interstitial_blocked", status="partial", interstitial=info)
        return self._done("clear_interstitial", {"status": "interstitial_blocked", **info})

    # -- BofA Deals ----------------------------------------------------------------------------
    def open_deals(self):
        """Signed-in BofA → its Cash Back Deals redirect → deals.merchant-rewards.com with sections loaded.

        Clears known pop-ups first, follows only BofA's own redirect link, and installs DEALS_SHIM_JS for
        this navigation alone (removed in ``finally``). Returns {status, reached, tiles, sections_loading,
        reason}; deals_ready and deals_empty set offers_page_verified for the agent's classification.
        """
        result = lambda status, reached=False, tiles=0, loading=(), reason="": self._done("open_deals", {
            "status": status, "reached": reached, "tiles": tiles, "sections_loading": list(loading), "reason": reason})
        state = self._state()
        if self.issuer != "bofa":
            return result("refused", reason="only BofA has a Deals site")
        if not state.get("login_submissions") or state.get("login_result") != "signed_in":
            return result("refused", reason="no signed-in BofA login in this run")
        popup = self.clear_interstitial()
        if popup["status"] not in ("clear", "dismissed"):
            return result(popup["status"], reason=popup.get("reason", "pop-up"))
        target = state.get("tab_id")
        session = self._switch_tab(target)  # The CDP session the shim is added to and removed from.
        if self._current_tab()["target_id"] != target:
            return self._judgment("open_deals", "bank tab is not attached")
        href = None
        for candidate in self._js(DEALS_LINKS_JS) or []:
            try:
                href = cards_journal.bofa_deals_href(candidate)
                break
            except ValueError:
                continue
        if not href:
            return self._judgment("open_deals", "no Cash Back Deals link to BofA's offers redirect")
        script = None
        probe, verdict, diag = {}, "not_there", None
        try:
            # Without Page.enable on this session Chrome never runs added scripts: on 2026-11-13 the live
            # Deals page stayed Loading with the shim "installed", and the mock reproduced it.
            self._cdp("Page.enable", session_id=session)
            for domain in ("Network", "Runtime", "Log"):  # Events for the diagnostics only.
                try:
                    self._cdp(f"{domain}.enable", session_id=session)
                except Exception:
                    pass
            self._events()  # Drop whatever the buffer held before this navigation.
            script = self._cdp("Page.addScriptToEvaluateOnNewDocument", session_id=session,
                               source=DEALS_SHIM_JS)["identifier"]
            self._goto_url(href)
            deadline = self.clock() + DEALS_SECONDS
            while self.clock() < deadline:
                self.sleep(3)
                try:  # The redirect chain can destroy the context; that is just another probe.
                    probe = self._js(DEALS_PROBE_JS) or {}
                except Exception:
                    continue
                verdict = cards_journal.classify_deals(probe)
                if verdict in ("ready", "empty", "expired"):
                    break
            diag = self._deals_diagnostics(session, verdict)  # While the shim's counters still exist.
        finally:
            if script is not None:
                removed = True
                try:
                    self._cdp("Page.removeScriptToEvaluateOnNewDocument", session_id=session, identifier=script)
                except Exception:
                    removed = False  # Recorded; the shim only ever acts on the hidden Deals host.
                self.journal.issuer(self.issuer, deals_shim_removed=removed)
        tiles, loading = probe.get("tiles", 0), probe.get("loading", [])
        if diag:
            self.journal.issuer(self.issuer, deals_diag=diag)
        if verdict in ("ready", "empty") or (tiles and verdict == "loading"):
            self.journal.issuer(self.issuer, offers_page_verified=True,
                                deals={"tiles": tiles, "sections_loading": loading})
            status = "deals_empty" if verdict == "empty" else "deals_ready"
            reason = "some sections still loading" if loading else ""
            return result(status, reached=True, tiles=tiles, loading=loading, reason=reason)
        reason = {"expired": "Deals session expired", "not_there": "did not reach the Deals site",
                  "loading": "sections still loading", "unclear": "no offer tiles or empty state"}[verdict]
        self.journal.fail(self.issuer, "deals_not_loaded", status="partial",
                          deals={"tiles": tiles, "sections_loading": loading, "reason": reason})
        return result("deals_not_loaded", tiles=tiles, loading=loading, reason=reason)

    def _events(self):
        try:
            return (self._drain_events() if self._drain_events else None) or []
        except Exception:
            return []

    def _deals_diagnostics(self, session, verdict):
        """Write what the Deals page did to <journal>-deals-diag.json; return {file, summary} for the journal.

        Redacted: URLs lose query strings and fragments, long digit runs are masked, and no headers,
        cookies, request bodies or input values are read.
        """
        try:
            requests, console, contexts = {}, [], {}
            for event in self._events():
                if event.get("session_id") not in (None, session):
                    continue
                method, params = event.get("method", ""), event.get("params", {})
                rid = params.get("requestId")
                if method == "Network.requestWillBeSent":
                    request = params.get("request", {})
                    requests[rid] = {"url": _bare_url(request.get("url", "")), "method": request.get("method"),
                                     "type": params.get("type"), "start": params.get("timestamp")}
                elif method == "Network.responseReceived" and rid in requests:
                    entry = requests[rid]
                    entry["status"] = params.get("response", {}).get("status")
                    if entry.get("start") and params.get("timestamp"):
                        entry["ms"] = round((params["timestamp"] - entry["start"]) * 1000)
                elif method == "Network.loadingFailed" and rid in requests:
                    requests[rid].update(failed=params.get("errorText"), blocked=params.get("blockedReason"),
                                         cors=(params.get("corsErrorStatus") or {}).get("corsError"))
                elif method == "Runtime.consoleAPICalled" and params.get("type") in ("error", "warning", "assert"):
                    text = " ".join(str(a.get("value", a.get("description", ""))) for a in params.get("args", []))
                    console.append({"type": params["type"], "text": text[:200]})
                elif method == "Runtime.exceptionThrown":
                    details = params.get("exceptionDetails", {})
                    text = (details.get("exception") or {}).get("description") or details.get("text", "")
                    console.append({"type": "exception", "text": text[:200]})
                elif method == "Log.entryAdded" and params.get("entry", {}).get("level") in ("error", "warning"):
                    entry = params["entry"]
                    console.append({"type": "log-" + entry["level"],
                                    "text": (entry.get("text", "")[:160] + " " + _bare_url(entry.get("url") or "")).strip()})
                elif method == "Runtime.executionContextCreated":
                    context = params.get("context", {})
                    if (context.get("auxData") or {}).get("isDefault"):
                        contexts[context["auxData"].get("frameId")] = context.get("id")
            offer_requests = [{k: v for k, v in r.items() if k != "start"} for r in requests.values()
                              if r.get("type") in ("XHR", "Fetch") or re.search(r"merchant-rewards|tripleup|upside", r["url"])]
            frames = []

            def walk(node):
                frame = node.get("frame", {})
                frames.append({"id": frame.get("id"), "url": _bare_url(frame.get("url", "")),
                               "origin": frame.get("securityOrigin")})
                for child in node.get("childFrames", []):
                    walk(child)
            walk(self._cdp("Page.getFrameTree", session_id=session).get("frameTree", {}))
            for frame in frames[:6]:
                try:
                    if frame["id"] in contexts:
                        frame["state"] = self._cdp("Runtime.evaluate", session_id=session, expression=DEALS_FRAME_JS,
                                                   contextId=contexts[frame["id"]], returnByValue=True)["result"]["value"]
                    elif frame is frames[0]:
                        frame["state"] = self._js(DEALS_FRAME_JS)
                except Exception as exc:
                    frame["state"] = {"error": type(exc).__name__}
            shims = [f["state"]["shim"] for f in frames if isinstance(f.get("state"), dict) and f["state"].get("shim")]
            statuses = sorted({str(r.get("status") or r.get("failed") or "pending") for r in offer_requests})
            summary = (f"{verdict}: {len(frames)} frame(s); shim ran in {len(shims)}"
                       + (f" (observers {sum(s.get('observers', 0) for s in shims)}, fired {sum(s.get('fired', 0) for s in shims)})"
                          if shims else "")
                       + f"; {len(offer_requests)} XHR/offer request(s) [{', '.join(statuses) or 'none'}]"
                       + f"; {len(console)} console error(s)")
            report = json.loads(mask(json.dumps({"at": cards_journal.now(), "verdict": verdict, "summary": summary,
                                                 "frames": frames, "offer_requests": offer_requests[:60],
                                                 "console": console[:40]})))
            path = self.journal.path.with_name(self.journal.path.stem + "-deals-diag.json")
            cards_journal.atomic_json(path, report)
            return {"file": path.name, "summary": report["summary"]}
        except Exception as exc:  # Diagnostics never decide the outcome.
            return {"file": None, "summary": f"diagnostics failed: {type(exc).__name__}"}

    # -- sign-out ------------------------------------------------------------------------------
    def sign_out(self):
        state = self._state()
        if not state.get("login_submissions") and not state.get("offers_page_verified"):
            return self._done("sign_out", {"status": "not_signed_in"})
        if not self._attached():
            return self._judgment("sign_out", "bank tab is not attached")
        if self.cfg.get("accounts_url") and self._origin() not in self.cfg["tab_origins"]:
            self._goto_url(self.cfg["accounts_url"])  # e.g. back from BofA's Deals site
            self.sleep(5)
        if not self._js(SIGN_OUT_CLICK_JS % _js_args(self.cfg["sign_out"])):
            return self._judgment("sign_out", "no visible sign-out control")
        probe = {}

        def check():
            probe.update(self._js(SIGN_OUT_PROBE_JS))
            return signed_out(probe, self.issuer)

        ok = self._poll(check, seconds=SIGN_OUT_SECONDS)
        if not ok:
            self.journal.fail(self.issuer, "sign-out not verified", status="partial", sign_out="unverified")
            return self._done("sign_out", {"status": "unverified"})
        proof = ("signed-out confirmation text" if SIGNED_OUT_TEXT.search(probe.get("body", ""))
                 else "BofA signed-out page" if _bofa_signed_out_page(probe) else "login wall")
        # A blocked status (e.g. a challenge after login) is kept; otherwise the offers decide.
        status = state.get("status", "running")
        if status in ("running", "partial"):
            status = "completed" if state.get("offers_done") and not state.get("pending_offer") else "partial"
        self.journal.issuer(self.issuer, status=status, sign_out="verified", sign_out_proof=proof)
        if status == "partial" and state.get("status") != "partial":
            self.journal.fail(self.issuer, "offers not finished before sign-out")
        return self._done("sign_out", {"status": "verified", "issuer_status": status})


# -- roundup -----------------------------------------------------------------------------------
LIMIT = 3800


def roundup(journal_path, reason="run ended at roundup"):
    """Deterministic Telegram summary. Finalizes a still-running journal first."""
    journal = Journal(journal_path)
    data = journal.read()
    if data["status"] == "running":
        journal.close(reason)
        data = journal.read()
    return render(data)


# Follow-on failures that say nothing about why an issuer's offers were never reached.
CONSEQUENTIAL_FAILURES = ("offers not finished before sign-out", "sign-out not verified", cards_journal.INCOMPLETE)


def _blocker(data, name, issuer):
    """Short reason an issuer never reached its offers page, for the roundup's summary line."""
    if (issuer.get("interstitial") or {}).get("dismissed") is False:
        return "pop-up"
    reasons = [f.get("reason", "") for f in data["failures"]
               if isinstance(f, dict) and f.get("issuer") == name and not f.get("resolved")
               and f.get("reason") not in CONSEQUENTIAL_FAILURES]
    reason = reasons[0] if reasons else issuer.get("status", "unknown")
    reason = {"deals_not_loaded": "Deals page didn't load"}.get(reason, reason)
    return reason if len(reason) <= 80 else reason[:79] + "…"


def render(data, limit=LIMIT):
    status = data["status"]
    try:
        from zoneinfo import ZoneInfo
        day = datetime.fromisoformat(data["started_at"]).astimezone(ZoneInfo("America/New_York")).date()
    except Exception:
        day = data.get("started_at", "")[:10]
    head = (["[CRON_FAILURE]"] if status != "completed" else []) + [f"Card offers {day} ({status})"]
    notes, lines_by_issuer = [], {}
    total = 0
    for name in ("chase", "amex", "bofa"):
        issuer = data["issuers"].get(name)
        if issuer is None and name not in data.get("required_issuers", ("chase", "amex")):
            continue
        issuer = issuer or {}
        bank = BANKS[name]["name"]
        mine = [a for a in data["activations"] if a["issuer"] == name]
        total += len(mine)
        state = issuer.get("status", "missing")
        reached = mine or issuer.get("offers_page_verified") or issuer.get("offers_done")
        if issuer.get("login_submissions") and not reached and state != "completed":
            summary = f"{bank}: offers not reached ({_blocker(data, name, issuer)})"
        elif name == "chase" and issuer.get("cards"):
            summary = f"{bank}: {len(mine)} activated"
        else:
            card = f" {issuer['card_id']}" if issuer.get("card_id") else ""
            summary = f"{bank}{card}: {len(mine)} activated" + (" (nothing new)" if not mine and state == "completed" else "")
        if state != "completed":
            summary += f" [{state}]"
        summary_index = len(notes)
        notes.append(summary)
        if state == "blocked_mfa":
            mfa = issuer.get("mfa", {})
            notes.append(f"{bank} needs a one-time verification; see thread. "
                         f"({mfa.get('page', bank)}: {', '.join(mfa.get('options', [])) or 'no options shown'})"
                         + (" Tick 'remember this device' when completing it." if mfa.get("remember_device_option") else ""))
        elif state == "blocked_fields_hidden":
            notes.append(f"{bank} login fields stayed hidden; no login was submitted.")
        elif state == "skipped_budget":
            notes.append(f"{bank} was skipped: not enough turns left; no login was submitted.")
        popup = issuer.get("interstitial") or {}
        if popup.get("dismissed") is False:
            notes.append(f"{bank} blocked by a pop-up: {popup.get('title', '?')} "
                         f"(buttons: {', '.join(popup.get('buttons', [])) or 'none'}; {popup.get('reason', '')}). "
                         "Nothing on it was clicked.")
        deals = issuer.get("deals") or {}
        if deals.get("sections_loading"):
            notes.append(f"{bank} Deals sections still loading after {DEALS_SECONDS} s: "
                         f"{', '.join(deals['sections_loading'])}.")
        elif deals.get("reason"):
            notes.append(f"{bank} Deals page: {deals['reason']}.")
        if deals.get("reason") and (issuer.get("deals_diag") or {}).get("summary"):
            notes.append(f"{bank} Deals diagnostics: {issuer['deals_diag']['summary']}.")
        for seen in issuer.get("interstitials", []):
            notes.append(f"{bank}: dismissed pop-up \"{seen.get('title', '?')}\" with \"{seen.get('label', '?')}\".")
        if issuer.get("login_submissions") and issuer.get("sign_out") != "verified":
            notes.append(f"{bank} sign-out not verified.")
        uncertain = [k for k in issuer.get("attempted_offers", []) if k not in {a["key"] for a in mine}]
        if uncertain:
            notes.append(f"{bank}: {len(uncertain)} click(s) not verified; not retried.")
        if issuer.get("deferred"):
            notes.append(f"{bank}: {len(issuer['deferred'])} offer(s) left for shopping time (timed/link/slot).")
        if issuer.get("bonus_review"):
            review = issuer["bonus_review"]
            notes.append(f"{bank} bonus review {review.get('month', '')}: {review.get('status', '')}"
                         + (f" — {review['note']}" if review.get("note") else ""))
        for used in issuer.get("selector_overrides", []):
            notes.append(f"{bank}: selector override {used['key']}={used['value']} in {used['method']}; update BANKS.")
        groups = []
        if name == "chase" and issuer.get("cards"):
            seen_cards = set()
            for scope, info in issuer["cards"].items():
                card_id = info.get("card_id")
                if card_id and card_id in seen_cards:
                    continue
                if card_id:
                    seen_cards.add(card_id)
                matches = [a for a in mine if card_id and a.get("card_id") == card_id]
                card_status = "" if info.get("status") == "completed" else f" [{info.get('status', 'pending')}]"
                groups.append((f"{card_id or scope} — {len(matches)} activated{card_status}", matches))
            unmatched = [a for a in mine if a.get("card_id") not in seen_cards]
            if unmatched:
                groups.append(("Card not identified", unmatched))
        else:
            groups.append((None, mine))
        lines_by_issuer[name] = (summary_index, groups)
    # Statuses above already explain these; list only the other unresolved failures.
    explained = {"skipped_budget", "mfa", "login fields hidden", "sign-out not verified", "interstitial_blocked",
                 "deals_not_loaded", cards_journal.INCOMPLETE}
    for failure in data["failures"]:
        if isinstance(failure, dict) and not failure.get("resolved") and failure.get("reason") not in explained:
            notes.append(f"Issue: {failure.get('issuer', '?')} {failure.get('reason', '')}"
                         + (f" ({failure['detail']})" if failure.get("detail") else ""))
    tail = f"Total: {total} activated." if total else "Total: nothing new activated."
    # Reserve space for every status/note and the truthful total before offer details.
    base = "\n".join(mask(line) for line in head + notes + [tail])
    budget = limit - utf16_len(base) - 100
    inserts, shown = {}, 0
    for summary_index, groups in lines_by_issuer.values():
        detail = []
        for label, offers in groups:
            if label is not None:
                line = mask(f"  {label}")
                if utf16_len(line) + 1 > budget:
                    continue
                detail.append(line)
                budget -= utf16_len(line) + 1
            for offer in offers:
                line = mask(f"  • {offer.get('merchant', '?')} — {offer.get('deal', '?')}")[:120]
                if utf16_len(line) + 1 > budget:
                    break
                detail.append(line)
                budget -= utf16_len(line) + 1
                shown += 1
        inserts[summary_index] = detail
    body = list(head)
    for index, note in enumerate(notes):
        body.append(mask(note))
        body.extend(inserts.get(index, []))
    more = total - shown
    if more:
        body.append(f"and {more} more activated offers")
    body.append(tail)
    out = "\n".join(body)
    if utf16_len(out) >= limit:  # Notes alone overflowed: keep the head and total.
        out = "\n".join(head) + "\n" + mask("\n".join(notes))
        while utf16_len(out + "\n" + tail) >= limit:
            out = out[: len(out) - 200]
        out += "\n" + tail
    return out
