---
status: complete
---

# Component: ProjectTracker, the reference world

Package `projecttracker` under `worlds/projecttracker/`, laid out exactly as `seahaven new`
produces (so it is also the scaffold's proof). A fictional Linear/Jira-shaped issue tracker for a
fictional company.

## 1. Schema (`schema/001_core.sql`, `002_search.sql`)

The tables: `users(id, email UNIQUE, name, role CHECK IN admin|member|viewer, created_at)`;
`teams(id, key UNIQUE CHECK 2-5 upper letters, name, created_at)`; `team_members(team_id, user_id,
joined_at, PK(team_id, user_id))`; `projects(id, team_id, name, state CHECK planned|active|done,
issue_counter INTEGER DEFAULT 0, created_at)`; `issues(id, project_id, key UNIQUE, title,
description, status CHECK backlog|todo|in_progress|done|canceled, priority CHECK 0..4, assignee_id
NULL, creator_id, created_at, updated_at, due_at NULL, archived_at NULL)` with the five indexes; `labels(id, team_id,
name, color, UNIQUE(team_id, name))`; `issue_labels(issue_id, label_id, PK)`; `comments(id,
issue_id, author_id, body, created_at)`; `issue_events(id, issue_id, actor_id, kind CHECK
created|status|assignee|comment, payload JSON, created_at)`. All `STRICT`, all timestamps canonical
text. `002_search.sql`: `issues_fts` (FTS5 external content over `issues(title, description)`) and
its three sync triggers, none reading a clock.

## 2. World object and errors

```python
# world.py
world = seahaven.World(name="projecttracker", version="1.0.0",
                       schema=seahaven.sql_files(__package__, "schema"),
                       description="A Seahaven world: a fictional issue tracker for a fictional "
                                   "company, and the reference world the framework is developed "
                                   "against. Nothing here mimics a real product's names, schema "
                                   "or error text.")

# errors.py: the product's shapes (fictional, Linear-flavoured codes), plain ToolError subclasses
class NotFound(seahaven.ToolError):
    def __init__(self, kind: str, key: str): super().__init__("NOT_FOUND", f"{kind} {key} not found", {"kind": kind, "key": key})
class InvalidInput(seahaven.ToolError):
    def __init__(self, field: str, why: str): super().__init__("INVALID_INPUT", f"{field}: {why}", {"field": field})
    @classmethod
    def from_violations(cls, violations): ...   # joins every violation into one INVALID_INPUT
class Conflict(seahaven.ToolError):
    def __init__(self, why: str): super().__init__("CONFLICT", why)
class Internal(seahaven.ToolError):
    def __init__(self, message: str = "Something went wrong"): super().__init__("INTERNAL", message)
```

`middleware/error_handler.py` is the architecture §6 scaffold (including the `WorldBug` re-raise)
plus one product-specific rule: `DbError` from `run_sql` passes through with SQLite's text (a SQL
door shows SQL errors), and a `DbError` from `search_issues` (an FTS5 syntax error) becomes
`InvalidInput("query", ...)`.

*Corrected 2026-09-13 — the `World(...)` sketch gained `description=`, carrying the sentence this
world really passes. `World` gained the argument (`components/world_and_dispatch.md` §1.1) and it is
the only thing that sets the OpenEnv one-line description, replacing the derivation of that line
from the README that `components/openenv.md` §2 specified and no longer does. This section sketches
`worlds/projecttracker/src/projecttracker/world.py`, the file that carries it, and without the
argument it described that file differently from `functional_spec.md` §3.1. The sentence is kept in
step with `README.md`'s opening paragraph by hand; `tests/test_package.py` pins that the README
still contains it.*

## 3. Tools (25 world tools plus the two helpers)

One module per resource under `tools/`, typed parameters, `ctx` first. Shared argument types in
`tools/_types.py`: `IssueStatus`, `Priority`, `Role`, `ProjectState` as `Literal`s;
`Timestamp = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")]`;
`Limit = Annotated[int, Field(ge=1, le=250)]`; `Cursor = str`. Every list returns
`{"<items>": [...], "next_cursor": str | None, "has_next": bool}` by keyset pagination on `(sort
column, id)`, the cursor encoded as base64 JSON `[sort_value, id, sort_key]` and refused when cut
for another ordering.

