---
status: complete
---

# Phase 9: Serialization Completeness

## Overview

Close the remaining serialization tells: add missing fields to resource FieldMaps, fix the pruner so the `account` schema reaches the pruned spec (and schema conformance), write the exclusion test for the three product fields the spec says not to emit, and write the generated serialization-completeness test driven by the probe's captured objects.

## Steps

1. **Pruner fix: seed `account` into the closure.** Add a `TOOL_OUTPUT_SCHEMAS` set containing `"account"`. After the main closure walk, run a second pass that seeds these schemas into the closure (bypassing the stoplist for the seed itself, but honouring it for the seed's children). This brings the account schema and its sub-schemas (controller, external_accounts, requirements, future_requirements, tos_acceptance) into spec3.min.json and schema_rules.json without removing `account` from STOPLIST_NAMES (which would let every `on_behalf_of` union inflate the closure).

2. **Add missing fields to FieldMaps.** For each field present in the schema but absent from our serialization:
   - `payment_intent.payment_details`: constant `None` (nullable object in schema)
   - `payment_intent.hooks`: constant `None`
   - `payment_intent.presentment_details`: constant `None`
   - `charge.radar_options`: constant `None` (spec types as `{"type":"null"}`, live emits `{}`)
   - `charge.presentment_details`: constant `None`
   - `charge.refunds`: constant `OMIT` (expand-only inline list)
   - `charge.transfer`: constant `None` (Connect)
   - `subscription.presentment_details`: constant `None`
   - `setup_intent.attach_to_self`: constant `None`
   - `customer.business_name`: constant `None`
   - `customer.individual_name`: constant `None`
   - `customer.invoice_credit_balance`: constant `None`
   - `invoice.confirmation_secret`: constant `None`
   - `invoice.payments`: `OMIT` (inline list)
   - `invoice.threshold_reason`: constant `None`

3. **Product exclusion test.** A test that creates a product through the API and asserts that `attributes`, `type`, and `tax_details` are absent from the response body.

4. **Generated serialization-completeness test.** A test that, for each object type we return, compares the set of fields our serialization emits against the schema's declared property set. Any field in the schema but absent from our output (not OMIT) fails the test.

5. **Update tells register.** Close the serialization tells that prior phases already coded (AS-12, AS-13, AS-22, AS-23, AS-24, AS-25, AS-29, AS-30, AS-31) and that this phase adds. Declare EC-09 (cache_context_key on deleted stubs) -- the spec types deleted_customer as a three-key stub, so adding a fourth field would break schema conformance.

6. **Regenerate spec artifacts.** Run `python -m tools_dev.prune_spec` against the committed full spec at `src/seahaven_stripe_world/spec/spec3.json` to produce updated spec3.min.json and schema_rules.json that include the account schema.

## Tests

- `test_product_excluded_fields`: creates a product and asserts `attributes`, `type`, `tax_details` are absent from the response
- `test_serialization_completeness`: for each object type in the by_object table, creates a minimal object and checks that every schema-declared property is either present in the output or in the OMIT set
- `test_as_26_payment_details_present`: payment_intent response includes `payment_details`
- `test_as_28_radar_options_present`: charge response includes `radar_options` (as null)
- `test_pruner_includes_account_schema`: the pruned spec includes the account schema
