"""Seahaven: a framework for building synthetic worlds.

The names below are the short-import subset of the public API, plus the
`helpers` and `sandbox` modules. They are not the whole of it: a name the
component document section covering a module lists as part of that module's
interface is public too, and is imported from that module -- `Handler`,
`Middleware` and `StartupHook` from `seahaven.world`, `load`, `load_all`,
`verify` and `freeze` from `seahaven.fixtures`, `SeahavenClient` and the action
and observation models from `seahaven.openenv` in the `serve` extra.
`docs/reference/api.md` is the published list and `architecture.md` section 1 is
the rule. A name in neither place is internal and may change without notice.
"""

from importlib.metadata import PackageNotFoundError, version

from seahaven import helpers, sandbox
from seahaven.call import Call
from seahaven.changes import CallRecord, LogRecord
from seahaven.clock import Clock
from seahaven.ctx import Ctx
from seahaven.db import Db
from seahaven.errors import (
    ArgumentError,
    DbError,
    SeahavenError,
    ToolError,
    UnknownTool,
    WorldBug,
)
from seahaven.fixtures import Fixture
from seahaven.handles import WorldHandle, Worlds
from seahaven.ids import Ids
from seahaven.instances import Instance
from seahaven.tool import Tool
from seahaven.world import World, sql_files

try:
    __version__ = version("seahaven")
except PackageNotFoundError:  # imported from a source tree that was never installed
    __version__ = "0.0.0+unknown"

__all__ = [
    "ArgumentError",
    "Call",
    "CallRecord",
    "Clock",
    "Ctx",
    "Db",
    "DbError",
    "Fixture",
    "Ids",
    "Instance",
    "LogRecord",
    "SeahavenError",
    "Tool",
    "ToolError",
    "UnknownTool",
    "World",
    "WorldBug",
    "WorldHandle",
    "Worlds",
    "helpers",
    "sandbox",
    "sql_files",
]
