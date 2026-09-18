"""This world's tools. Importing the package registers every module in it.

`seahaven check` (SH301) fails if a module in this directory is not imported
here, so a tool module that exists but was never registered cannot go unnoticed.

The seven modules below are the twenty-five tools this product has, one module per
resource. The last two registrations are the framework's own helpers, and they are
made here rather than in a module of their own because there is nothing of this
world's to write: `run_sql` and `describe_schema` are factories, and what this
world decides about them is which tables they are open onto -- `_types.TABLES`,
the nine this world owns.

The FTS5 index is not among them. An agent reaches search through `search_issues`,
which is a tool with a `MATCH` behind it; opening `issues_fts` to `run_sql` as
well would mean listing its shadow tables and widening the SQL function allowlist,
for a second way to do the one thing the world already has a tool for.

The `_`-prefixed modules beside these -- argument types, pagination, row shapes,
the event trail -- register nothing. They are imported by the tool modules that
use them, which is what puts them in `sys.modules` and satisfies SH301 without a
line here claiming they register something.
"""

import seahaven
from projecttracker.tools import comments, issues, labels, projects, search, teams, users
from projecttracker.tools._types import TABLES
from projecttracker.world import world

__all__ = ["TABLES", "comments", "issues", "labels", "projects", "search", "teams", "users"]

world.tool(
    seahaven.helpers.run_sql(
        tables=TABLES,
        description=(
            "Run one read-only SQL statement (SQLite dialect) against the tracker's tables: "
            f"{', '.join(TABLES)}. Returns columns and rows. Use describe_schema to see them."
        ),
    )
)
world.tool(seahaven.helpers.describe_schema(tables=TABLES))
