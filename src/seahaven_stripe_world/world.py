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
    # The world's own identity, and a disclosure: the OpenEnv card's name, a
    # directory under the working root, and the `world:` field in every fixture
    # sidecar all read this. It says *synthetic Seahaven world* to every human,
    # hub and coding agent that meets it, and it is spelled the way the package
    # is rather than the way the distribution is, because an added world's
    # default alias is its name and that alias is lowercase letters, digits and
    # single underscores: `seahaven-stripe-world` would be legal here and
    # illegal as the alias, forcing an explicit `name=` at every `add_world`.
    # One name, two spellings, same normalisation the CLI already does.
    name="seahaven_stripe_world",
    version="0.1.0",
    schema=seahaven.sql_files(__package__, "schema"),
    untracked_tables=("counters", "idempotency_keys"),
    # What the MCP handshake publishes, and the only name here the tool-calling
    # agent ever reads: Stripe's own server name, verbatim, so an agent that
    # reads the handshake sees the product and not the replica. The line the
    # name above draws is this one — disclosure everywhere a person looks,
    # fidelity everywhere the agent does (`SEAHAVEN_FINDINGS.md` Entry 10).
    mcp_server_name="stripe-mcp",
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
