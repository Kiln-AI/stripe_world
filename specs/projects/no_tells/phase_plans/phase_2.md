---
status: complete
---

# Phase 2: Account, startup and the `livemode` sweep

## Overview

This phase builds the account foundation that every later phase reads. Three pieces of work:

1. A `startup.py` module with `@world.instance_startup` that builds `ctx.state["account"]` from fixture configuration, holding the account id, mode, name, and the full account object.
2. The `livemode` sweep: remove `"livemode": False` from ~15 resource modules' `constants` and inline literals, and add it centrally in `serialize/fields.py` behind the four-object carve-out (`balance_transaction`, `refund`, `subscription_item`, `discount`).
3. Derive embedded `livemode=` error strings from the instance's mode rather than hardcoding `false`.

The account object becomes a consistent fresh sandbox (functional spec 10.2): charges and payouts disabled, empty capabilities, business_profile sub-fields null, real-shaped account id, missing top-level keys present, no metadata key.

## Steps

1. **Create `src/seahaven_stripe_world/startup.py`**: register `@world.instance_startup` that builds `ctx.state["account"]` with keys `id`, `livemode`, `name`, `country`, `default_currency`, and the full `object` dict. Default to `livemode=True`. Accept `livemode` as a startup kwarg override.

2. **Import `startup` in `__init__.py`**: side-effect import so the hook registers.

3. **Rewrite `tools/account.py`**: `get_stripe_account_info` reads from `ctx.state["account"]["object"]` instead of a hardcoded dict. The account object shape becomes a consistent fresh sandbox matching the probe data.

4. **Add `livemode` centrally in `serialize/fields.py`**: in `to_api()`, after building the dict from columns/constants/derived, inject `livemode` from `ctx.state["account"]["livemode"]` for every object not in the four-object exclusion set. The four excluded objects: `balance_transaction`, `refund`, `subscription_item`, `discount`.

5. **Remove `"livemode": False` from every resource module's `constants`** and every inline literal dict that sets `livemode`. Approximately 15 modules plus `billing/ledger.py` and `billing/invoicing.py`.

6. **Derive `invoiceitems.py`'s `livemode=false` error string** from `ctx.state["account"]["livemode"]` so it says `livemode=true` under a live instance.

7. **Update `conftest.py`**: the `probe` fixture's throwaway worlds need the startup hook registered, or they need `ctx.state["account"]` seeded. Add account state to instances in test fixtures.

8. **Update `billing/ledger.py`**: remove `"livemode": False` from `read_balance`.

9. **Write the source-grep guard test first** (before the sweep): `test_no_hardcoded_livemode` greps the source tree for `"livemode": False` and `"livemode": True` string literals in resource modules and fails if any are found. This is written before the sweep, so the sweep is driven by the test.

## Tests

- `test_no_hardcoded_livemode`: greps `src/` for `livemode` hardcoded in `constants` or inline dicts; fails on any hit outside the test file itself and the four-exclusion set.
- `test_livemode_true_on_objects`: creates a customer with `livemode=True` (the default) and asserts `livemode` is `True` on the response.
- `test_livemode_false_on_sandbox_instance`: creates a customer with `livemode=False` startup override and asserts `livemode` is `False`.
- `test_livemode_absent_on_excluded_objects`: asserts `balance_transaction`, `refund`, `subscription_item`, `discount` carry no `livemode` key.
- `test_account_object_fresh_sandbox`: asserts the account object matches the consistent fresh sandbox shape.
- `test_account_id_shape`: asserts the account id is `acct_` + 16 alphanumeric characters.
- `test_invoiceitem_error_derives_livemode`: asserts the invoice item 404 message says `livemode=true` under a live instance and `livemode=false` under a sandbox.
