"""The `World` every other module here registers against."""

import seahaven

world = seahaven.World(
    name="ledger",
    version="3.0.0",
    schema=seahaven.sql_files(__package__, "schema"),
    state_format="seahaven.state/1",
)
