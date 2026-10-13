"""The replay engine shared by `small_co` and `big_co` (components/replay.md).

History is replayed, not hand-written: each step runs world code (the API dispatcher, or one of
the engine walkers a frozen clock cannot fire) through a context whose clock reads the step's
instant, inside the builder's one `inst.bulk()` transaction.
"""
