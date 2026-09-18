# ProjectTracker: a walkthrough

ProjectTracker is Seahaven's reference world: a fictional issue tracker, shaped like Linear or Jira,
for a fictional company. It is the framework's integration test, the world the authoring docs are
written against, and the pattern a new world copies. There is no second reference world.

It is a **working tracker**, deep enough to grade an eval on state, rather than a benchmark world.
Nothing in it mimics a real product's names, schema or error text.

You will find it at `worlds/projecttracker/` in the Seahaven repository. Read this page for the
shape, then read the package for the detail, in this order: `world.py`, `schema/`, `errors.py`,
`middleware/error_handler.py`, `tools/`, `fixtures_src/generate.py`.

## Driving it

```python
import projecttracker

with projecttracker.world.instance("agency", seed=7) as tracker:
    # Product-shaped keys, and a tool that takes either a key or an id.
    issue = tracker.call("get_issue", key="ENG-12")

    # Every list is keyset-paginated and answers the same three fields.
    page = tracker.call("list_issues", status="todo", limit=2)
    assert sorted(page) == ["has_next", "issues", "next_cursor"]
    if page["has_next"]:
        tracker.call("list_issues", status="todo", limit=2, cursor=page["next_cursor"])

    # FTS5, best match first, with a snippet of what matched.
    found = tracker.call("search_issues", query="billing", limit=3)
    assert "score" in found["issues"][0] and "snippet" in found["issues"][0]

    # A writing tool, and the trail it leaves.
    tracker.call("transition_issue", issue_id=issue["id"], status="done")
    assert any(record.table == "issue_events" for record in tracker.change_log())
```

## The package

```
worlds/projecttracker/
  src/projecttracker/
    __init__.py            # imports world, startup, middleware, tools -- for their side effects
    world.py               # the World: name, version, sql_files(__package__, "schema")
    errors.py              # NOT_FOUND, INVALID_INPUT, CONFLICT, INTERNAL
    startup.py             # the viewer: who this session is driving the tracker as
    middleware/error_handler.py
    openenv_app.py
    schema/001_core.sql    # nine tables
    schema/002_search.sql  # the FTS5 index and its three triggers
    tools/                 # one module per resource, plus four `_`-prefixed shared ones
  fixtures_src/generate.py # the script all three fixtures are built by
  fixtures/{empty,small_startup,agency}/
  tests/                   # pytest, on Seahaven's plugin
```

`__init__.py` is four lines and no cleverness: build the world, then import the modules whose import
registers things. That is the whole reason those imports exist, and `seahaven check` (`SH301`) fails
if a module under `tools/` or `middleware/` is missing from them.

## The schema

Nine tables and one FTS5 index. Users belong to teams, teams own projects, projects hold issues,
issues carry labels and comments, and every write to an issue appends to an audit trail.

| Table | Notes |
|---|---|
| `users` | `role` is `admin`, `member` or `viewer`, with a `CHECK` |
| `teams` | `key` is 2–5 capitals (`ENG`), and `issue_counter` is the per-team issue-key counter |
| `team_members` | a composite primary key, `(team_id, user_id)` |
| `projects` | `state` is `planned`, `active` or `done` |
| `issues` | `key` is unique; five indexes, one per filter column and one per ordering |
| `labels` | unique per `(team_id, name)`; `color` is a `#rrggbb` `GLOB` |
| `issue_labels` | the join, with a composite primary key |
| `comments` | oldest first, ordered by `(created_at, id)` |
| `issue_events` | the audit trail; `payload` is JSON in TEXT, with `CHECK (json_valid(payload))` |
| `issues_fts` | FTS5 over title and description, `content='issues'`, kept in step by three triggers |

Every table is `STRICT` with an explicit primary key. Every timestamp is canonical text written by
the tool that makes the row. There is not one wall-clock expression in the schema, and the FTS5
triggers do not stamp a time either, because a trigger that did would be the one place in this world
where a row's time did not come from `ctx.clock`.

`payload TEXT CHECK (json_valid(payload))` is worth copying. A `STRICT` table has no JSON storage
class, so the `CHECK` is what makes the column mean JSON rather than merely hold it.

## Errors

Four shapes, in `errors.py`, all `seahaven.ToolError` subclasses with `SCREAMING_SNAKE` codes. Those
are this fictional product's vocabulary; Seahaven's own three (`invalid_arguments`, `db_error`,
`unknown_tool`) are lower case.

| Code | Raised for |
|---|---|
| `NOT_FOUND` | a record the caller named does not exist |
| `INVALID_INPUT` | an argument the product will not accept; also the framework's `ArgumentError`, restated with every violation in one message |
| `CONFLICT` | well formed, but contradicts the state of the tracker |
| `INTERNAL` | something failed in a way this product does not explain |

`middleware/error_handler.py` is where framework errors become these, and it names its two
deliberate exceptions by tool:

- **`run_sql`** keeps SQLite's own message, because the product being mimicked *is* a SQL door and
  "database error" would tell an agent nothing about the syntax it got wrong;
- **`search_issues`** turns a `DbError` into `INVALID_INPUT` on the `query` field, carrying FTS5's
  complaint, because the agent wrote the query and the parser is the only thing that can say what is
  wrong with it.

A `DbError` from anywhere else is this world's own failure to write correct SQL. It goes to the log
with its traceback, and the agent gets `INTERNAL`. That is why the tools look a parent up with a
`SELECT` before inserting, rather than letting a foreign key refuse the write: a constraint failure
would turn a sentence the agent could act on into `INTERNAL`.

## The tools

Twenty-five, one module per resource, plus Seahaven's two SQL helpers.

