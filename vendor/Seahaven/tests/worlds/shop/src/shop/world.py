"""The `World` every other module here registers against, and the world it adds.

`payments` is added with an empty allow list: the shop settles through it, and
the agent never sees a payments tool on a shop's surface.
"""

import payments

import seahaven

world = seahaven.World(
    name="shop",
    version="2.1.0",
    schema=seahaven.sql_files(__package__, "schema"),
    state_format="seahaven.state/1",
)
world.add_world(payments.world, name="payments", tool_allow_list=[])
