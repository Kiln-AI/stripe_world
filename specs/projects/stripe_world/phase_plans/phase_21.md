---
status: complete
---

# Phase 21: `get_stripe_account_info`

## Overview

Add the fifth tool to the MCP surface: `get_stripe_account_info`. This tool returns a static account
object -- no database, no routing table entry. The account is hardcoded with sensible defaults for a
billing-focused test-mode account. The tool sits beside the four dispatcher-backed tools in
`tools/account.py` (as the architecture's package layout names it).

## Steps

1. Create `src/seahaven_stripe_world/tools/account.py` with:
   ```python
   @world.tool
   def get_stripe_account_info(ctx: seahaven.Ctx) -> dict[str, Any]:
   ```
   The function returns a dict representing a Stripe Account object with billing-relevant fields:
   `id`, `object`, `business_profile`, `business_type`, `charges_enabled`, `country`, `created`,
   `default_currency`, `details_submitted`, `email`, `metadata`, `payouts_enabled`, `type`,
   `capabilities`, `settings`. Values are hardcoded defaults for a test-mode standard account.

2. Import the new module in `tools/__init__.py`.

3. Update the tool-name list in `test_tools.py`:
   - Add `get_stripe_account_info` to `test_the_tool_descriptions_name_no_sibling_tool`.
   - Update `test_the_wired_surface_is_small_and_named` to expect five tools if applicable
     (this tool has no route -- it is not in the route table).

## Tests

- `test_account_info_returns_account_object`: call the tool, verify `object == "account"`, `id`
  starts with `acct_`, and billing-relevant fields are present.
- `test_account_info_is_idempotent`: two calls return identical results.
- `test_account_info_no_sibling_names`: the description does not name any sibling tool (SH206).
