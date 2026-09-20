---
status: complete
---

# Phase 8: The money path

## Overview

`payment_intents` and `charges` end to end: the two tables, the PI state machine
(create / confirm / capture / cancel, the decline and 3DS branches), the charge
rows those transitions write, the four money-path events, and the idempotency
layer the headline eval needs (cross_cutting §3.1, landed opportunistically here
because the plan ties it to this phase and functional spec §12's scenario 4 —
"a customer charged, a declined card, an idempotent retry" — cannot replay
without it). Every behavior below was probed live at `2026-08-26.dahlia`
(2026-09-20, sandbox account) before this plan was written; the cassette for
functional spec §12 scenario 4 is recorded as `s04_money_path`.

## Probed behavior this phase implements (recordings win)

- Plain PI create: `status: requires_payment_method`, **`capture_method` defaults
  to `automatic_async`**, `amount_details: {"tip": {}}`, `client_secret` is
  `<pi_id>_secret_<22 alnum>`, `payment_method_types` defaults to `["card"]`
  (the recording account's `["card","link"]` is dashboard config — allow-listed).
- PI create with `payment_method` but no `confirm`: `requires_confirmation`.
- Confirm success (automatic): charge `succeeded`/`captured`/`paid`/`amount_captured`,
  PI `succeeded`/`amount_received`; events `charge.succeeded` then
  `payment_intent.succeeded`. Manual: charge `succeeded` but `captured: false`,
  `paid: true`, `amount_captured: 0`; PI `requires_capture`,
  `amount_capturable: amount`; events `charge.succeeded` then
  `payment_intent.amount_capturable_updated`.
- Capture: `amount_to_capture` ≤ amount (else recorded `amount_too_large`),
  partial capture sets `amount_received` to the captured amount; events
  `charge.captured` then `payment_intent.succeeded`. Wrong-status capture and
  cancel refuse with recorded `payment_intent_unexpected_state` messages naming
  the allowed status lists. Cancel stamps `canceled_at`, echoes
  `cancellation_reason`, stays `null` when omitted.
- Decline (`…0341`, attachable decline): **402 returned, rows kept** — a failed
  charge row (`failure_code`, `outcome.type: "issuer_declined"`,
  `network_status: "declined_by_network"`, `reason: <decline_code>`), PI back to
  `requires_payment_method` with `payment_method` cleared and `last_payment_error`
  set, events `charge.failed` then `payment_intent.payment_failed`. The error
  envelope carries `charge` (id) and the **full PI as `payment_intent` inside
  `error`**, and no `param`.
- Confirm with no resolvable PM: recorded `payment_intent_unexpected_state`
  400s, message differing for a PI with a customer (naming the customer id) and
  without, each carrying the PI sub-object. An unattached PM is **not** a
  refusal: a customer intent proceeds to the issuer outcome (cassette 04's
  decline flow and fresh-confirm steps), and a customerless intent charges
  with a fresh one — the ownership refusals (customerless + attached,
  wrong-customer) are the recorded 400s, added in the CR rounds.
- 3DS (`…3220`): on-session confirm → `requires_action` with a
  `use_stripe_sdk` next_action (network certificates — unreproducible; this
  world emits a deterministic stub, declared structural difference, unit-tested
  only). Off-session (`off_session: true`) → recorded 402
  `code`/`decline_code` `authentication_required`, message "Your card was
  declined. This transaction requires authentication."
- Amount 0 create: recorded `parameter_invalid_integer` refusal, param
  `amount`, verbatim message. (40/49 succeed — no minimum enforced on the
  recording account.)
- Update: `amount` only updatable from `requires_payment_method`,
  `requires_confirmation`, `requires_action` (recorded refusal, `param: amount`,
  PI sub-object); `description`/`metadata` updatable on `succeeded`.
- Charges: GET list filters `customer`, `created`, `payment_intent`
  (`transfer_group`, first listed here as a filter, is not one — corrected in
  the CR round; the update-attempt refusal is recorded in cassette 04). Charge
  update (`description`, `metadata`, `receipt_email`) works. Charge-level
  capture of a PI-created charge: recorded 400 "This
  uncaptured Charge was created by a PaymentIntent (pi_…). You must capture the
  PaymentIntent instead…"; of a captured charge: `charge_already_captured`.
  `POST /v1/charges` itself is legacy-dead, three recorded refusals:
  customer-with-PM 402 `missing` "…but does have a Payment Method attached…",
  customer-without-PM 402 `missing` "…doesn't have any saved payment details…",
  bare amount+currency 400 `parameter_missing` "Must provide source or
  customer."; `source: tok_visa` answers the end-of-life 400 verbatim.
- Missing path ids: PI paths name `param: "intent"` and say "No such
  payment_intent"; charge paths name `param: "id"` and say "No such charge".
- Idempotent replay of a create: same key+params → the stored response, byte
  for byte; same key+different params → `idempotency_error` whose message ends
  "Try using a key other than '<key>' if you meant to execute a different
  request." (probed; extends the constructor cross_cutting §2.1 quoted).
- Charge body facts: `refunds` **absent unexpanded** (expand-only at dahlia —
  data_model §7's derived-inline reading corrected; Phase 9 owns it);
  `radar_options: {}`, `calculated_statement_descriptor: "Stripe"`,
  `fraud_details: {}`; card rail carries `amount_authorized`,
  `checks.cvc_check: "pass"`, `electronic_commerce_indicator: "07"` (undeclared
  in spec — omitted here), `capture_before = created + 7 days` under manual
  capture, `network_token/extended_authorization/incremental_authorization/
  multicapture/overcapture` status objects (all deterministic);
  `authorization_code`/`network_transaction_id` are network randoms (null
  here, allow-listed); `balance_transaction` set only after a manual capture
  lands in the ledger — null until Phase 11 (allow-listed);
  `outcome.risk_score` is Radar's (omitted here, allow-listed).
- The real 3DS `tok_*` spellings are `tok_threeDSecure2Required` /
  `tok_threeDSecureRequired` (probed: Phase 6's `tok_card_*` spellings are
  invalid tokens) — magic_cards corrected.

## Steps

1. **`src/stripeapi/schema/002_payments.sql`** — `payment_intents` (28 cols) and
   `charges` (32 cols) + their five indexes, DDL verbatim from
   `components/data_model.md` §4.
2. **`tools_dev/prune_spec.py` + regenerate** — `DECLINE_CODES`, the 50-value
   authoritative table from gap-closure-2026-09-18.md item 7, rendered into
   `spec/enums.py` beside the enum tables (hand-transcribed constant, not
   spec-derived: `decline_code` has no machine-readable enum anywhere).
3. **`src/stripeapi/stripe_errors.py`** — `declined(*, code, decline_code,
   message, charge=None, sub_objects=None) -> dict`: the returned-never-raised
   402 body, `decline_code` validated against `DECLINE_CODES` (WorldBug on a
   miss, cross_cutting §3.4.4 reasoning). `idempotency_mismatch(key)` gains the
   probed key-hint suffix. `invalid_request` gains an optional `sub_objects`
   passthrough so the `payment_intent_unexpected_state` family can carry the
   full PI on `error` the way the recordings do.
4. **`src/stripeapi/billing/magic_cards.py`** — correct the 3DS token spellings
   (probed 2026-09-20) and add `4000000000003063`; the `pm_card_*` aliases stay
   (this world's deliberate acceptance).
5. **`src/stripeapi/resources/charges.py`** — FieldMap (columns + spec-declared
   nullable constants, `OMIT` for `transfer`/`radar_options`-shaped absent
   fields, derived `receipt_url`), ParamSpecs (list filters `customer`/
   `created`/`payment_intent`; update body `customer`,
   `description`, `fraud_details`, `receipt_email`, `shipping`),
   engine-served list/retrieve/update
   (`updated_event="charge.updated"`), hand-written `create` (the three legacy
   refusals + the end-of-life `source`/`card` refusal) and `capture` (the two
   recorded refusals; a legal uncaptured capture cannot exist without a PI in
   this world). The card-rail builder (`payment_method_details` from a PM row,
   the deterministic status objects, `capture_before`) lives here.
6. **`src/stripeapi/resources/payment_intents.py`** — FieldMap (constants:
   `amount_details {"tip": {}}`, the nullable always-present fields as `None`),
   ParamSpecs, engine-served list/retrieve/update (a `before_update` raising
   the recorded amount-refusal off the stored row), hand-written
   `create`/`confirm`/`capture`/`cancel` sharing one charge-attempt core:
   resolve PM (param → PI → customer default) with the recorded no-PM and
   unattached-PM refusals; read `payment_methods.x_behavior`
   (`charge_declined=`, `three_d_secure=`, dispute tag stored for Phase 9);
   write the charge row first, then move the PI, then emit the event pair.
   Declines **return** `ApiResponse(402, declined(...))`, never raise.
7. **`src/stripeapi/schema/004_infra.sql` + `middleware/idempotency.py` +
   `world.py`** — the `idempotency_keys` table (cross_cutting §3.1.3 verbatim),
   the middleware implementing §3.1.4's four outcomes with `request_hash`
   (§3.1.2), registered innermost, and `untracked_tables=("counters",
   "idempotency_keys")`.
8. **`src/stripeapi/dispatch/routes.py`** — wire the twelve money-path routes
   (7 PI: list/create/retrieve/update/cancel/capture/confirm; 5 charges:
   list/create/retrieve/update/capture) with `response_object`/`envelope`.
   `amount_details_line_items`, `apply_customer_balance`,
   `increment_authorization`, `verify_microdeposits` and the charge
   dispute/refund sub-routes stay unwired for their later phases.
9. **`tests/conformance/redact.py`** — normalize `receipt_url` (embeds the
   account id base64) like `request_log_url`: placeholder, no finding.
10. **`tests/conformance/allowed_differences.py`** — the money-path block:
    `**.client_secret` (shape-predicated), `**.latest_charge`/`**.payment_intent`
    reference pairs, `**.balance_transaction` (ledger lands Phase 11),
    `**.outcome.risk_score`, the network-random card fields, the
    dashboard-config artifacts (`automatic_payment_methods`,
    `payment_method_configuration_details`, default `payment_method_types`,
    extra `payment_method_options` rails), the live-only undeclared fields
    (`source`, `payment_record`, `shared_payment_granted_token`,
    `electronic_commerce_indicator`, `destination`, `dispute`, `order`,
    `radar_options`), `**.receipt_url`, `**.capture_before` (clock),
    decline-envelope `advice_code`/`network_decline_code` (scenario-scoped),
    id-shaped message predicates for the two charge-capture refusals and the
    mismatch message's key echo, and the `error.payment_intent.*` coverage the
    `**.` entries give.
11. **Record `s04_money_path.py`** — customer + attached PM + default-PM set;
    plain create; card-only confirmed create under an idempotency key (+
    same-key retry, + same-key-different-params `idempotency_error`); expanded
    latest_charge + charge GET; decline flow (402 → PI GET → failed charge
    GET); manual capture (uncaptured charge GET, partial capture, re-capture
    refusal, charge-after-capture GET); cancel flow (echoed reason, re-cancel
    refusal, cancel-succeeded refusal, capture-canceled refusal); the two
    no-PM confirm refusals; 3DS off-session decline; updates (metadata/
    description on succeeded, amount on succeeded refused, amount on plain
    ok); the three lists; the four legacy-charge refusals; zero-amount
    refusal; the three missing-id 404s.
12. **Tests** — `tests/test_payment_intents.py`, `tests/test_charges.py`,
    `tests/test_idempotency.py`; additions to `test_stripe_errors.py`
    (`declined` envelope shape, code validation, never-raised lint),
    `test_payment_methods.py` (corrected 3DS token spellings). Engine and
    replay tests need no new code: the autouse schema-conformance fixture
    covers every body this slice returns, and cassette 04 joins the
    parametrized replay gate.

## Tests

- `test_payment_intents.py` — plain-create defaults (status, capture_method
  `automatic_async`, client_secret shape, payment_method_types `["card"]`,
  amount_details); create-with-PM → `requires_confirmation`; confirm success
  (automatic and manual, every amount/charge column, event pair and order);
  decline (402 envelope with `error.payment_intent` + `error.charge`, rows
  kept: failed charge, PI `requires_payment_method` with cleared PM and
  `last_payment_error`, event pair); 3DS on-session (`requires_action`, stub
  next_action) and off-session (`authentication_required`); capture (full,
  partial, overcapture refusal, wrong-status refusals with the recorded
  messages); cancel (echoed reason, null when omitted, refusal messages);
  the no-PM and unattached-PM confirm refusals; zero-amount refusal; update
  guards; `x_seq`-ordered lists; latest_charge expansion.
- `test_charges.py` — the charge body's every constant (fraud_details,
  calculated_statement_descriptor, radar_options absent, refunds absent,
  receipt_url derived); card rail shape incl. `capture_before` and the status
  objects; decline outcome shape; the four legacy-create refusals verbatim;
  both charge-capture refusals; update; list filters incl. `payment_intent`;
  events (`charge.succeeded`/`failed`/`captured`/`updated`).
- `test_idempotency.py` — cross_cutting §3.1.8's two doors: the replay writes
  zero change-log records and returns the stored body byte-for-byte; the key
  row records method/path/hash; mismatch (with the probed message); DELETE and
  GET pass through; a keyed POST that raises pre-execution retracts the
  reservation; `in_flight` seeded via `inst.bulk()` answers 409; a returned
  402 decline is cached under the key (§3.5.5).
- `tests/conformance/test_replay_conformance.py` — cassette 04 replays green
  via the merge gate (parametrized, no new test code).
