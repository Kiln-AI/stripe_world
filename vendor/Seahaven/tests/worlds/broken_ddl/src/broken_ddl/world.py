"""`World(...)` builds the schema in memory, so this line is where it fails."""

import seahaven

world = seahaven.World(
    name="broken_ddl",
    version="1.0.0",
    schema=seahaven.sql_files(__package__, "schema"),
    state_format="seahaven.state/1",
)
