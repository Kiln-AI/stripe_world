"""A perfectly good Python package that is not a world.

The mistake this stands for is a real one: a `world.py` that was never imported
from the package `__init__`, so the attribute the tooling looks for is not there.
"""

__all__ = ["NOT_A_WORLD"]

NOT_A_WORLD = "this package forgot to export world = seahaven.World(...)"
