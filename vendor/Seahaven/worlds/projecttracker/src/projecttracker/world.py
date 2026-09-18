"""The `World` object every other module in this package registers against.

One `World` per package, built here so that tool and middleware modules import
it without an import cycle: `world.py` imports nothing of this world's, and
everything of this world's imports `world.py`.
"""

import seahaven

world = seahaven.World(
    name="projecttracker",
    version="1.0.0",
    schema=seahaven.sql_files(__package__, "schema"),
    # The one line an OpenEnv hub shows beside the name. The README beside this
    # package is the card's body and nothing is derived from it, so this
    # sentence is kept in step with the README's opening paragraph by hand.
    description=(
        "A Seahaven world: a fictional issue tracker for a fictional company, and the reference "
        "world the framework is developed against. Nothing here mimics a real product's names, "
        "schema or error text."
    ),
    state_format="seahaven.state/1",
)
