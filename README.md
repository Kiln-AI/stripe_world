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
| Errors | `INVALID_INPUT`, `INTERNAL` (`src/seahaven_stripe_world/errors.py`); Stripe's own error envelope (`src/seahaven_stripe_world/stripe_errors.py`) is a return value, not an exception |
| Fixtures | `empty` — the schema and nothing else, frozen at `2026-09-01T14:00:00.000Z` |

## Using it

```python
import seahaven_stripe_world

with seahaven_stripe_world.world.instance("empty") as instance:
    ...
```

Serve it over OpenEnv with `uv run seahaven serve`, list its fixtures with
`uv run seahaven fixture list`, and run its tests with `uv run pytest`. No `--world` anywhere: the
distribution (`seahaven-stripe-world`) normalises to the package and world name
(`seahaven_stripe_world`), which is what the CLI's and the pytest plugin's project-name convention
expects. Those names disclose what this is; the name the agent reads on the MCP handshake is
Stripe's own (`mcp_server_name="stripe-mcp"`). AGENTS.md has the rule.

## Conventions

- **Timestamps** are canonical UTC text with milliseconds and a trailing `Z`
  (`2026-06-01T09:00:00.000Z`), written from `ctx.clock.iso()` and never from the wall clock. The
  API's Unix-second form exists only at the serialization edge, in `_time.py`.
- **Ids** are Stripe-shaped (`cus_…`, `pi_…`, `ch_…`), minted by `_ids.stripe_id` from
  `ctx.ids.random`, so a seed replays a run exactly.
- **Money** is integer minor units; rates are TEXT decimal literals. No floats in the money path.
- **The framework** is `seahaven`, taken from its own (private) repository at a pinned commit —
  `[tool.uv.sources]` in `pyproject.toml` holds the full SHA, and `uv.lock` records the resolution.
  A checkout that cannot read `github.com/Kiln-AI/Seahaven` over HTTPS cannot sync. `pydantic` is
  pinned to 2.12.3 per `SEAHAVEN_FINDINGS.md` Entry 1. The framework's docs ship inside the
  installed package: `uv run seahaven docs` prints the directory.
