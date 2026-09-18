"""The ASGI app this world is served as: `uvicorn projecttracker.openenv_app:app`.

One world per server, so one app and nothing to configure here. `seahaven serve`
builds the same object from the same call; this module exists because a
container image, a Space or anything else that takes an ASGI import string needs
a name to point at.

Importing it needs the serve extra (`pip install "projecttracker[serve]"`), which
is why it is a module of its own and not part of the package's `__init__`: a
world used in process, in pytest or in a script, never imports `openenv`.
"""

import seahaven.openenv
from projecttracker import world

app = seahaven.openenv.app(world)
