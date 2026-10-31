# Amazon Visa and Alaska/Bank of America

Added at the user's request on 2026-10-30. The user confirmed the Bank of
America Login is already in **AI agents**. Discover its native vault handle;
this confirmation is not evidence that origin binding or authentication works.
Use the same browser, native vault fill, login limit, click reservations,
verification, and sign-out rules as the main skill. These new UI paths still
need their first live verification; inspect current controls rather than
inventing selectors. Preserve the existing Chase/Amex coverage.

## Chase: explicitly cover Amazon/Prime Visa

Amazon Visa and Prime Visa are Chase cards. Use the existing Chase session;
do not sign into Amazon or treat the Synchrony Amazon Store Card as this card.

1. On the first extended run, capture the initially selected Chase card's
   masked label and last four as the existing-card baseline. On later runs,
   recover that baseline from the preceding journal's `cards.existing.card_id`
   and select it explicitly: Chase may remember Amazon as the last selection.
   Do not silently replace the baseline if that card disappears or is ambiguous.
   Before activation, set `required_cards=["existing", "amazon"]` and
   `cards={"existing": {"status": "pending"}, "amazon": {"status": "pending"}}`
   with `journal.issuer("chase", ...)`. Keep this coverage map throughout the run.
2. Finish that card's offers, then use the observed Choose account control to
   select the card labeled Amazon Visa / Prime Visa. Confirm the displayed card
   and last four before any offer click and after every return to the hub.
   If both scopes identify the same card, process once and mark both covered.
   If absent or ambiguous, report Amazon coverage blocked, not zero offers.
3. Use `chase::<masked-card-label-last4>::<offer_id>` for reservations, visited
   keys, and activations. Identical merchant/offer IDs on different cards are
   independent. Preserve old `chase::<offer_id>` records without using them to
   suppress visible offers on either card.
4. Check all available sections, including Other when present. Record each
   card's observed `card_id` (masked label plus last four) and status in the
   coverage map, including the existing baseline before switching. Mark a scope completed
   only after a verified empty/exhausted offers view. Persist the full map via
   `journal.issuer("chase", cards=coverage)` as each card finishes. A successful
   login or the other card's successful activation does not prove coverage.
5. Sign out once after both cards and any due bonus review. Preserve per-card
   counts and card labels in the roundup so the user knows which card to use.

## BofA: Alaska / Atmos Rewards

Start at `https://www.bankofamerica.com/`, use official sign-in, then navigate
to Rewards & Deals / cash back deals / BankAmeriDeals. Alaska branding may now
appear as Atmos Rewards: verify the actual card and masked last four. Check
eligible cards and each offer's exclusions; travel-related merchants may
exclude travel co-branded cards, including Atmos. Do not promise eligibility
just because a deal appears in the general account feed.

Follow a Deals partner redirect only when reached through authenticated BofA
navigation. Never inject bank credentials into a partner site. If a separate
enrollment, account link, location permission, or unrelated consent is required,
report the action needed. Do not change preferences to expose extra offers.

Live observation, 2026-10-30: the existing user-verified BofA session exposed
an Atmos Rewards Ascent Visa Signature account. Accounts overview → Rewards &
Deals → Cash Back Deals reached `deals.merchant-rewards.com` through the bank's
authenticated navigation. The Deals shell loaded but offer sections remained
Loading after 40 seconds; no BofA activation was tested. A loaded shell or a
recognized card does not establish per-offer eligibility or zero available
offers. Wait for actual cards/terms or an explicit empty result; persistent
loading is partial coverage, not success. Do not navigate to this partner
directly as a substitute for the authenticated bank handoff.

Classify each offer from its current terms **before** activation:

