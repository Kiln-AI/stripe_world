"""This world's middleware. Importing the package registers every module in it.

The order of these two imports is the order of the chain, outermost first: the
product's error handler wraps the XML-RPC response layer, so a `DbError` becomes
a fault document rather than this product's `INTERNAL`.

`seahaven check` (SH301) fails if a module in this directory is not imported
here, so a middleware that exists but was never registered cannot go unnoticed.
"""

from tracker_rpc.middleware import error_handler, faults

__all__ = ["error_handler", "faults"]
