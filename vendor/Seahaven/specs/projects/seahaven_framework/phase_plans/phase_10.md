---
status: complete
---

# Phase 10: ProjectTracker, in full

## Overview

Phase 5 built a placeholder: one table, one tool, the error shapes and an `empty` fixture, just
enough to be a real package for phases 6 to 9 to serve, scaffold, check and test against. This phase
replaces it with the world `components/projecttracker.md` specifies — nine tables and an FTS5
index,
twenty-five tools plus the two helpers, three fixtures and the generator that makes them, and a test
suite on the pytest plugin.

The implementation plan calls this "the first real consumer of everything before it; API friction
found here is fixed in the framework, not worked around in the world". So the phase has a second
job beside the world: anything the framework makes awkward is a framework change, recorded here.

### What the world is

A fictional Linear/Jira-shaped issue tracker. Users belong to teams, teams own projects, projects
hold issues, issues carry labels and comments, and every write to an issue appends a row to an
audit trail. Keys are product-shaped (`ENG-41`), lists are keyset-paginated, search is FTS5, and
every error an agent can see is one of this product's four shapes.

### Deviations from the component document

**1. `issue_counter` lives on `teams`, not on `projects`.** §1 lists the column on `projects`; §3
says the key is "`TEAMKEY-n` with a per-team counter", and §5 asks for a test that mints `ENG-41`
after the forty issues of `small_startup`, which are spread over two projects of one team. A
per-project counter cannot produce either: two projects of team `ENG` would both mint `ENG-1`, and
`issues.key` is `UNIQUE`, so the second `create_issue` in the second project would fail outright.
Two statements and a correctness argument against one column list: the counter goes on `teams`. The
DDL says so in a comment.

**2. The cursor's third element names the resource as well as the ordering.** §3 says the cursor is
base64 JSON `[sort_value, id, sort_key]` "refused when cut for another ordering". The sort keys here
are `issues:created_at_desc`, `users:created_at_asc` and so on, so a cursor cut by `list_users` is
also refused by `list_issues`. That is strictly more refusal than the sentence asks for and no less,
and it closes the case where two resources share an ordering and a foreign cursor would silently
return a wrong page instead of an error.

**3. `ping` is removed.** It is the placeholder's one tool and is not among the twenty-five. The
tests that drove it (`test_ping.py`, and the OpenEnv and fixture tests that called it) drive
`list_users`, `create_user` and `get_issue` instead.

**4. The startup hook falls back to the workspace's admin.** §3 says the hook stores
`reset(user_id=...)` in `ctx.state["viewer_id"]` and that a write with neither an `actor_id` nor a
viewer raises `InvalidInput("actor_id", "no actor")`. §4 also says of `small_startup` and `agency`
that "viewer is the admin", which cannot be a property of the fixture's bytes: `ctx.state` is not
persisted. It is a property of the hook, so the hook resolves the viewer to the earliest-created
`admin` when `reset` named no `user_id`. `empty` has no users, so the "no actor" error stays
reachable, and an explicit `user_id` always wins.

## Steps

### 1. Schema (`src/projecttracker/schema/`)

`001_core.sql` replaces the placeholder's `users`-only file with the nine tables, all `STRICT`, all
timestamps canonical text with no DDL defaults:

- `users(id PK, email UNIQUE, name, role CHECK admin|member|viewer, created_at)`
- `teams(id PK, key UNIQUE CHECK 2-5 upper letters, name, issue_counter INTEGER NOT NULL DEFAULT 0,
  created_at)`
- `team_members(team_id, user_id, joined_at, PRIMARY KEY(team_id, user_id))`, both columns FK
- `projects(id PK, team_id FK, name, state CHECK planned|active|done, created_at)`
- `issues(id PK, project_id FK, key UNIQUE, title, description, status CHECK
  backlog|todo|in_progress|done|canceled, priority INTEGER CHECK 0..4, assignee_id FK NULL,
  creator_id FK, created_at, updated_at, due_at NULL, archived_at NULL)` and five indexes:
  `project_id`, `status`, `assignee_id`, `created_at`, `updated_at`
- `labels(id PK, team_id FK, name, color CHECK `#rrggbb`, UNIQUE(team_id, name))`
- `issue_labels(issue_id, label_id, PRIMARY KEY(issue_id, label_id))`
- `comments(id PK, issue_id FK, author_id FK, body, created_at)`
- `issue_events(id PK, issue_id FK, actor_id FK, kind CHECK created|status|assignee|comment,
  payload TEXT CHECK json_valid, created_at)`

