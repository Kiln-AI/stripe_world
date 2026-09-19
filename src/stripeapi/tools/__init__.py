"""This world's tools. Importing the package registers every module in it.

`seahaven check` (SH301) fails if a module in this directory is not imported
here, so a tool module that exists but was never registered cannot go unnoticed.

The four Stripe MCP tools (`api.py`) land with the dispatcher phase; until then
this package is deliberately empty.
"""

__all__: list[str] = []
