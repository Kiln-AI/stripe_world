# StripeAPI

A Seahaven world: a faithful, stateful, forkable replica of Stripe's Billing and Payments core.
An agent inside it makes Stripe API calls against SQLite-backed state that responds the way Stripe
does — objects with real ids, money that moves through a ledger, subscriptions with real statuses,
invoices that finalize and get paid — without touching Stripe. Built for rollouts: thousands in
parallel, each forked from a known fixture in milliseconds, every changed row recoverable.

**Not affiliated with Stripe.** Stripe's field names, enum values and id prefixes are functional API
vocabulary under the source material's MIT licence. This world implements the Stripe MCP tool shape
(`stripe_api_search`, `stripe_api_details`, `stripe_api_read`, `stripe_api_write`) against a pinned
Stripe API version (`2026-08-26.dahlia`).

## What it has

| | |
|---|---|
| Schema | Phase 1 skeleton — tables arrive with the resource phases |
| Tools | none yet — the four Stripe tools arrive with the dispatcher phase |
| Errors | `INVALID_INPUT`, `INTERNAL` (`src/stripeapi/errors.py`); Stripe's own error envelope (`src/stripeapi/stripe_errors.py`) is a return value, not an exception |
| Fixtures | `empty` — the schema and nothing else, frozen at `2026-09-01T14:00:00.000Z` |

## Using it

```python
import stripeapi

with stripeapi.world.instance("empty") as instance:
    ...
```

Serve it over OpenEnv with `uv run seahaven serve --world stripeapi:world`, list its fixtures with
`uv run seahaven fixture list --world stripeapi:world`, and run its tests with `uv run pytest`.
Every `seahaven` subcommand needs `--world stripeapi:world`, because the distribution
(`seahaven-stripe-world`) is not the world's package (`stripeapi`).

## Conventions

- **Timestamps** are canonical UTC text with milliseconds and a trailing `Z`
  (`2026-06-01T09:00:00.000Z`), written from `ctx.clock.iso()` and never from the wall clock. The
  API's Unix-second form exists only at the serialization edge, in `_time.py`.
- **Ids** are Stripe-shaped (`cus_…`, `pi_…`, `ch_…`), minted by `_ids.stripe_id` from
  `ctx.ids.random`, so a seed replays a run exactly.
- **Money** is integer minor units; rates are TEXT decimal literals. No floats in the money path.
- **The framework** is the vendored `vendor/Seahaven`, installed editable; `pydantic` is pinned to
  2.12.3 per `SEAHAVEN_FINDINGS.md` Entry 1.
