"""This world's tools. Importing the package registers every module in it.

`seahaven check` (SH301) fails if a module in this directory is not imported
here, so a tool module that exists but was never registered cannot go unnoticed.
"""

from tracker_rpc.tools import rpc

__all__ = ["rpc"]
