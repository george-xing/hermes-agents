# BofA Alaska/Atmos offers and monthly bonus review

Added at the user's request on 2026-10-30. Chase card selection (existing card
plus Amazon/Prime Visa) is handled by `Bank("chase").activate()`; this file
covers the two parts that stay agentic.

## BofA: Alaska / Atmos Rewards

After `submit_login` returns `signed_in`, use BofA's authenticated navigation:
Accounts overview → Rewards & Deals → Cash Back Deals (BankAmeriDeals). On
2026-10-30 that reached `deals.merchant-rewards.com`, but offer sections stayed
"Loading" after 40 seconds and no activation was tested. Do not open the
partner site directly, never inject bank credentials into it, and treat a
loaded shell or a recognized card as neither eligibility nor zero offers.
Alaska branding may appear as Atmos Rewards: verify the card and masked last
four. Travel merchants may exclude travel co-branded cards. If enrollment,
account linking, location permission or unrelated consent is required, report
the action needed; do not change preferences to expose more offers.

Classify each offer from its current terms **before** any click:

| Observed type | Scheduled behavior |
| --- | --- |
| Ordinary card-linked; activation required; no activation-relative timer or shared slot limit | Activate once with `activate_one`, verify an explicit Activated / Ready to use state. |
| Auto Activated / already active | No click; reported separately from new activations. |
| Daily Essentials, short activation-relative timer, or limited activation slots | Defer until the user is shopping; never bulk activate overnight or deactivate existing deals. |
| Shop Now / Must Use Link / merchant-fulfilled / coupon | Report useful new requirements; visiting a link is not an activation. Do not shop. |
| Unclear terms or card eligibility | Do not activate; record what needs clarification. |

Public terms describe Daily Essentials as four hours after activation, up to
five active at once, location dependent and absent from Search and My
Activated Deals. Offer-specific terms take precedence. No displayed expiration
is not proof of a timed deal.

To activate an ordinary offer, after inspecting its control and success state:

```python
bank = Bank("bofa", JOURNAL, harness=globals(), turn=TURN)
print(bank.activate_one("bofa::<masked card>::<offer id>", "<observed button selector>",
                        "<read-only JS that is true only once this offer shows Activated>",
                        merchant=..., deal=..., offer_id=..., card_id=..., expires_at=...,
                        minimum_spend=..., reward_cap=..., offer_type="card_linked"))
```

It reserves the key, clicks once, waits 3–6 s and polls 15 s. Stop on any
status other than `verified`. Without a stable offer id, build the key from
observed merchant, deal, expiration and scope. Never invent an account number.

Record the rest with the journal (`from cards_journal import Journal`):
`journal.issuer("bofa", deferred=[{merchant, deal, reason}, ...])` for
policy-deferred offers, and `journal.issuer("bofa", offers_done=True)` only
after every visible offer was classified and nothing was unreadable. Then call
`sign_out()`; without `offers_done` the issuer ends `partial`. Correctly
deferred offers do not make an otherwise complete review fail; an unreadable
offer, uncertain eligibility, endless Loading or an uncertain click does.

## Monthly targeted spending bonuses

On the first successful check each calendar month (America/New_York), before
signing out, review the Amazon Visa and BofA card's authenticated offers or
rewards area for targeted spending/category bonuses. If the month began with a
blocked login, do it on a later day. Before reviewing, read earlier journals
for that issuer/card/month: `checked` suppresses another review;
`action_required` is carried into this journal without a new submission.

For Chase, `https://www.chase.com/mybonus` is the official Spend and Get entry
point, only after the core card offers are complete. Treat any submission as
potentially activating: reserve it first (`journal.reserve_offer`). If it needs
card details or personal information the native vault workflow cannot supply,
report the link for a manual check. Never ask for or retrieve a full card
number, and never claim a bonus from examples or another card.

Auto-enroll only a free, clearly identified rewards promotion for the in-scope
card with readable terms and no purchase, account change, fee, redemption or
financing agreement. Record the bonus, eligible spending, threshold, cap,
exclusions, enrollment deadline and earning period. Enrollment is not earning;
never recommend extra spending to chase it.

Persist `journal.issuer(issuer, bonus_review={"month": "YYYY-MM", "status":
"checked" | "action_required", "card_id": "masked label", "note": "..."})`.
A review that fails to load is partial, not checked. A manual-only check does
not fail otherwise complete daily activations; the roundup prints the note.

Amazon's standard purchase rewards need no activation. Do not confuse ordinary
rewards, portal shopping, welcome bonuses or financing offers with card-linked
offers.

## Official references (checked 2026-10-30)

- [BofA deals and current deal types](https://promotions.bankofamerica.com/en/deals)
- [Chase Offers: card-specific activation and account switching](https://www.chase.com/personal/merchant-offers/faqs)
- [Chase Spend and Get](https://www.chase.com/mybonus)
