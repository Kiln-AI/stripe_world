"""Search: FTS5 over issue titles and descriptions.

The query string is FTS5's own, not a language this world invented: a bare word,
a `"quoted phrase"`, a `prefix*`, a `title:scoped` column, and `AND`/`OR`/`NOT`
between them. Ranking is FTS5's `bm25` and is documented as such -- it is not
Lucene's, it is not a vector search, and an eval that grades on "the best result"
is grading on what this SQLite build computes.

**No cursor, and that is deliberate.** Every other list here is keyset-paginated,
which needs a key that orders rows the same way on every page. `bm25()` is not
one: it is a score relative to one query string against one index, so a cursor cut
from it would only be valid for that exact query and would silently mean something
else for any other. The tool takes a `limit` and answers with the best of them,
which is what a search box does.

A query FTS5 cannot parse arrives as a `DbError`, and the error handler
(`middleware/error_handler.py`) restates it as `INVALID_INPUT` on `query`,
carrying FTS5's own complaint -- because the agent wrote the query, and the parser
is the only thing that can say what is wrong with it.
"""

from typing import Any

import seahaven
from projecttracker.tools import _rows
from projecttracker.tools._types import Limit
from projecttracker.world import world

__all__ = ["search_issues"]

# `-1` is FTS5's "the column with the best match": a query that hit the
# description should be shown the description, not a truncated title.
_SNIPPET = "snippet(issues_fts, -1, '[', ']', '...', 16)"

_SEARCH = f"""
SELECT {_rows.ISSUE_COLUMNS_QUALIFIED},
       bm25(issues_fts) AS score,
       {_SNIPPET} AS snippet
FROM issues_fts
JOIN issues ON issues.rowid = issues_fts.rowid
WHERE issues_fts MATCH ?
ORDER BY issues_fts.rank, issues.id
LIMIT ?
"""


@world.tool
def search_issues(ctx: seahaven.Ctx, query: str, limit: Limit = 50) -> dict[str, Any]:
    """Find issues whose title or description matches a full-text query.

    The syntax is SQLite's FTS5: `login` for a word, `"login page"` for a phrase,
    `log*` for a prefix, `title:login` for one column, and `AND`, `OR` and `NOT`
    between them. Results come best-match first, with `score` (lower is better)
    and a `snippet` of the text that matched.

    Archived issues are searchable: an agent looking for what was decided about a
    thing should find it whether or not the issue is still on a list.
    """
    found = ctx.db.rows(_SEARCH, query, limit)
    # Ties on `rank` are broken by `issues.id` in the statement, so the order is
    # total and two runs of one query agree row for row.
    return {"issues": _rows.attach_labels(ctx, found)}