| Module | Tools |
|---|---|
| `users.py` | `create_user`, `get_user`, `list_users` |
| `teams.py` | `create_team`, `get_team`, `list_teams`, `add_team_member`, `list_team_members` |
| `projects.py` | `create_project`, `get_project`, `list_projects`, `update_project` |
| `labels.py` | `create_label`, `list_labels`, `set_issue_labels` |
| `issues.py` | `create_issue`, `get_issue`, `list_issues`, `update_issue`, `assign_issue`, `transition_issue`, `archive_issue` |
| `comments.py` | `add_comment`, `list_comments` |
| `search.py` | `search_issues` |
| `tools/__init__.py` | `run_sql` and `describe_schema`, from `seahaven.helpers`, over the world's nine tables |

The four `_`-prefixed modules beside them register nothing, and they are the part most worth
stealing.

- **`_types.py`** holds the argument types every tool spells its parameters with. The `Literal`s
  mirror the schema's `CHECK` constraints deliberately: the `CHECK` is what the database will not
  store, and the `Literal` is what the agent is told *before* it tries. They are plain assignments
  rather than `type` aliases, because pydantic publishes a PEP 695 alias as a `$def` with a `$ref`,
  and the tool list should carry the five statuses where the argument is rather than a reference to
  resolve.
- **`_pagination.py`** holds the keyset cursor. Never an `OFFSET`: an offset page re-reads
  everything before it and shifts under a concurrent insert, so an agent walking a list can see a
  row twice. The key is `(sort column, id)`, because the sort column alone is not unique, and the
  cursor is base64 of JSON carrying both halves plus a key naming the resource and the ordering, so
  a cursor from one list is refused by every other. Opaque, but not secret.
- **`_rows.py`** holds the row shapes and the `require_*` lookups that raise `NOT_FOUND`.
- **`_events.py`** holds the audit trail append.

The FTS5 index is deliberately *not* in `run_sql`'s table list. An agent reaches search through
`search_issues`, and opening `issues_fts` to raw SQL would mean allowing its shadow tables for a
second way to do the one thing the world already has a tool for.

## The product rules worth knowing

These are the rules an eval can rely on, and they are the kind of decision every world has to make.

**Issue keys are `TEAMKEY-n`, minted from a counter on the team.** `ENG-41` is the forty-first issue
team `ENG` ever had, whichever of its projects it is in. The counter is bumped by the same `UPDATE
... RETURNING` that reads it, so two issues never take one number and a number is never handed back.
Anything that writes issues without that statement, such as a bulk load in a fixture generator, has
to leave the counter where the tools would have.

**A closed issue has no assignee.** Moving an issue to `done` or `canceled` drops its assignee and
records the drop, and assigning a closed issue is a `CONFLICT`. So `status IN ('done','canceled')
AND assignee_id IS NOT NULL` is a state this world cannot reach, and a grader may rely on it.

**An archived issue keeps its fields and leaves the lists.** `archive_issue` stamps `archived_at`
and takes the issue out of `list_issues`. `get_issue`, `search_issues` and `run_sql` still see it,
and no tool will change a field of it again. `add_comment` is the deliberate exception: archiving
freezes the issue, not the conversation about it. "Read-only" is therefore the wrong word for it,
and the world's own notes avoid it.

**The viewer.** `world.instance(fixture, user_id=...)` — `reset(user_id=...)` over a server — names
who the session is driving the tracker as. `startup.py` puts it in `ctx.state["viewer_id"]`, falling
back to the workspace's first admin. Every write takes an optional `actor_id` which wins, so one run
can have two people writing without two instances. A write with neither, in a workspace with no
admin such as a blank instance or `empty`, is `INVALID_INPUT`. A `user_id` naming nobody is a
`WorldBug` rather than a product error, because it comes from the eval's `reset` and not from the
agent.

**Order within one run.** The clock does not move, so every row one run writes shares a
`created_at`. Reading `issue_events` in the order things happened means `ORDER BY created_at,
rowid`. `list_comments` cannot do that, because a keyset cursor carries its tiebreaker as a value
and `rowid` is not a projected column, so it orders by `(created_at, id)` and says so in its
docstring: oldest first across the fixture's history, then id order among the comments one run
wrote. Do not write an eval that grades on the order of comments an agent added. Grade on the rows.

## The fixtures

All three are frozen at `2026-06-01T09:00:00.000Z`, and all three are built by
`fixtures_src/generate.py`, which is the only way one is made here.

| id | What is in it |
|---|---|
| `empty` | the schema with no rows. Where a setup-flow eval starts, and what a fork would start from |
| `small_startup` | one engineering team, three people, two projects, forty issues over two months, sixty comments, six labels |
| `agency` | three teams, twelve people, nine projects including a finished one, six hundred issues over six months, fifteen hundred comments, thirty labels, and an audit trail with reassignments and transitions in it |

`agency` is also the fixture the framework's benchmark runs against.

To rebuild one, delete its directory and re-run the generator. Never edit one in place.

## Its tests

`worlds/projecttracker/tests/` is a worked example of [testing.md](testing.md): every tool through a
real `instance.call`, the fixtures' invariants, the declared errors by code, the error handler's two
exceptions, pagination's cursor rules, and determinism from the same seed. Nothing builds an
instance in a `conftest.py`, and nothing calls a tool function directly.

```sh
uv run pytest worlds/projecttracker
uv run seahaven check
```

## What to copy, and what not to

Copy the module layout, `_types.py`, the keyset cursor, the error vocabulary, the handler's named
exceptions, the `require_*` lookups, and the habit of writing down the product rules an eval may
rely on in the world's own `AGENTS.md`.

Do not copy the *product*. ProjectTracker's rules are ProjectTracker's. Your world's rules come from
the real product it mimics. Read this one to see what those decisions look like, then make your own.
