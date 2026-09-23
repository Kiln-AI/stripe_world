"""Discovery: how an agent finds operations across the full Stripe API catalogue.

``index.py`` is the runtime catalogue over ``discovery_index.json`` — a
precomputed index of all 123 catalogued operations (architecture §5.1).
The two discovery tools in ``tools/api.py`` are thin faces over it.

The index covers the entire Stripe MCP catalogue, not only the routed
subset, so an agent finds an operation, tries it, and is told its key
cannot use it.  That sequence is coherent; an empty search result is not.
"""

from seahaven_stripe_world.discovery.index import BY_OP_ID, INDEX, Operation, details, search

__all__ = ["BY_OP_ID", "INDEX", "Operation", "details", "search"]
