"""The dispatch layer: the route table and, from the dispatcher phase, the
router, the generated-CRUD engine and the parameter machinery that hang off it.

`routes.py` lands first, as data: the pruner that builds `spec/` reads it, so
the routed scope has exactly one home from the spec pipeline onward.
"""
