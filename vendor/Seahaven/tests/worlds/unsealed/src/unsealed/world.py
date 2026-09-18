"""A host whose allow list names a tool the world it adds does not contribute.

Nothing here raises at import: a composition is sealed at the first *use* of the
tree, so this world is built, imported and registered against exactly like any
other, and the refusal arrives at `world.instance(...)`, at `world.tools`, or --
which is the point of it -- at `seahaven check`.
"""

import ledger

import seahaven

world = seahaven.World(
    name="unsealed",
    version="0.1.0",
    schema=seahaven.sql_files(__package__, "schema"),
    state_format="seahaven.state/1",
)
world.add_world(ledger.world, name="ledger", tool_allow_list=["post_entrie"])