`002_search.sql`: `issues_fts` as FTS5 external content over `issues(title, description)` with
`content='issues'`, plus `issues_fts_after_insert`, `_after_delete` and `_after_update`. No trigger
reads a clock.

### 2. `startup.py`

```python
@world.instance_startup
def remember_viewer(ctx: seahaven.Ctx, user_id: str | None = None) -> None: ...
```

Stores the viewer in `ctx.state["viewer_id"]`; an explicit `user_id` that names nobody is a
`WorldBug`, like an unknown fixture id, because it is the eval's mistake and not the agent's.

### 3. Shared tool modules (`tools/_*.py`)

- `_types.py`: `IssueStatus`, `Priority`, `Role`, `ProjectState`, `IssueOrder` as `Literal`s;
  `Timestamp`, `Limit`, `Cursor`; `TABLES`, the nine table names the helpers are registered over;
  `CLOSED_STATUSES`.
- `_pagination.py`: `Order(key, column, id_column, descending)`, `encode_cursor`, `decode_cursor`
  (raising `InvalidInput("cursor", ...)`), and `page(ctx, select, where, params, order, limit,
  cursor)` returning `{items, next_cursor, has_next}` by keyset on the SQL row value
  `(<column>, <id column>)`.
- `_rows.py`: the column lists, one projection per resource, `require_user/team/project/issue/label`
  lookups raising `NotFound`, `resolve_actor`, and `attach_labels`, which fills `label_ids` for a
  page of issues in one query rather than one per row.
- `_events.py`: `record_event(ctx, issue_id, actor_id, kind, payload)`, the one place an
  `issue_events` row is written.

### 4. The twenty-five tools

One module per resource, `ctx` first, typed keyword parameters, every id from `ctx.ids.uuid()` and
every timestamp from `ctx.clock.iso()`.

- `users.py`: `create_user`, `get_user`, `list_users`
- `teams.py`: `create_team`, `get_team` (by key), `list_teams`, `add_team_member`,
  `list_team_members`
- `projects.py`: `create_project`, `get_project`, `list_projects`, `update_project`
- `labels.py`: `create_label`, `list_labels`, `set_issue_labels`
- `issues.py`: `create_issue`, `get_issue`, `list_issues`, `update_issue`, `assign_issue`,
  `transition_issue`, `archive_issue`
- `comments.py`: `add_comment`, `list_comments`
- `search.py`: `search_issues`

Product rules the tools enforce, each raising one of the four declared shapes:

- a duplicate email, team key or label name is a `Conflict`, checked before the insert so a `UNIQUE`
  violation never becomes `INTERNAL`;
- a referenced record that does not exist is a `NotFound`, checked before the insert for the same
  reason;
- `get_issue` takes exactly one of `issue_id` and `key`;
- a `done` or `canceled` issue has no assignee: transitioning to one clears it (and records the
  clearing), and assigning to a closed issue is a `Conflict`;
- `archive_issue` stamps `archived_at` and is a `Conflict` the second time; archived issues are
  outside `list_issues`;
- `key` is minted `UPDATE teams SET issue_counter = issue_counter + 1 ... RETURNING`;
- every write resolves its actor through `resolve_actor`, which is `actor_id`, else the viewer, else
  `InvalidInput("actor_id", "no actor")`.

`tools/__init__.py` imports the seven modules and registers `run_sql(tables=TABLES)` and
`describe_schema(tables=TABLES)`.

### 5. Fixtures and the generator

`fixtures_src/generate.py` grows a `Workspace` spec and one `_populate` that builds any of them from
`ctx.ids.random` and `ctx.clock`:

| id | content |
|---|---|
| `empty` | schema only |
| `small_startup` | 1 team `ENG`, 3 users, 2 projects, 40 issues over 60 days, 60 comments, 6 labels |
| `agency` | 3 teams, 12 users, 9 projects (one `done`), 600 issues over 180 days, 1,500 comments, 30 labels, 20% reassigned, 25% transitioned |

