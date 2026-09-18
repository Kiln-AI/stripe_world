# ProjectTracker

A Seahaven world: a fictional issue tracker for a fictional company, and the reference world the
framework is developed against. Nothing here mimics a real product's names, schema or error text.

Users belong to teams, teams own projects, projects hold issues, issues carry labels and comments,
and every write to an issue appends to an audit trail. Keys are product-shaped (`ENG-41`), lists are
cursor-paginated, and search is SQLite's FTS5.

## What it has

| | |
|---|---|
| Schema | `users`, `teams`, `team_members`, `projects`, `issues`, `labels`, `issue_labels`, `comments`, `issue_events`, plus an FTS5 index over issue titles and descriptions |
| Tools | 25, one module per resource under `src/projecttracker/tools/`, plus `run_sql` and `describe_schema` from Seahaven's helpers |
| Errors | `NOT_FOUND`, `INVALID_INPUT`, `CONFLICT`, `INTERNAL` (`src/projecttracker/errors.py`) |
| Fixtures | `empty`, `small_startup`, `agency` — all frozen at `2026-06-01T09:00:00.000Z` |

### The tools

| Module | Tools |
|---|---|
| `users.py` | `create_user`, `get_user`, `list_users` |
| `teams.py` | `create_team`, `get_team`, `list_teams`, `add_team_member`, `list_team_members` |
| `projects.py` | `create_project`, `get_project`, `list_projects`, `update_project` |
| `labels.py` | `create_label`, `list_labels`, `set_issue_labels` |
| `issues.py` | `create_issue`, `get_issue`, `list_issues`, `update_issue`, `assign_issue`, `transition_issue`, `archive_issue` |
| `comments.py` | `add_comment`, `list_comments` |
| `search.py` | `search_issues` |

### The fixtures

| id | Contents |
|---|---|
| `empty` | The schema with no rows. Start here to write a history, or to test setup flows. |
| `small_startup` | One engineering team, three people, two projects, forty issues over two months, sixty comments, six labels. |
| `agency` | Three teams, twelve people, nine projects including a finished one, six hundred issues over six months, fifteen hundred comments, thirty labels, and an audit trail with reassignments and transitions in it. |

Every fixture is built by `fixtures_src/generate.py`, which is committed and is the only way one is
made here. Rebuild one by deleting it and running the script:

```sh
uv run python worlds/projecttracker/fixtures_src/generate.py small_startup
```

## Using it

```python
import projecttracker

with projecttracker.world.instance("small_startup") as tracker:
    tracker.tools()  # the tool list, with JSON schemas
    tracker.call("list_issues", status="todo")  # a page of issues, plus next_cursor
    tracker.call("get_issue", key="ENG-12")  # one issue, by its product key
    tracker.call("search_issues", query="billing")  # FTS5, best match first
```

The instance's *viewer* — who the tracker thinks is using it — is `user_id` on creation, and is the
workspace's first admin when that is not given:

```python
with projecttracker.world.instance("agency", user_id=someone) as tracker:
    tracker.call("add_comment", issue_id=..., body="looking at this now")
```

Tests: `uv run pytest worlds/projecttracker` from the repository root, or `uv run pytest` from here.

## Conventions

- **Timestamps** are canonical UTC text with milliseconds and a trailing `Z`
  (`2026-06-01T09:00:00.000Z`), written from `ctx.clock.iso()` and never from the wall clock.
- **Issue keys** are `TEAMKEY-n`, minted from a counter on the team: `ENG-41` is the forty-first
  issue team `ENG` ever had, in whichever of its projects.
- **A closed issue has no assignee.** Moving an issue to `done` or `canceled` drops its assignee,
  and assigning a closed issue is refused.
- **An archived issue keeps its fields and leaves `list_issues`** — no tool will change one again,
  though `get_issue` and `search_issues` still find it. It can still be commented on: archiving
  freezes the issue, not the conversation about it.
- **Ids** come from `ctx.ids.uuid()`, so a seed replays a run exactly.
- **Lists order by a column and then by id**, which is a total order and the same on every run. The
  tracker's clock does not move, so rows written during one episode share an instant and the id is
  what separates them: `list_comments` is oldest-first across a fixture's history and id-ordered
  among comments the episode itself added.
- **Error codes** are this product's, in `SCREAMING_SNAKE`; the framework's own codes never reach an
  agent, which is the error handler's job. `run_sql` is the one door that shows SQLite's own text,
  because a SQL console's errors are SQL errors.
