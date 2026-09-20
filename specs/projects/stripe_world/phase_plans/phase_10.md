---
status: complete
---

# Phase 10: Setup intents

## Overview

`setup_intents` end to end: the table, the seven routes, the confirm
transition with its magic-card outcomes (success with auto-attach, 3DS park,
returned-402 decline whose rows survive), cancel, the recorded wrong-state
and ownership refusals, `verify_microdeposits`' recorded refusal, the
engine-served update with its `payment_method` guards, and the event family
(`setup_intent.created` / `requires_action` / `setup_failed` / `succeeded` /
`canceled`). Every behavior below was probed live at `2026-08-26.dahlia`
(2026-09-20, sandbox account) before this plan was written; the cassette is
recorded as `10_setup_intents`.

## Probed behavior this phase implements (recordings win)

- **Create**: bare → `requires_payment_method`, `client_secret`
  `seti_…_secret_…`, `payment_method_options` stamped with the card default
  `{mandate_options: null, network: null, request_three_d_secure:
  "automatic"}` (no `installments` key, unlike the PI default); with
  `payment_method` → `requires_confirmation`. **The `usage` parameter is
  dead at the pinned version**: `on_session` and even `bogus` are accepted
  (200) and the body answers `off_session` every time, create and update
  alike — accepted, ignored, never stored as anything but the default.
- **Ownership refusals** (recorded spellings, all id-bearing): create with
  `customer` A + a PM attached to B → 400, no code, `param:
  "payment_method"`, `The PaymentMethod pm_… does not belong to the Customer
  you supplied cus_…. Please use this PaymentMethod with the Customer that it
  belongs to instead.`, **no** intent carried; update of `payment_method` on
  a customerless intent with a customer's PM → 400, no code, same param,
  `The payment method supplied (pm_…) belongs to the Customer cus_…. Please
  include the Customer in the \`customer\` parameter on the SetupIntent.`,
  the full intent carried. Confirm-time spellings are recorded by the
  cassette steps themselves.
- **Confirm success**: `succeeded`, `latest_attempt` mints a `setatt_…`
  stub (never resolved), `mandate` / `single_use_mandate` stay **null** on
  card setups (recorded), `next_action` null; when the intent carries a
  `customer` and the PM is unattached, the PM is auto-attached (rail
  cvc_check flips to `pass`, `payment_method.attached` fires) — the
  documented attach-on-setup. **No customer-default fallback**: a
  `customer` whose `invoice_settings.default_payment_method` is set still
  refuses confirm with the missing-method message — the resolution chain is
  parameter → the intent's own column, full stop (recorded; unlike the PI
  chain).
- **Confirm 3DS** (`tok_threeDSecure2Required`): **200**, parks at
  `requires_action`, `latest_attempt` minted, `next_action` the
  `use_stripe_sdk` shape — the recorded body carries issuer certificates no
  replica can reproduce, so this world emits the deterministic stub (the
  Phase 8 declared difference, same ruling; the cassette avoids the step and
  the stub is unit-tested).
- **Confirm decline**: a **returned 402** whose rows survive — the intent
  resets to `requires_payment_method`, `payment_method` NULLed,
  `last_setup_error` set (the `api_errors` shape: `code`, `decline_code`,
  `doc_url`, `message`, the full `payment_method`, `type` — no
  `payment_method_type`, unlike the PI's `last_payment_error`), and
  `latest_attempt` minted. The envelope carries `error.payment_method` and
  the full `error.setup_intent`. Setup-intent decline extras (recorded):
  `decline_code` falls back to the code when the tag carries none
  (`expired_card` declines answer `decline_code: "expired_card"`, not null),
  and the expired-card envelope additionally carries `param: "exp_month"`
  — the one recorded decline `param` on this surface.
- **Wrong-state refusals** (all recorded verbatim, all carrying the full
  intent on the error object): confirm with no resolvable PM (any status) →
  `setup_intent_unexpected_state`, `You cannot confirm this SetupIntent
  because it's missing a payment method. You can either update the
  SetupIntent with a payment method and then confirm it again, or confirm it
  again directly with a payment method or ConfirmationToken.`; confirm on
  succeeded → `…because it has already succeeded.`; cancel on succeeded →
  the status-list spelling with backticks — `You cannot cancel this
  SetupIntent because it has a status of succeeded. Only a SetupIntent with
  one of the following statuses may be canceled: \`requires_payment_method\`,
  \`requires_confirmation\`, or \`requires_action\`.`; cancel on canceled →
  `…because it is already canceled.`; `payment_method` update on succeeded →
  `You cannot update this SetupIntent because it has already succeeded.`
  (description / metadata / customer updates on a succeeded intent are
  **200**, recorded). The missing-method check runs **before** the status
  guard (recorded: a canceled intent without a PM answers the
  missing-method message).
- **Cancel**: `canceled` + the caller's `cancellation_reason` (null when
  omitted), `client_secret` kept, event `setup_intent.canceled`.
