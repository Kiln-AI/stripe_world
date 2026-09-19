"""Discovery: how an agent finds its way into the dispatcher without the 148
operations enumerated up front. `index.py` is the runtime catalogue over
`spec3.min.json`; the two discovery tools in `tools/api.py` are thin faces
over it.
"""

from stripeapi.discovery.index import BY_KEY, INDEX, Operation, details, search

__all__ = ["BY_KEY", "INDEX", "Operation", "details", "search"]
