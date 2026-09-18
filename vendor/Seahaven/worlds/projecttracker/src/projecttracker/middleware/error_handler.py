"""The app-wide error wrapper: nothing reaches the agent that this world did not choose.

Registered first, so it is the outermost layer of the chain and sees every
error every other layer and every tool raises. Its job is to leave the agent
with this product's vocabulary and nothing else: framework errors are restated
as this world's shapes, an unexpected Python exception becomes `INTERNAL`, and a
`WorldBug` is re-raised untouched because it is the author's to see and turning
it into a product error would hide it.

Two doors are deliberate exceptions, and both are about engine text:

* the SQL door (`run_sql`) mimics a product that *is* a SQL console, so SQLite's
  own message is what its errors say -- "database error" would tell an agent
  nothing about the syntax it got wrong. `run_sql` has already put that text on
  the `DbError` it raises, so the rule here is to let it past;
* search (`search_issues`) takes a query string the agent wrote and hands it to
  FTS5, so a `DbError` there is nearly always the agent's query and not this
  world's bug. It is restated as `INVALID_INPUT` on the `query` field, carrying
  FTS5's complaint, which is the only text that says what was wrong with it.

Both are named by tool, in the two sets below, because that is what makes them
exceptions: a `DbError` from anywhere else is this world's failure to write
correct SQL, and the agent is told `INTERNAL`.

`Handler` -- what the rest of the chain looks like from inside a middleware -- is
the framework's own alias, imported from `seahaven.world`, where
`components/world_and_dispatch.md` §1 lists it beside the `World` it describes. A
name that the component document section covering a module gives as part of that
module's interface is public; `seahaven/__init__.py` re-exports only the subset
worth a short import, and `Handler` not being in that subset does not make
`seahaven.world` private. A
world that declares its own copy of a framework type is a world that will drift
from it. The rule is stated in `architecture.md` section 1 and published in the
bundled `docs/reference/api.md`.
"""

import logging
from typing import Any

import seahaven
from projecttracker.errors import Internal, InvalidInput
from projecttracker.world import world
from seahaven.world import Handler

__all__ = ["SEARCH_TOOLS", "SQL_DOOR_TOOLS", "error_handler"]

_log = logging.getLogger("projecttracker.errors")

# The SQL doors: tools whose errors are SQLite's, because SQL is what they take.
# A world that registers `run_sql` under another name names it here too.
SQL_DOOR_TOOLS = frozenset({"run_sql"})

# The tools whose `DbError` means "the agent's query string did not parse".
SEARCH_TOOLS = frozenset({"search_issues"})


@world.middleware
def error_handler(ctx: seahaven.Ctx, call: seahaven.Call, next_: Handler) -> Any:
    """Map everything that escapes a tool into one of this world's error shapes."""
    try:
        return next_(ctx, call)
    except seahaven.ArgumentError as error:
        # Every violation in one error: the framework collected them all so the
        # agent could fix them in one turn.
        raise InvalidInput.from_violations(error.violations) from error
    except seahaven.DbError as error:
        if call.name in SQL_DOOR_TOOLS:
            raise
        if call.name in SEARCH_TOOLS:
            raise InvalidInput("query", error.sqlite_message) from error
        # SQLite failed under a tool that writes its own SQL, so the SQL is this
        # world's and the agent can do nothing with the engine's complaint. The
        # author can: it goes to the log with the text and the traceback.
        _log.error("%s: database error under %s", ctx.instance.id, call.name, exc_info=error)
        raise Internal() from error
    except seahaven.ToolError:
        # This world's own shapes, and any other `ToolError` a tool chose to
        # raise: written for the agent already.
        raise
    except seahaven.WorldBug:
        # The author's, not the agent's. Loudly, and unchanged.
        raise
    except Exception as error:
        # A bug in world code. The framework has already logged it with its
        # traceback; the agent gets this product's wording for "we broke".
        raise Internal() from error
