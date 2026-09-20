---
status: complete
---

# Phase 7: Catalog

## Overview

The catalog slice: `products`, `prices`, `coupons`, `promotion_codes`, `tax_rates` — five
resources, 23 routes, almost entirely generated `ResourceSpec` CRUD (the slice the plan says
proves the engine carries its weight). The one hand-written seam is prices' `lookup_key`
transfer (archiving the holding price is a second-row write the normalizer contract forbids)
and the coupon-`valid` recomputation. Step 3 records this slice's cassette against real test
mode; where a recording contradicts the data-model DDL (it already does once: at the pinned
version `promotion_code` nests its coupon under `promotion: {type, coupon}` rather than a
top-level `coupon` field), the recording wins and the spec artifact is corrected in-phase.

## Steps

1. **`src/stripeapi/schema/001_core.sql`** — the five catalog tables + indexes, DDL verbatim
   from `components/data_model.md` §3 (products 18 cols, prices 20, coupons 17,
   promotion_codes 12, tax_rates 17). `counters` already seeds all five names.
2. **Probe the real API** (recipe step 3) — `tools_dev/scenarios/probe_catalog.py`, recorded:
   defaults of each create (product `marketing_features`, price `recurring.interval_count`,
   tax-rate `effective_percentage`/`rate_type`, promotion-code minted `code` shape and
   `promotion` nesting), the cross-field refusals (coupon `percent_off`/`amount_off` xor,
   price without product, price without an amount, promotion-code without `promotion.coupon`),
   the lookup_key conflict and `transfer_lookup_key` archive semantics, retrieve-after-delete
   stubs, filter behavior (`prices?type=`, `products?ids[]=`, `promotion_codes?coupon=`),
   `missing_path_param` spelling per resource, and the catalog events'
   shapes (`product.updated` `previous_attributes`, `coupon.deleted` snapshot) via `/v1/events`.
3. **`src/stripeapi/resources/products.py`** — FieldMap + ParamSpecs (create body: `name` req,
   `active`, `default_price_data`, `description`, `images`, `marketing_features`, `metadata`,
   `package_dimensions`, `shippable`, `statement_descriptor`, `tax_code`, `unit_label`, `url`;
   update body adds `default_price`); soft delete; `updated` stamped on every write; events
   `product.created/updated/deleted`. `default_price_data` = inline price create (writes the
   price row then points the product at it — done in `before_create`'s column derivation is
   impossible across two tables, so products create carries a thin hand-written wrapper that
   calls the engine then the price insert, per the recorded write order).
4. **`src/stripeapi/resources/prices.py`** — FieldMap + ParamSpecs; `product` XOR
   `product_data`; `recurring` → `type=recurring` + the canonical nested shape; tiered prices
   stored as JSON; `lookup_key` + `transfer_lookup_key` (hand-written create: archives the
   live holder then inserts, emitting the holder's `price.updated`); list filters `active`,
   `created`, `currency`, `product`, `type`, `recurring`, `lookup_keys`; events
   `price.created/updated`; no delete.
5. **`src/stripeapi/resources/coupons.py`** — caller-suppliable unprefixed `id`
   (`_ids.coupon_id`); `percent_off` TEXT decimal emitted as JSON number; `amount_off` XOR
   `percent_off`, `currency` required with `amount_off`, `duration_in_months` with
   `repeating` (the DDL CHECKs are the second lock; the handler raises Stripe's own messages
   first); `valid` recomputed on writes that can change it; `applies_to` canonical
   `{products: [...]}`; soft delete; events `coupon.created/updated/deleted`.
6. **`src/stripeapi/resources/promotion_codes.py`** — `promotion: {type, coupon}` request
   nesting (pinned-version shape, recording wins over the DDL comment) stored on the `coupon`
   column; minted `code` from `ctx.ids.random`; `restrictions` canonical shape; update
   (`active`, `metadata`, `restrictions`); list filters `active`, `code`, `coupon`, `created`,
   `customer`; events `promotion_code.created/updated`.
7. **`src/stripeapi/resources/tax_rates.py`** — percentage TEXT decimal; `flat_amount`,
   `rate_type`, `effective_percentage`, `jurisdiction_level` as recorded (read-only columns
   written only at create); update body (`active`, `description`, `display_name`,
   `jurisdiction`, `metadata`, `state`, plus `country`/`tax_type` if recorded); list filters
   `active`, `created`, `inclusive`; events `tax_rate.created/updated`.
8. **`src/stripeapi/dispatch/routes.py`** — wire the 23 catalog routes (5 products, 4 prices,
   5 coupons, 4 promotion_codes, 4 tax_rates, +1 DELETE each where the spec has one) with
   `response_object`/`envelope`.
9. **`tests/conformance/allowed_differences.py`** — catalog-shaped entries: reference-valued
   `coupon`/`product`/`default_price` fields (predicated id-shaped pairs), minted `code`
   randoms, and any recorded account-specific omissions.
10. **Record `s07_catalog.py`** — the curated replayable cassette: product cycle with
    `default_price_data`, price create one-time + recurring + `product_data`, lookup_key
    transfer, coupon percent + amount + delete + retrieve-after-delete, promotion_code create
    (minted code + restrictions) + update + filters, tax_rate create + update + list, and the
    catalog 404s.
11. **Tests** — `tests/test_products.py`, `tests/test_prices.py`,
    `tests/test_coupons.py`, `tests/test_promotion_codes.py`, `tests/test_tax_rates.py`:
    round-trips, defaults, every probed refusal, events, `valid` transitions, transfer
    semantics, `x_seq`-ordered lists.

## Tests

- `test_products.py` — create defaults (`active: true`, `images: []`,
  `marketing_features: []`, `default_price` absent), full-body round-trip,
  `default_price_data` sets `default_price`, update stamps `updated` and emits
  `product.updated` with `previous_attributes`, delete stub + retrieve-after-delete stub,
  list filters `active`/`ids`.
- `test_prices.py` — one-time and recurring shapes (`recurring.interval_count: 1`,
  `usage_type: "licensed"`), `product` vs `product_data`, `unit_amount_decimal` round-trip,
  tiers + `tiers_mode`, `transform_quantity`, lookup_key conflict refusal and
  transfer archive (old price `active: false`, `price.updated` emitted), filters
  `product`/`type`/`currency`/`active`/`lookup_keys`, no DELETE route (405 shape), events.
- `test_coupons.py` — percent/amount/xor refusals, `id` round-trip and minted shape,
  `valid` false past `redeem_by`/`max_redemptions` where computable, `applies_to`,
  update (`name`, `metadata`, `currency_options`), delete + retrieve stub, list, events.
- `test_promotion_codes.py` — `promotion` nesting (stored `coupon`, wire `promotion`), minted
  `code` shape, restrictions canonical shape, update `active`, filters `coupon`/`code`/
  `active`/`customer`, unknown-coupon refusal (400 `resource_missing`), events.
- `test_tax_rates.py` — percentage decimal round-trip (`8.875` not `8.875000`), required
  params, `effective_percentage`/`rate_type` as recorded, update, filters `active`/`inclusive`,
  events.
- `tests/conformance/test_replay_conformance.py` — cassette 07 replays green via the merge
  gate (parametrized, no new test code).