| Module | Tools | Notes |
|---|---|---|
| `users.py` | `create_user(email, name, role="member")`, `get_user(user_id)`, `list_users(role=None, limit=50, cursor=None)` | email pattern check → `InvalidInput` |
| `teams.py` | `create_team(key, name)`, `get_team(key)`, `list_teams(limit=50, cursor=None)`, `add_team_member(team_id, user_id)`, `list_team_members(team_id, limit=50, cursor=None)` | |
| `projects.py` | `create_project(team_id, name, state="planned")`, `get_project(project_id)`, `list_projects(team_id=None, state=None, limit=50, cursor=None)`, `update_project(project_id, name=None, state=None)` | |
| `labels.py` | `create_label(team_id, name, color)`, `list_labels(team_id, limit=50, cursor=None)`, `set_issue_labels(issue_id, label_ids)` | color `#rrggbb`; `set_issue_labels` replaces the whole set |
| `issues.py` | `create_issue(project_id, title, description="", status="backlog", priority=0, assignee_id=None, due_at=None, actor_id=None)`, `get_issue(issue_id=None, key=None)`, `list_issues(status=None, assignee_id=None, project_id=None, label_ids=None, created_after=None, order="created_at_desc", limit=50, cursor=None)`, `update_issue(issue_id, title=None, ..., actor_id=None)`, `assign_issue(issue_id, assignee_id, actor_id=None)`, `transition_issue(issue_id, status, actor_id=None)`, `archive_issue(issue_id)` | `get_issue` requires exactly one of id/key; a done or canceled issue has no assignee (the product's rule); `key` minted as `TEAMKEY-n` with a per-team counter `UPDATE ... RETURNING`; `archive_issue` stamps `issues.archived_at` from `ctx.clock.iso()` and archived issues are excluded from `list_issues` |
| `comments.py` | `add_comment(issue_id, body, actor_id=None)`, `list_comments(issue_id, limit=50, cursor=None)` | appends an `issue_events` row |
| `search.py` | `search_issues(query, limit=50)` | FTS5 `MATCH`, `bm25`, `snippet`, `ORDER BY rank, issues.id`. No cursor: a keyset cursor over `bm25()` rank is only valid for one query string and no eval pages through search results |
| `__init__.py` (package) | `run_sql(tables=[the nine tables])`, `describe_schema(tables=[...])` | helpers |

`actor_id=None` means "the instance's viewer": a startup hook stores `reset(user_id=...)` in
`ctx.state["viewer_id"]`; a write with neither raises `InvalidInput("actor_id", "no actor")`. Every
write stamps `created_at`/`updated_at` from `ctx.clock.iso()`, every id from `ctx.ids.uuid()`, every
event row through one `record_event` helper in `tools/_events.py`.

## 4. Fixtures

Three, built by `fixtures_src/generate.py` (a module the CLI's `--run` imports; committed):

| id | `now` | Content | Description (for eval authors) |
|---|---|---|---|
| `empty` | `2026-06-01T09:00:00.000Z` | schema only | "The tracker's schema with no rows. Start here to write a history, or to test setup flows." |
| `small_startup` | same | 1 team (`ENG`), 3 users (1 admin, 2 members), 2 projects, 40 issues over 60 days, 60 comments, 6 labels, events for every write; viewer is the admin | "A three-person startup's tracker: one engineering team, two active projects, forty issues spread over the last two months, a handful of labels. Good for single-team triage, assignment and status-change scenarios." |
| `agency` | same | 3 teams (`ENG`, `DES`, `OPS`), 12 users, 9 projects (one done), 600 issues over 180 days, 1,500 comments, 30 labels, 20% reassigned, 25% transitioned; viewer is an admin | "A twelve-person agency: three teams, nine projects including a finished one, six hundred issues with a six-month history, comments, labels and an audit trail. Good for cross-team queries, reporting, bulk operations and search." |

The generator is deterministic on `ctx.ids.random` and `ctx.clock` (fixed vocabulary tables for
titles and comments), uses `inst.bulk()` for rows and the tools for the last few writes so both
paths are exercised, and asserts every timestamp lies within the span before `now` except `due_at`,
which may be up to 45 days after. Size budget for all three: 10 MB (a CI test asserts it).
Rebuilding produces byte-identical files on one SQLite build (a CI test asserts it, with the content
hash as the documented fallback).

## 5. Tests (`tests/`)

Using the plugin, marker per test:

- One test module per tool module: happy path, each declared error, pagination across three pages
  with a stable order and a refused foreign cursor, the assignee-on-closed rule, key minting
  (`ENG-41` after forty issues), events appended.
- `test_fixtures.py`: descriptions present; counts per fixture; the span assertion; regenerate and
  compare hashes (marked slow).
- `test_determinism.py`: the same seed yields the same ids and the same changeset for a scripted
  sequence; a different seed differs.
- `test_search.py`: phrase, prefix, column-scoped queries; a syntax error maps to
  `INVALID_INPUT`; ranking ties broken by id.
- `test_sql_tools.py`: `run_sql` reads all nine tables, refuses writes, returns SQLite's message on a
  typo; `describe_schema` lists the nine tables with their keys.
- `test_openenv.py`: the world through the stock client end to end (reset with `user_id`, list,
  call, error, state), with the serve extra installed.
- `test_errors.py`: no SQLite text escapes through any tool except `run_sql`.

## 6. Docs

`README.md` (the Space card and metadata) and `AGENTS.md` (the scaffold's text plus the world's
notes: key format, timestamp presentation, the viewer rule, the closed-issue rule).
