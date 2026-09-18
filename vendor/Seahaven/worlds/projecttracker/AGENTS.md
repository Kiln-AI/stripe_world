# ProjectTracker

A Seahaven world. This file holds only what is particular to this world; the framework's bundled
docs are the place to start, and the commands below say how to read them.

Commands: `uv run seahaven check` runs every lint over this world and is what to run before a
commit; `uv run seahaven fixture list` lists its fixtures and `uv run seahaven fixture freeze` mints
one; `uv run seahaven docs` prints the directory the bundled authoring documentation is installed
in. Those pages are written: start at `index.md`, then `concepts.md` and `authoring.md`, and read
`projecttracker.md` there for a walkthrough of this world.

This world lives inside the Seahaven repository rather than beside it, so the repository's own
`AGENTS.md` — Python 3.14, fully typed, `ty` and `ruff` and the tests clean before any commit —
applies here too.

## About this world

- **Scope.** The whole tracker: nine tables and an FTS5 index, twenty-five tools plus Seahaven's two
  SQL helpers, three fixtures and the generator that makes them.
- **Layout.** One module per resource under `tools/`, and the `_`-prefixed modules beside them are
  shared and register nothing: `_types.py` (the argument types every tool spells its parameters
  with), `_pagination.py` (the keyset cursor), `_rows.py` (row shapes and the `require_*` lookups),
  `_events.py` (the audit trail). A new tool goes in the module for its resource, or in a new one
  that `tools/__init__.py` imports — `seahaven check`'s SH301 fails if it does not.
- **Timestamps** are canonical UTC text with milliseconds and a trailing `Z`, e.g.
  `2026-06-01T09:00:00.000Z`. Every one of them comes from `ctx.clock.iso()`. There are no
  wall-clock reads in world code and no time defaults in the DDL.
- **Ids** come from `ctx.ids.uuid()` and randomness from `ctx.ids.random`, so one seed replays one
  run.
- **Issue keys** are `TEAMKEY-n`, minted by `UPDATE teams SET issue_counter = issue_counter + 1 ...
  RETURNING` in `tools/issues.py`. The counter is on the *team*, not the project: `ENG-41` is the
  forty-first issue team `ENG` ever had, whichever project it is in. Anything that writes issues
  without that statement — a bulk load in a fixture generator — has to leave the counter where the
  tools would have.
- **The viewer.** `world.instance(fixture, user_id=...)` (`reset(user_id=...)` over OpenEnv) names
  who the session is driving the tracker as; `startup.py` puts it in `ctx.state["viewer_id"]` and
  falls back to the workspace's first admin. Every write takes an optional `actor_id`, which wins,
  and a write with neither is `InvalidInput("actor_id", "no actor")` — which is what a blank
  instance and `empty` do, having no admin to fall back to.
- **Order within one episode.** An instance's clock does not move, so every row a single episode
  writes carries the same `created_at`, and the timestamp orders none of them. Two places feel it.
  Reading `issue_events` in the order things happened means `ORDER BY created_at, rowid` — the
  timestamp for a fixture's history, SQLite's insertion order for what the episode itself did;
  `ORDER BY created_at` alone is not wrong, it is simply not an order. `list_comments` cannot do
  that — a keyset cursor carries its tiebreaker as a value and `rowid` is not a projected column —
  so it orders by `(created_at, id)` and says so: oldest first across a fixture's history, id order
  among the comments one episode wrote. Do not write an eval that grades on the order of comments an
  agent added; grade on the rows.
- **The closed-issue rule.** A `done` or `canceled` issue has no assignee: transitioning to one
  drops the assignee and records the drop, and assigning a closed issue is a `CONFLICT`. An eval may
  rely on `status IN ('done','canceled') AND assignee_id IS NOT NULL` being a state this world
  cannot reach.
- **Archiving** stamps `issues.archived_at` and takes the issue out of `list_issues`. It stays in
  the database and inside `get_issue` and `search_issues`, and no field of it changes again: all
  four tools that write to an issue — `update_issue`, `assign_issue`, `transition_issue` and
  `set_issue_labels` — refuse it with a `CONFLICT`, through `_rows.require_open_issue`.
  `add_comment` is the deliberate exception: archiving freezes the issue, not the conversation
  about it, so a comment and the `issue_events` row recording it still land on an archived issue.
  "Read-only" is therefore the wrong word for it and this file does not use it.
- **Errors.** Declare shapes in `errors.py` as `seahaven.ToolError` subclasses with
  `SCREAMING_SNAKE` codes, and raise them by name. Nothing else may reach an agent: the error
  handler in `middleware/error_handler.py` restates framework errors in those shapes, and the two
  doors that are allowed to show engine text (`run_sql`, `search_issues`) are named there. A tool
  that lets SQLite refuse a write it could have refused itself — a missing parent, a duplicate
  unique key — turns a sentence an agent can act on into `INTERNAL`, so every such case is checked
  with a `SELECT` first.
- **Tests** use Seahaven's pytest plugin: `@pytest.mark.seahaven(fixture="small_startup")` over the
  `instance` fixture, or `fixture=None` with `now=BLANK_NOW` for an instance with no fixture behind
  it. Nothing here builds an instance in a `conftest.py`, and nothing calls a tool function
  directly: a tool is tested through `instance.call`.
- **Fixtures** are frozen from a blank instance at `2026-06-01T09:00:00.000Z` by
  `fixtures_src/generate.py`, which is committed and is the only way a fixture here is made. Never
  edit a fixture in place: fork it, change it, freeze it. A schema change invalidates all three —
  delete them and re-run the generator, and commit the new bytes.
