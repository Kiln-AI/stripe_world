"""The `World` every other module here registers against."""

import seahaven

world = seahaven.World(
    name="tidy",
    version="1.0.0",
    schema=seahaven.sql_files(__package__, "schema"),
    state_format="seahaven.state/1",
)