Rows go in through `inst.bulk()`; the last few issues, comments and transitions of a populated
fixture go through `inst.call(...)`, so both write paths are exercised and the team counter is left
where the tools put it. `_assert_timespan` checks every timestamp against the span before `now`,
`due_at` excepted (up to 45 days after).

### 6. Documents

`README.md` and `AGENTS.md` lose the "placeholder slice" framing and gain the world's notes: the key
format, the timestamp format, the viewer rule and the closed-issue rule.

## Tests

`worlds/projecttracker/tests/`, all on the pytest plugin, marker per test.

- `test_users.py`: create/get/list happy paths; a bad email; a duplicate email; an unknown user;
  the `role` filter; the created row's shape.
- `test_teams.py`: create/get by key/list; a bad key; a duplicate key; membership; a member of a
  team that does not exist; the member list's order.
- `test_projects.py`: create under a team; an unknown team; get; list filtered by team and by state;
  update name and state; update nothing; an unknown project.
- `test_labels.py`: create; a bad colour; a duplicate name in one team and the same name in another;
  list by team; `set_issue_labels` replaces the whole set, refuses an unknown label, and empties.
- `test_issues.py`: create mints `ENG-1` and records a `created` event; `get_issue` by id and by
  key, and the exactly-one rule; update stamps `updated_at`; assign, transition, the closed-issue
  rule both ways; archive stamps and is excluded from lists and refuses twice; the filters; the
  `order` argument; `actor_id` and the viewer and neither.
- `test_comments.py`: add appends a comment and a `comment` event; list is oldest first; an unknown
  issue.
- `test_pagination.py`: three pages over a stable order with no repeats and no gaps; `has_next` and
  `next_cursor` agree; a cursor cut for another ordering is refused; a malformed cursor is refused;
  `limit` bounds.
- `test_search.py`: a phrase, a prefix and a column-scoped query; a syntax error is `INVALID_INPUT`;
  ranking ties broken by id; the snippet and score fields.
- `test_sql_tools.py`: `run_sql` reads all nine tables, refuses a write, returns SQLite's own
  message on a typo, and refuses a table it was not given; `describe_schema` lists the nine tables
  with their keys and foreign keys.
- `test_fixtures.py`: the three descriptions; the counts per fixture; the span assertion; the total
  size under 10 MB; regenerate and compare content, and bytes on the same SQLite build (slow).
- `test_determinism.py`: one seed replays ids and the changeset over a scripted sequence; another
  seed differs.
- `test_errors.py`: no SQLite text escapes any tool but `run_sql`; every error an agent can provoke
  is one of the four codes.
- `test_openenv.py` (the existing `test_openenv_app.py`, rewritten): reset with `user_id`, list,
  call, error and state over the wire with both clients.
- The existing `test_package.py`, `test_declared_errors.py`, `test_error_handler.py` and
  `test_empty_fixture.py` are updated for the new registry and the removal of `ping`.

Plus `uv run seahaven check` clean on the finished world, the framework's own suite unchanged, and
the XML-RPC extension's 75 tests still passing over the grown schema.

## What the code review changed

Three rounds. All three deviations above were judged independently and stand; round 3 came back
clean.

### Round 1

One Critical, four Moderates and eight Milds.

- **A malformed cursor made the world accuse itself of a bug.** `decode_cursor` validated the
  cursor's sort *key* and let its two value halves through as whatever JSON held. A JSON object or
  array there is bound as a SQL parameter, APSW raises `TypeError`, and the error handler's last
  branch turns that into `INTERNAL` — which in this world means "a bug in world code" and is logged
  with a traceback. So an agent editing the opaque string it had been handed could make the world
  blame itself, once per call, from a string it was invited to carry. Both halves are now checked.
- **`set_issue_labels` wrote to archived issues.** It called `require_issue` where the other three
  mutating tools called the archived check — which lived in `issues.py`, one module away from the
  fourth tool that needed it. The check moved to `_rows.require_open_issue`, where the tool that is
  not in `issues.py` can reach it, and the parametrised test now names all four.
- **A label could be attached across a team boundary.** `set_issue_labels` checked each label
  existed but not that it belonged to the issue's team, so `list_issues(label_ids=...)` would answer
  across the boundary the product model is explicit about — while the fixture generator was careful
  to hold it, which is the worst combination: an invariant visible in the data and absent from the
  tools.
