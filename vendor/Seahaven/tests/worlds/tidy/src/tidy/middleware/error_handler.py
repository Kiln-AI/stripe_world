"""The app-wide error wrapper, in the shape every world's is."""

from typing import Any

import seahaven
from seahaven.world import Handler
from tidy.world import world

__all__ = ["error_handler"]


@world.middleware
def error_handler(ctx: seahaven.Ctx, call: seahaven.Call, next_: Handler) -> Any:
    """Leave the agent with this world's vocabulary and nothing else."""
    try:
        return next_(ctx, call)
    except seahaven.WorldBug:
        raise
    except seahaven.ToolError:
        raise
    except Exception as error:
        raise seahaven.ToolError("INTERNAL", "Something went wrong") from error
