---
status: complete
---

# Phase 6: The Account Tools

## Overview

Register the two missing account tools (`list_available_accounts_or_orgs`,
`manage_stripe_accounts`) and update the existing `get_stripe_account_info` to
use its verbatim description. All three read from `ctx.state["account"]` built
in Phase 2's startup. Surface conformance goes green for all eight registered
tools.

## Steps

1. Add three description constants to `tools/_descriptions.py`:
   `LIST_AVAILABLE_ACCOUNTS_OR_ORGS`, `MANAGE_STRIPE_ACCOUNTS`, and
   `GET_STRIPE_ACCOUNT_INFO` (the last is the documented-tool description,
   distinct from the live schema which has null). Update `__all__`.

2. Rename `tools/account.py` to `tools/accounts.py` (architecture section 2.1
   layout). Add:
   - `list_available_accounts_or_orgs(ctx)` -- no parameters, returns
     `{"accounts": [{"stripe_context": acct_id, "livemode": mode, "name": name}]}`
     projected from `ctx.state["account"]`.
   - `manage_stripe_accounts(ctx)` -- no parameters, returns
     `{"reconsent_url": "https://access.stripe.com/mcp/oauth2/authorize/sessions/oases_<id>"}`.
     The `oases_` id is a 24-char alphanumeric suffix from `ctx.ids.random`.
   - Keep `get_stripe_account_info(ctx)` with its `@world.tool(description=...)`
     using the description constant.

3. Update `tools/__init__.py` to import `accounts` instead of `account`.

4. Update `_EXPECTED_NOW` in `tests/surface/test_surface_conformance.py` to
   include the three new tools. Remove the `xfail` markers from
   `test_ts_03_list_accounts_registered` and `test_ts_19_manage_accounts_registered`.

5. Update `tells.md` dispositions for TS-02 (declared -- kept deliberately per
   functional spec section 4.1.1), TS-03 (closed), TS-19 (closed).

## Tests

- `test_ts_03_list_accounts_registered`: surface conformance for
  `list_available_accounts_or_orgs` -- schema and description match real.
- `test_ts_19_manage_accounts_registered`: surface conformance for
  `manage_stripe_accounts` -- schema and description match real.
- `test_list_accounts_returns_session_accounts`: calls the tool, asserts
  the return shape and that values match `ctx.state["account"]`.
- `test_manage_accounts_returns_reconsent_url`: calls the tool, asserts
  the return shape and that the URL has the right prefix/structure.
- `test_manage_accounts_url_is_deterministic`: two calls on the same
  instance yield the same `oases_` id (seeded stream).
- `test_account_info_description_matches_real`: verifies the description
  constant is byte-identical to the captured artifact.
