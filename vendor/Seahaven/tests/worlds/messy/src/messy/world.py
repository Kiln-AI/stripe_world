"""The `World`. Its schema is clean; everything wrong here is in the code."""

import seahaven

world = seahaven.World(
    name="messy",
    version="1.0.0",
    schema=seahaven.sql_files(__package__, "schema"),
    state_format="seahaven.state/1",
)
