"""The `World` object every other module in this package registers against.

One `World` per package, built here so that tool and middleware modules import
it without an import cycle: `world.py` imports nothing of this world's, and
everything of this world's imports `world.py`.

`untracked_tables` — for `idempotency_keys` and `counters` — is added by the
phases that create those tables, not here: naming a table that does not exist
yet would be a pin on a schema this world does not have.
"""

import seahaven

world = seahaven.World(
    name="stripeapi",
    version="0.1.0",
    schema=seahaven.sql_files(__package__, "schema"),
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