- **`verify_microdeposits`**: refused on every intent this world can mint —
  400 `intent_invalid_state`, `This SetupIntent cannot be actioned on
  because it has a status of <status>. Only a SetupIntent with one of the
  following statuses may be actioned on: ["requires_action"].` (the JSON
  array inside the message is Stripe's own quirk, kept verbatim), full
  intent carried. Microdeposit rails are out of scope; the refusal is the
  whole surface.
- **Reads**: missing id → 404 `resource_missing` `No such setupintent:
  'seti_…'` (one word, unlike `payment_intent`), `param` stays `intent`;
  list filters `customer` / `payment_method` / `created`; `attach_to_self`
  is a stored column the wire never carries (absent on every recorded body)
  and is not serialized.
- **Events**: `setup_intent.created` (snapshot the created state, before any
  confirm — the recorded create+confirm call's `created` snapshot carries
  the post-failure `last_setup_error`, an async-emission artifact this world
  does not reproduce; the Phase 8 PI precedent, declared), then
  `setup_intent.requires_action` / `setup_failed` / `succeeded` per
  transition. Auto-attach emits `payment_method.attached` before the
  terminal event. No `setup_intent.updated` exists in the closed set —
  updates emit nothing.

## Steps

1. **`src/stripeapi/schema/002_payments.sql`** — the `setup_intents` table
   (21 cols) and its three indexes, DDL verbatim from
   `components/data_model.md` §4; rebuild `fixtures/empty`
   (`python fixtures_src/generate.py` after removing the committed dir).
2. **`src/stripeapi/stripe_errors.py`** — `declined()` grows an optional
   `param` (the recorded `exp_month` on the expired-card decline; every
   existing caller passes nothing and is unchanged).
3. **`src/stripeapi/resources/setup_intents.py`** — FieldMap (no
   `attach_to_self`; constants for the Connect/config nulls incl.
   `managed_payments: None` per the PI ruling), ParamSpecs (create:
   `attach_to_self`, `automatic_payment_methods`, `confirm`, `customer`,
   `description`, `payment_method`, `payment_method_options` (card:
   `mandate_options`/`network`/`request_three_d_secure` with the setup
   enums), `payment_method_types`, `usage` accepted-and-ignored; update:
   the mutable set incl. `payment_method`; confirm: `payment_method`,
   `payment_method_options`; cancel: `cancellation_reason`;
   verify_microdeposits: `amounts`/`descriptor_code`), the confirm core
   (resolution → ownership → missing-method → status guard → 3DS → decline
   → success+auto-attach), hand-written `create` / `confirm` / `cancel` /
   `verify_microdeposits`, engine-served update behind a `before_update`
   that owns the reference lookups and the recorded refusals, and
   `ResourceSpec` registered with `error_name="setupintent"`.
4. **`src/stripeapi/dispatch/routes.py`** — wire the seven routes (2
   generated reads + 5 hand-written).
5. **Record `10_setup_intents`** — the flow above step-for-step: the bare
   and `usage`-pinned creates, ownership refusals at create/update/confirm,
   confirm success (attached and auto-attach paths), the missing-method and
   customer-default refusals, the decline 402s (generic + expired), cancel
   with reason and every wrong-state refusal, the `verify_microdeposits`
   refusal, the lists, the missing id. 3DS stays out (issuer certificates;
   unit-tested stub).
6. **`tests/conformance/allowed_differences.py`** — extend
   `**.client_secret` to the `seti_` shape; new entries: `**.latest_attempt`
   (setatt_ id rule), `**.error.network_advice_code` /
   `**.last_setup_error.{advice_code,network_advice_code,network_decline_code}`
   (network chatter), and the two ownership-message id-only predicates.
7. **Tests** — `tests/test_setup_intents.py`; the seven op_ids added to
   `test_tools.py`'s wired list; `test_expand.py` addition for
   `expand[]=payment_method` off a setup intent.
8. **Spec corrections** — `components/data_model.md`: the `usage` row in
   §"doc-only enums" gains the probed note that the parameter is dead at
   the pinned version (accepted and ignored; the emitted value is always
   `off_session`).

## Tests

- `test_setup_intents.py` — the created body's every field (status,
  client_secret shape, the card-options default, `usage` always
  `off_session`, the ignored `on_session`/`bogus` params, `attach_to_self`
  absent); `requires_confirmation` with a PM; confirm success (attach kept,
  `latest_attempt` minted, `mandate` null, event order `created` →
  [`payment_method.attached`] → `succeeded`); auto-attach of an unattached
  PM (rail flip + event); 3DS park (stub `next_action`, `requires_action`
  event); both decline 402s returned with rows surviving (`last_setup_error`
  shape, `payment_method` NULLed, `decline_code` fallback, the `exp_month`
  param, `setup_failed` event); the missing-method refusal incl. the
  no-customer-default rule; the confirm/cancel wrong-state messages
  verbatim; cancel echo + `canceled` event; update paths (metadata merge,
  description on succeeded 200, `payment_method` on succeeded refused,
  ownership refusals); `verify_microdeposits` refusal verbatim; the lists
  and the missing-id 404 (`No such setupintent`, `param: "intent"`).
- `test_expand.py` — `expand[]=payment_method` and `expand[]=customer` off
  a setup intent.
- Replay — cassette 10 joins `test_replay_conformance.py` (parametrized).
