"""The `World` every other module here registers against."""

import seahaven

world = seahaven.World(
    name="payments",
    version="1.4.0",
    schema=seahaven.sql_files(__package__, "schema"),
    # Deliberately not the default: this world is a leaf of `emporium`, and a
    # leaf's pin is never the one an instance of the tree answers in.
    state_format="seahaven.state+calls/1",
)
