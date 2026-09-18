"""The call log: a table, a startup hook, and the row the endpoint writes.

An XML-RPC server that a company actually runs keeps an audit trail of what was
called and by whom, and an eval grading on state wants to read it. Providing one
is what makes this example exercise two more of the seams `functional_spec.md`
§21 lists, rather than only the two the endpoint needs:

* **DDL as text** (§21 point 4). `CALL_LOG_DDL` is a string. A world that wants
  the log concatenates it into its schema; the extension does not create the
  table, because nothing in Seahaven lets it -- the schema is the world's, hashed
  and frozen into every fixture. That is the cost the contract names: adding this
  fragment changes the world's schema hash, and every fixture of that world has
  to be regenerated.
* **Instance startup and `reset()` keyword arguments** (§21 point 3).
  `remember_client` is a hook the world registers; the `xmlrpc_client=` an eval
  passes to `world.instance(...)` or to `reset` over OpenEnv reaches it there,
  and the log records which client drove each call.

The log is written inside the call's transaction, so a call that faults rolls its
row back with everything else it did. That is the honest behaviour: the trail is
of what the server *did*, and a faulted call did nothing.
"""

import seahaven

__all__ = [
    "CALL_LOG_DDL",
    "CALL_LOG_TABLE",
    "CLIENT_STATE_KEY",
    "UNKNOWN_CLIENT",
    "record",
    "remember_client",
]

CALL_LOG_TABLE = "xmlrpc_call_log"

# STRICT, an explicit primary key, and no wall-clock default: the rules
# `seahaven check` enforces over a world's schema (SH101 to SH103), which a
# fragment an extension ships has to satisfy like any other, because by the time
# the lint sees it there is only one schema.
CALL_LOG_DDL = f"""
CREATE TABLE {CALL_LOG_TABLE} (
    id TEXT PRIMARY KEY,
    method TEXT NOT NULL,
    client TEXT NOT NULL,
    called_at TEXT NOT NULL
) STRICT;
"""

# Where `remember_client` leaves the client name. `ctx.state` is a plain dict the
# world owns, so a key in it is a name an extension has to publish rather than
# keep to itself.
CLIENT_STATE_KEY = "xmlrpc_client"

UNKNOWN_CLIENT = "unknown"

# What SQLite says when the world forgot the DDL. Matched on the text because
# `DbError` carries the engine's message and its numeric code is the generic
# SQLITE_ERROR that every other prepare failure also has.
_MISSING_TABLE = f"no such table: {CALL_LOG_TABLE}"


def remember_client(ctx: seahaven.Ctx, *, xmlrpc_client: str = UNKNOWN_CLIENT) -> None:
    """Instance startup: record which client drives this instance.

    Registered by the world, `world.instance_startup(remember_client)`, and read
    from `reset`: `world.instance("empty", xmlrpc_client="acme-crm/2.4")`. The
    default is what an eval that does not care gets, so the column is never null
    and a world need not pass anything.
    """
    ctx.state[CLIENT_STATE_KEY] = xmlrpc_client


def record(ctx: seahaven.Ctx, method: str) -> None:
    """One row of the trail, on the instance's clock and its id stream.

    A world that asked for the log and forgot the DDL is told so, on its first
    call, as the author's mistake it is. The factory cannot catch it -- there is
    no schema at registration -- and left alone it is the worst kind of failure:
    SQLite's "no such table" is a `DbError`, `render_faults` turns a `DbError` into
    a plausible `APPLICATION_ERROR` fault, and the framework never logs it because
    a `DbError` is a `ToolError`. Every call of the eval would answer with a fault
    that looks like the product refusing, and nothing anywhere would say why.
    """
    try:
        ctx.db.execute(
            f"INSERT INTO {CALL_LOG_TABLE} (id, method, client, called_at) VALUES (?, ?, ?, ?)",
            ctx.ids.uuid(),
            method,
            str(ctx.state.get(CLIENT_STATE_KEY, UNKNOWN_CLIENT)),
            ctx.clock.iso(),
        )
    except seahaven.DbError as error:
        # On the message, because `DbError` carries SQLite's text and not a code
        # that distinguishes a missing table. Anything else -- a disk error, a
        # constraint -- is re-raised as the `DbError` it is.
        if _MISSING_TABLE not in error.sqlite_message:
            raise
        raise seahaven.WorldBug(
            f"log_calls=True needs the {CALL_LOG_TABLE} table, and this world's schema has none: "
            f"add seahaven_xmlrpc.CALL_LOG_DDL to the world's schema and regenerate its fixtures, "
            f"or register the endpoint with log_calls=False"
        ) from error
