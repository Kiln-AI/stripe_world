"""The `World` object every other module in this package registers against.

One `World` per package, built here so that tool and middleware modules import
it without an import cycle: `world.py` imports nothing of this world's, and
everything of this world's imports `world.py`.

`untracked_tables` names `counters` from the dispatcher phase on: every insert
bumps a counter, so a tracked `counters` would put a bookkeeping row into the
change log of every graded episode (`components/data_model.md` §5).
`idempotency_keys` joins it for the same reason — a middleware write must
never appear in an episode's change log (`components/cross_cutting.md`
§3.1.8) — and because a short-circuit's defining property is that the log
gains zero records for it.
"""

import seahaven

world = seahaven.World(
    # Not `seahaven_stripe_world`, which is what the distribution, the package and
    # the hub card should say, because this one string is also the MCP
    # `serverInfo.name` the tool-calling agent handshakes against
    # (`seahaven.mcp.server`, `Server(name=resolved.name)`). Renaming it before the
    # framework can carry an agent-facing name of its own would move the disclosure
    # name into the agent's view, which is backwards. It moves to the disclosing
    # form once `World(mcp_server_name=...)` exists to hold `stripe-mcp`:
    # `SEAHAVEN_FINDINGS.md` Entry 10.
    name="stripeapi",
    version="0.1.0",
    schema=seahaven.sql_files(__package__, "schema"),
    untracked_tables=("counters", "idempotency_keys"),
    # The one line an OpenEnv hub shows beside the name. `README.md` is the
    # card's whole body, and nothing is derived from it; this sentence is kept
    # in step with the README's opening paragraph by hand.
    description=(
        "A Seahaven world: a faithful, stateful, forkable replica of Stripe's Billing and "
        "Payments core behind the four-tool Stripe MCP surface. Not affiliated with Stripe."
    ),
    # The shape `inst.state()` answers in, pinned here at the world's creation.
    # Changing it changes what every eval of this world saves, so bump `version`
    # with it.
    state_format="seahaven.state/1",
)