- **A third of `agency`'s rows referenced people and projects that did not exist yet.** The span
  check bounded the global window and never compared a child's stamp to its parent's, so 177 of 600
  issues were filed by people who had not joined and 89 comments predated their author. Every row is
  now dated after what it refers to, and `_assert_within_span` gained parent-child clauses the
  generator asserts while building and a test re-asserts of the committed bytes.
- The Milds taken: `ensure_ascii=False` on both `json.dumps` calls; all nine table counts pinned per
  fixture rather than six; `ISSUE_FIELDS` as the one source for the plain and the table-qualified
  column lists, replacing string surgery in `search.py`; `update_project`'s empty-update error
  naming `project_id` as `update_issue`'s does; two stale comments; and, from Mild 3, overdue due
  dates and a trail whose actor is not always the filer — both of which `agency` is sold on and
  neither of which it had.
- Declined, with the reason recorded: archiving issues inside the fixtures. `components/
  projecttracker.md` §4 fixes each fixture's contents *and* its description text, and neither
  mentions archived issues; adding them would make "six hundred issues" quietly untrue of
  `list_issues`, and editing the description is a deviation with no correctness argument behind it.
- Two pieces of framework friction were found rather than worked around and are recorded as
  `BACKLOG.md` B18 (a world has no way to order rows by when they were written inside one episode)
  and B19 (a fixture generator cannot be pointed at a world, so testing one means monkeypatching a
  singleton).

### Round 2

The regenerated fixtures verified clean on every temporal clause. The Critical was still open for
one value shape, and a new Moderate appeared behind the round-1 fix.

- **`_bindable` checked the type and not the range.** APSW binds 64-bit integers; JSON has no
  integer bound, so `2 ** 80` in a cursor half decoded happily and raised `OverflowError` — the same
  reachability and the same `INTERNAL`-with-a-traceback outcome as the container case. The predicate
  is now exactly APSW's bound. A new test asserts the property the sweep could never have caught:
  `INTERNAL` is one of the four declared codes, so what matters is not the code but that **no call
  an agent can provoke writes to the author's log**, and nothing had asserted that.
- **The documents overstated archiving.** `add_comment` works on an archived issue and appends to
  its trail, deliberately — so "read-only", which `AGENTS.md` and `README.md` both said, was the
  wrong word. This is round 1's Moderate pointing the other way, and the round-1 fix is what made
  the divergence deliberate. Both documents now state the rule with its carve-out, and `AGENTS.md`
  says why it does not use the term.
- **`agency`'s people reached across teams.** The generator had just been taught to hold the team
  boundary for labels and did not hold it for people: 287 of 600 issues were filed by non-members of
  the owning team, which is about half noise in the "how much did ENG file" query the fixture exists
  to support. A `_Workforce` value now carries people, join dates and membership together and is the
  one pool every row draws from; four membership clauses joined the parent-child check.
- The ordering decision (below) stands, but its stated reasoning was weaker than the real case and
  was rewritten: the `issue_events` symmetry argument is wrong, since that table has no list tool
  and `ORDER BY created_at, rowid` serves the only reader it has.

### Round 3

Clean. The verification measured `_bindable`'s predicate against APSW on this build and confirmed it
is APSW's bound and not off by one in either direction; reverted `_bindable` in the working tree to
prove both new tests fail without it; ran all seventeen parent-child clauses directly against the
three committed `state.sqlite` files; reconciled the event totals arithmetically; and confirmed two
invariants beyond the seventeen — each team's `issue_counter` equals both its issue count and its
highest minted key, and no closed issue anywhere carries an assignee.

## The judgement calls, and why

Two things were decided rather than fixed, and both were reviewed and upheld.

**`list_comments` says its order rather than gaining a sequence column.** An instance's clock does
not move, so every row one episode writes carries one `created_at` and the keyset's id tiebreaker is
what orders them: stable across runs, and not the order they were written in. A monotonic column on
`comments` would carry in a cursor where `rowid` cannot, and was not taken, because the cause is the
framework's frozen clock rather than anything this table does — so the fix belongs where the cause
is, as `BACKLOG.md` B18 — because `components/projecttracker.md` is `status: complete` and its §1
spells this table's columns, and because saying it costs nothing: `seahaven.tool` publishes a tool's
whole docstring as its description, so the caveat is in the tool list the agent reads.

**The fixtures hold no archived issues**, for the reason under round 1.
