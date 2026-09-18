"""The `World` this extension is tested against: ProjectTracker, over XML-RPC.

A copy, not the reference world itself, and the copy is the point. The call log
`seahaven_xmlrpc` offers is a table, and a table is part of the world's schema:
adding it changes the schema hash, and every fixture of that world then has to be
regenerated. `functional_spec.md` §21 point 4 says so, and says it is the cost of
the DDL seam. ProjectTracker's `empty` fixture is committed and belongs to the
reference world, so the extension takes a copy of the schema instead of paying
that cost on someone else's world.

Everything below the protocol is ProjectTracker's: the same DDL, read out of the
installed package, and -- in `middleware/error_handler.py` -- the same error
handler, imported rather than retyped, so what the tests drive is the reference
world's behaviour and not a lookalike written to agree with them.
"""

import seahaven
import seahaven_xmlrpc

world = seahaven.World(
    name="tracker_rpc",
    version="1.0.0",
    # `sql_files` names the package explicitly, because the package this schema
    # comes from is not this one. Concatenation is how a world "includes a DDL
    # string in its schema set": `sql_files` reads files, and an extension ships
    # text.
    schema=seahaven.sql_files("projecttracker", "schema") + seahaven_xmlrpc.CALL_LOG_DDL,
    state_format="seahaven.state/1",
)
