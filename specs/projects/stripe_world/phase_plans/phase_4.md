---
status: complete
---

# Phase 4: Schema conformance harness

## Overview

The cheap, constant half of the conformance component (`components/conformance.md`
§"Schema conformance"): generated validation of every object this world returns
against `spec3.min.json`. Built before any real resource because it constrains
all of them — from Phase 6 on, every resource phase's own tests feed it
automatically. No cassettes, no recorder, no allow-list (that is Phase 5);
a schema violation has no legitimate excuse.

Three pieces: two new generated inputs from the Phase 2 pipeline
(`ENUM_OVERRIDES` in `spec/enums.py`, `spec/schema_rules.json`), and a
test-package validator plus a pytest hook that validates every
`stripe_api_read` / `stripe_api_write` / `call_stripe` response body any test
produces.

The eleven bare-`string`-but-closed fields: the six `DOC_ONLY_ENUMS` entries
plus `setup_intent.usage` (this project's declared reading, marked, not
documented fact) plus the four JSON-column fields (`charge_outcome.type`,
`fee.type`, `payment_method_card.brand`, `payment_method_card.funding`) —
`components/data_model.md` §12, `components/conformance.md` §Public Interface.

## Steps

1. **`tools_dev/prune_spec.py` — `ENUM_OVERRIDES`.** The extra five field sets
   beyond `DOC_ONLY_ENUMS` as a hand-transcribed table beside the existing six
   (same cited-transcription mechanism; prose is not regex-parsed). A
   generation-time assertion — run inside `build_artifacts`, which holds the
   full spec — checks every value token of every entry in both tables appears
   in the live description of that `(schema, field)`, that the property is a
   bare `string`, and that it carries no machine `enum`; a Stripe wording
   change fails regeneration instead of silently dropping a value
   (`components/data_model.md` §12). Rendered into `spec/enums.py` as
   `ENUM_OVERRIDES: dict[str, dict[str, tuple[str, ...]]]` (11 field paths)
   plus `DECLARED_OVERRIDES: frozenset[tuple[str, str]]` marking the
   `setup_intent.usage` reading. `DOC_ONLY_ENUMS` stays exactly the six — it
   remains the DDL `CHECK` source, and `setup_intent.usage` stays
   unconstrained in the DDL (`components/discovery.md` §5's settled ruling).

2. **`tools_dev/prune_spec.py` — `spec/schema_rules.json`.** Normalize every
   schema reachable from the discriminated object roots plus every
   `deleted_*` schema in the pruned closure into structural rule nodes:
   `t` (JSON type), `nul`, `enum`, `items`, `props`+`req` (closed property
   set), `map` (an open `additionalProperties` map, e.g. `metadata`),
   `any` (union members), `ref` (schema-name indirection). `x-*` keys,
   descriptions, titles, maxLength/format/pattern are dropped. Roots keyed by
   `object` discriminator value: `by_object` and `deleted_by_object`
   (`deleted: true` selects the stub). Generation-time checks: no dangling
   `ref`, no `allOf` outside `x-*`, root maps non-empty, enum values only
   str/bool. Canonical JSON dump like `spec3.min.json`.

3. **`src/stripeapi/spec/__init__.py`** — re-export `ENUM_OVERRIDES` /
   `DECLARED_OVERRIDES`; add a cached `schema_rules()` loader mirroring
   `spec_document()`.

4. **Regenerate the committed artifacts** (`python -m tools_dev.prune_spec`).

5. **`tests/schema_conformance/validate.py`** — the validator, test-only by
   design (`components/conformance.md` Dependencies):

   ```python
   @dataclass(frozen=True)
   class SchemaViolation:
       path: str      # dotted from the response body root, "" for the whole object
       problem: str

   def validate_object(obj: dict[str, Any], *, source: str, path: str = "") -> list[SchemaViolation]
   def violations_in_body(body: Any, *, source: str) -> list[SchemaViolation]
   ```

   `obj["object"]` selects the schema (`deleted: true` selects the
   `deleted_*` stub; unknown non-`list` discriminators are themselves a
   violation). Structural checks: required fields, JSON types (bool is not
   an integer), nullability, closed property sets (an undeclared field is a
   violation — the "leaked internal column" catcher), open maps, list items,
   unions (so an unexpanded id-string and an expanded object both pass),
   machine `enum`s compared type-strictly. `ENUM_OVERRIDES` is consulted at
   `(owning schema, field)` wherever the walk sits inside a named schema —
   which is how the four JSON-embedded fields are reached — and only for
   non-null string values. `violations_in_body` walks any body: every dict
   carrying a known `object` discriminator is validated with full path
   context, `"list"` envelopes are recursed into, and every dict's children
   are still walked (open maps such as `event.data.object` are not covered
   by the structural pass); duplicate `(path, problem)` pairs from the
   double coverage are deduped.

6. **`tests/schema_conformance/capture.py` + the hook in `tests/conftest.py`.**
   The design's hook reads `instance.call_log`; a `CallRecord` carries
   `tool`/`arguments`/`error` and **no result** (`seahaven.changes.CallRecord`),
   so the hook wraps `seahaven.Instance.call` instead (monkeypatched in an
   autouse fixture), recording `(ordinal, tool, arguments, result)` for the
   three HTTP tool names. Teardown validates every recorded body and
   `pytest.fail`s with one report listing every violation, its call and its
   path. Registered in the root `tests/conftest.py` — not the component
   doc's `tests/schema_conformance/conftest.py`, which would only cover that
   subdirectory — because the design's own rule is "the resource suite *is*
   the corpus". A test cannot opt out. Framework gap logged as
   `SEAHAVEN_FINDINGS.md` Entry 9.

7. **Tests** (`tests/schema_conformance/test_schema_conformance.py`,
   `tests/schema_conformance/test_rules_artifact.py`).

## Tests

- `test_schema_conformance.py`:
  - `test_a_live_customer_validates` — a real `stripe_api_write` /
    `stripe_api_read` body (the probe customers slice) validates clean.
  - `test_planted_bad_enum_value_fails` — `dispute.reason = "not_a_real_reason"`
    is a violation (the enum-override layer is not a no-op).
  - `test_json_column_enum_override_applies` — a planted bad
    `payment_method_details.card.brand` is caught through the nested walk.
  - `test_undeclared_field_is_a_violation` — an extra field on a customer
    body fails (catches a serializer leaking an internal column).
  - `test_missing_required_and_wrong_type_and_bad_null` — the three
    structural failures, each with its path.
  - `test_nullable_and_optional_absent_both_pass` / `test_expansion_union_accepts_id_and_object`.
  - `test_deleted_stub_validates_against_the_deleted_schema`.
  - `test_list_envelope_is_recursed_and_unknown_discriminators_flagged`.
  - `test_event_data_open_map_contents_are_validated` — the reason the walker
    does not stop at structurally-validated parents.
  - `test_the_hook_validates_every_seahaven_test` — a seahaven test's own
    calls are captured and validated, no opt-in; a planted violation through
    the hook's capture fails the test with a report naming call, path, both
    sides.
  - `test_enum_overrides_are_exactly_the_generated_set` — `ENUM_OVERRIDES`
    equals `prune_spec.ENUM_OVERRIDES` (36 pairs after review round 1's
    pass-2 enrollment), `DECLARED_OVERRIDES` is `setup_intent.usage` alone.
  - `test_conformance_code_never_imports_stripe_python` — AST scan over
    `src/stripeapi/`, `tests/schema_conformance/`, `tests/conformance/`.
- `test_rules_artifact.py` (tier 1, no 8 MB spec needed):
  - `test_rules_match_the_committed_spec_discriminators` — recompute the
    discriminator maps from `spec3.min.json`, compare to `schema_rules.json`.
  - `test_every_ref_in_the_rules_resolves` / `test_rule_enums_are_str_or_bool`.
  - `test_enum_overrides_point_at_real_rules` — every `(schema, field)` pair
    exists in the rules.

## Review round 1: the pass-2 prose-closed curation record

The CR round applied `components/data_model.md` §12's pass-2 rule mechanically
over the whole closure: every bare-`string` field whose description genuinely
closes its value set is enrolled in `ENUM_OVERRIDES` (36 `(schema, field)`
pairs; the generation-time token assertion is the verifier). Enrolled beyond
the original eleven: `charge_fraud_details.user_report`;
`charge_outcome.network_status` and `.risk_level`; the card sets under their
other wire names — `payment_method_details_card.{brand,funding,network}`,
`dispute_payment_method_details_card.{brand,network}`; the checks sets under
both names — `payment_method_card_checks.*` and
`payment_method_details_card_checks.*`; `invoice_payment.status`;
`payouts_trace_id.status`; `refund.failure_reason`; and `reference_status`
on all eight `refund_destination_details_*` schemas.

Deliberately excluded, with reasons:

| Field | Reason |
|---|---|
| `payment_method_card.display_brand` | "…and may contain more values in the future" — explicitly forward-compatible, so open. |
| `charge_outcome.reason` | Values are named per Radar rule ("Charges blocked by … have the value `highest_risk_level`") without the prose claiming the four named values are exhaustive; unlike `setup_intent.usage` there is no spec ruling that declares it closed. |
| `networks.preferred` | "Can be `cartes_bancaires`, `mastercard`, `visa` or `invalid_preference` **if requested network is not valid**" — a conditional enumeration, not a closed set; the network universe is larger. |
| `payment_intent_next_action.type`, `setup_intent_next_action.type` | "Examples include:" — explicitly illustrative. |
| `file.type` | "(for example, `csv`, `pdf`, `jpg`, or `png`)" — explicitly illustrative. |
| `payment_method_card_wallet_{masterpass,visa_checkout}.{email,name}` and their `payment_method_details_card_wallet_*` copies (8 fields) | "Values are verified or provided by the wallet" describes provenance, not a value set; no backticked enumeration at all. |

Pinned by `test_the_fields_whose_prose_does_not_close_are_left_open`
(display_brand, charge_outcome.reason) so reopening any exclusion is a
deliberate change, and by `test_the_pass2_prose_closed_fields_are_enrolled`
(one spot-pin per enrolled family).
