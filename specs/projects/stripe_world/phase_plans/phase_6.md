---
status: complete
---

# Phase 6: Customers and payment methods

## Overview

The first real resource slice, establishing the pattern every later slice copies. Replaces the
Phase-3 throwaway customers slice with the full one, adds `payment_methods` end to end —
table, generated CRUD, hand-written `attach`/`detach`, magic-card interpretation at creation —
and settles the carried delete-event ordering/snapshot question by recording it against real
test mode. Probing was done live at `2026-08-26.dahlia` before this plan was written; every
pinned behavior below cites what the probes returned.

## Probed behavior this phase implements (recordings win)

- `customer.deleted`'s `data.object` is the **full pre-delete customer object, with no
  `deleted` key** — not the three-key stub. `payment_method.detached` carries
  `previous_attributes: {"customer": …}`; `payment_method.attached` carries none.
- Delete-event emission moves **after** the write (create/update already do; the recording
  shows the snapshot equals the normal serializer's view either way, so ordering is decided by
  the cross-cutting rule, not by observation).
- Top-level `GET /v1/payment_methods` returns nothing without `customer`; with it, only that
  customer's attached methods. A nonexistent `customer` on that query is a **400**
  `resource_missing` (query-side), while a bad path id is a 404.
- `attach` twice is 200-idempotent; `attach` without `customer` answers 400
  `parameter_missing` **"Must provide customer or customer_account."** (no `param`); attaching
  a directly-declining card (`tok_visa_chargeDeclined`) answers **402** `card_error`
  `generic_decline`, while `4000…0341` attaches and only declines when charged.
- `detach` of an unattached method is 400, message verbatim, no `code`.
- Card PMs via `card[token]` (`tok_visa` family) return the full `card` block
  (`checks.cvc_check: "unchecked"`, `networks.available`, `three_d_secure_usage`, `wallet`).
  Raw PANs are refused on the recording account ("Sending credit card numbers directly…"),
  so cassettes use tokens; the world itself accepts raw numbers per the magic-card table.
- A stubbed rail (`type=klarna`) creates fine and returns `"klarna": {}` — the rail key under
  the type's own name, nothing more.
- `us_bank_account` creation returns the full rail including
  `networks: {preferred: "ach", supported: ["ach"]}`, `bank_name: "STRIPE TEST BANK"`,
  `status_details: {}`.
- Customer create with `balance != 0` stamps `currency` to the account default (sandbox:
  `"cad"`); `balance = 0` leaves it null; `currency` is not a settable parameter.
- `shipping: {}` on update is a no-op; nested objects merge per leaf, and a provided
  `address`/`shipping`/`billing_details` serialises with every key present (missing leaves
  null).
- The live body emits `shared_payment_granted_token` on `payment_method`, which the pinned
  spec does not declare — allow-listed, not emitted.

## Steps

1. **`src/stripeapi/schema/002_payments.sql`** — new file, the `payment_methods` DDL verbatim
   from `components/data_model.md` §4 (table, three indexes). Header comment per file
   convention. The `counters` seed already names `payment_methods`.
2. **`src/stripeapi/billing/magic_cards.py`** — the failure-injection table as data:
   number / `tok_*` / `pm_card_*` token → brand, funding, last4, display_brand, country, and
   behavior tags (`attach_declines`, `charge_declines(code, decline_code)`, dispute/refund/
   payout tags recorded now, consumed by later phases). Deterministic `fingerprint(number)`
   (content digest, not `ctx` — same number, same fingerprint, like the real API).
   `billing/__init__.py` created; imported from the package where needed.
3. **`src/stripeapi/resources/payment_methods.py`** — FieldMap (id, created, type,
   allow_redisplay, billing_details, customer, metadata; constants livemode/customer_account/
   radar_options) plus a serializer wrapper that adds the rail column under the row's `type`
   key (modeled rails in full, stub rails as `{}`). ParamSpecs for create/update/retrieve/
   attach/detach. `before_create` interprets `card` (number or token) and `us_bank_account`
   into the rail JSON + `x_behavior`, canonicalises `billing_details` (all keys), defaults
   `allow_redisplay: "unspecified"`. Hand-written `attach`/`detach` with the probed errors and
   events (`payment_method.attached` / `.detached` with `previous_attributes`;
   `updated_event="payment_method.updated"`, **no** `created_event`). `ListFilter`s:
   `type` (literal, the 57), `allow_redisplay` (literal), `customer` (exact, plus the new
   `empty_without` flag).