| Observed type | Scheduled behavior |
| --- | --- |
| Ordinary card-linked; activation required; no activation-relative timer or shared slot limit | Activate once, verify explicit Activated/Ready to use state, and journal it. |
| Auto Activated / already active | No click; separate from newly activated totals. |
| Daily Essentials, short activation-relative timer, or limited activation slots | Defer until the user is about to shop; never bulk activate overnight or deactivate existing deals. |
| Shop Now / Must Use Link / merchant-fulfilled / coupon | Report useful newly observed requirements; visiting a shopping link is not an activation. Do not shop or claim the reward is secured. |
| Unclear terms or card eligibility | Do not activate; record what requires clarification. |

Current public terms describe Daily Essentials as four hours after activation,
up to five active at once, location dependent, and absent from Search and My
Activated Deals. Current offer-specific terms take precedence. Ordinary offers
may have no displayed expiration; absence of a date is not proof of a timed deal.

Capture merchant, deal, stable offer ID, eligible masked card/scope, expiration,
minimum spend and reward cap when displayed. Use
`bofa::<observed_scope>::<offer_id>`; the scope is the Alaska/Atmos masked card
or a verified shared account scope, never an invented account number. If no
offer ID exists, use a deterministic composite of observed merchant, deal,
expiration and scope. Do not assume account-wide activation or duplicate it
across cards. Record verified results through `journal.record("bofa", key, ...)`.
Never count disappearing buttons alone as proof. Stop on an uncertain click.

Record policy-deferred offers under `journal.issuer("bofa", deferred=[...])`,
with only nonsecret offer metadata and reasons. Correctly classified timed/link
offers do not make an otherwise complete review fail. An unreadable offer or
uncertain eligibility means incomplete review; report it as partial. Missing
login, MFA, uncertain activation, unfinished scanning or unverified sign-out
also remain partial. Continue the other issuers independently.

## Monthly targeted spending bonuses

On the first successful check each calendar month in America/New_York, review
the Amazon Visa and BofA card's authenticated offers/rewards area for targeted
spending/category bonuses. Run on a later day if the month began with a blocked
login. Ordinary daily offer checks still activate qualifying bonuses encountered
between monthly reviews.

For Chase, `https://www.chase.com/mybonus` is the official Spend and Get entry
point. Inspect its current flow only after the core card offers are complete.
Treat any submission as potentially activating: reserve it first. If it needs
card details or personal information unavailable through the current supported
native vault workflow, report the official link for a manual check. Never ask
the user to paste a full card number, retrieve it with shell commands, or guess.
If activation cannot reveal the offer terms beforehand, leave it for manual
review. Do not claim a specific bonus from online examples or another card.

Auto-enroll only a free, clearly identified rewards promotion for the in-scope
card with readable terms and no purchase, account change, fee, points redemption
or financing agreement. Apply the same one-click reservation and visible success
verification. Record the observed bonus, eligible spending/categories, threshold,
cap, exclusions, enrollment deadline and earning period. Distinguish enrollment
from earning the bonus; never recommend extra spending solely to chase it.

Persist `bonus_review={"month": "YYYY-MM", "status": "checked" | "action_required",
"card_id": "masked-label", "note": "nonsecret result"}` in the issuer journal.
Before reviewing, inspect prior run journals for that issuer/card/month. A
checked result suppresses redundant monthly reviews. For action_required, carry
the unresolved reminder into the current journal without repeated submissions;
retry when the user supplies the missing setup or requests a check. Failures to
load the review are partial, not a checked result. A manual-only bonus check can
be reported separately without failing otherwise complete daily activations.
No separate cron job or inbox access is needed.

## Official references (checked 2026-10-30)

- [BofA deals and current deal types](https://promotions.bankofamerica.com/en/deals)
- [Chase Offers: card-specific activation and account switching](https://www.chase.com/personal/merchant-offers/faqs)
- [Chase Spend and Get](https://www.chase.com/mybonus)
- [Amazon Visa issuer and standard rewards](https://creditcards.chase.com/cash-back-credit-cards/amazon-rewards)

Amazon's standard purchase rewards need no periodic offer activation. Do not
confuse ordinary rewards, portal shopping, welcome bonuses, or financing offers
with card-linked offers that can be safely added in this workflow.
