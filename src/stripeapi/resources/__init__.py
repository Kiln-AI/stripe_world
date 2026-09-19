"""One module per resource: its `ResourceSpec`, its serializer field map, and
its hand-written state transitions. `dispatch/routes.py` is the only dispatcher
module that imports these; the engine itself never does.
"""