4. **`src/stripeapi/dispatch/resource.py`** — two narrow engine changes: (a) `delete` emits
   its event **after** the tombstone/remove write, with the comment that settles the carried
   item; (b) `ListFilter.empty_without: bool = False` — when such a filter is absent the page
   is empty (the payment_methods top-level list), cursors still resolving first.
5. **`src/stripeapi/resources/customers.py`** — the real slice: full create/update body
   (`address`, `balance`, `description`, `email`, `invoice_prefix`, `invoice_settings`,
   `metadata`, `name`, `next_invoice_sequence`, `phone`, `preferred_locales`, `shipping`,
   `tax_exempt`), canonicalised nested objects, leaf-wise merge on update,
   `balance != 0 → currency = ACCOUNT_DEFAULT_CURRENCY` ("usd", one constant, comment on the
   sandbox's "cad").
6. **`src/stripeapi/dispatch/params.py`** — a body `Param(kind="id")` that does not resolve
   raises `resource_missing` at **400** (path ids stay 404; both probed).
7. **`src/stripeapi/dispatch/routes.py`** — wire the six payment_methods routes (list, create,
   retrieve, update engine-served; attach/detach hand-written) and the two customer-scoped
   reads (`Scope` on customer), with `response_object`/`envelope`; payment_methods pins
   `missing_path_param=None` (probed: the placeholder is named).
8. **`tests/schema_conformance/validate.py`** — declared exception: on `payment_method`, an
   undeclared key that is a member of the `type` enum and equals `{}` is a rail stub
   (pruned spec drops those props by design; data_model §3.9).
9. **`tests/conformance/allowed_differences.py`** — new entries: `**.fingerprint`,
   `**.shared_payment_granted_token`, scenario-06-scoped decline-envelope omissions
   (`param: ""`, `advice_code`, `network_decline_code`); structural notes for raw-PAN
   acceptance, the stubbed rails, and the account-default currency.
10. **Regenerate spec artifacts** — `python -m tools_dev.prune_spec` (full spec is on disk);
    the drift tests stay green.
11. **Record** — `tools_dev/scenarios/s06_customers_payment_methods.py` (the slice cassette:
    customer create/update/delete, PM create via tokens, attach, idempotent re-attach,
    decline attach 402, lists, scoped reads, update, detach, detach-again 400, stub retrieve)
    and `probe_customer_payment_method_events.py` (the `/v1/events` shapes that pin the
    carried item — probe-only because `/v1/events` is unrouted until Phase 17). Record both;
    commit cassettes.
12. **Tests** — new `tests/test_customers.py`, `tests/test_payment_methods.py`; engine and
    validator tests for the two narrow changes; carried-item test on the events table.

## Tests

- `test_customers.py` — full-body create round-trips every nested object (canonical full-key
  shapes), leaf-merge update (`shipping: {}` no-op, single-leaf set), `balance != 0` stamps
  currency, `next_invoice_sequence`/`invoice_prefix` honored, deleted stub + list exclusion
  (already engine-tested; keep the customer-specific defaults).
- `test_payment_methods.py` — token create (full card block incl. checks/networks/3ds/wallet),
  raw-number create (4242 + each magic family incl. Luhn-fail `…4241` →
  `incorrect_number`), `us_bank_account` create, stubbed-rail create (`klarna: {}`),
  billing_details defaults, update (allow_redisplay/billing_details/card expiry/metadata),
  attach/detach semantics and every probed error, scoped list/retrieve (incl. the
  other-customer 404), top-level list `empty_without`, events attached/detached/updated and
  the absence of `payment_method.created`, `x_behavior` tagging via `inst.inspect()`.
- `test_resource_engine.py` (additions) — `empty_without` filter behavior; delete-event
  ordering/snapshot: emitted after the write, snapshot is the full pre-delete object with no
  `deleted` key, previous_attributes absent (the carried item).
- `tests/schema_conformance` — a stubbed-rail PM body passes with the declared exception; a
  bogus extra key still fails.
- `tests/conformance/test_replay_conformance.py` — cassette 06 replays green via the merge
  gate (parametrized, no new test code).
