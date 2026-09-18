"""A host that adds one leaf twice, under two names and two prefixes.

Both edges take the default store, so `ledger` and `books` are one node -- one
file, one account -- reached by two routes, each contributing the whole of
`ledger`'s tool list under its own prefix. That is two declarations rather than a
collision, and the agent sees one account twice (SH207); the prefixes also leave
`ledger`'s cross-referencing descriptions naming tools no surface has (SH206).
"""

import ledger

import seahaven

world = seahaven.World(
    name="bazaar",
    version="0.1.0",
    schema=seahaven.sql_files(__package__, "schema"),
    state_format="seahaven.state/1",
)
world.add_world(ledger.world, name="ledger", tool_prefix="ledger_")
world.add_world(ledger.world, name="books", tool_prefix="books_")
