# The database, the schema and fixtures

A world stores its data in SQLite. This page explains that in three steps: the database an instance
runs on, the schema that shapes it, and the fixtures that hold the starting states an eval uses.

| Section | What it covers |
|---|---|
| [The database](#the-database) | What an instance actually runs on, and how world code reaches it |
| [The schema](#the-schema) | Where the tables are declared, the three rules, and the schema hash |
| [What a fixture is](#what-a-fixture-is) | The directory, the sidecar file, and the rules that keep a fixture trustworthy |
| [Building a fixture](#building-a-fixture) | Blank instances, bulk loading, and the commands |
| [The generator is committed](#the-generator-is-committed) | Why the script that builds a fixture ships beside it |
| [Writing a description](#writing-a-description) | The one field written for a person |
| [When the schema changes](#when-the-schema-changes) | Regenerating every fixture, and why nothing migrates |
| [Where fixtures live](#where-fixtures-live) | The deployment shape, and what breaks if you install a world |
| [What `seahaven check` verifies](#what-seahaven-check-verifies) | The six fixture rules |

## The database

Every instance is one SQLite database file of its own, copied from a fixture and thrown away at the
end of the run.

Seahaven stores a world in SQLite because a whole database is one file. Copying that file is how a
run gets its own private starting state, and it takes milliseconds. A file also has a checksum, so
Seahaven can tell whether a fixture has changed since it was frozen. And one connection can read
every table, so an eval can grade a run with a single query.

World code reaches that database through `ctx.db`:

| Call | What it does |
|---|---|
| `ctx.db.one(sql, *params)` | the first row as a `dict`, or `None` |
| `ctx.db.rows(sql, *params)` | every row, each a `dict` keyed by column name |
| `ctx.db.execute(sql, *params)` | run a statement for effect |
| `ctx.db.executemany(sql, rows)` | run one statement once per row of bindings |
| `ctx.db.transaction()` | a nested transaction, as a context manager |
| `ctx.db.conn` | the raw APSW connection, for what the wrapper does not cover |

Parameters are positional `?` bindings. Every SQLite failure arrives as `seahaven.DbError`, so world
code catches one type.

Seahaven owns the connection setup: write-ahead logging, foreign keys on, SQLite's defensive mode,
and the overrides that make the clock and `random()` read the instance's own values. World code
never sets a pragma. Do not close `ctx.db.conn`, change its pragmas or its authorizer, or open a
second connection to the instance's file. The clock functions, the change log's per-call session
and the per-call transaction all depend on that one connection.

To read an instance without going through a tool — in a test, or when grading a run — use
`inst.inspect()`. It is a read-only handle on a second connection, with the same `one` and `rows`,
over every table.

## The schema

A **schema** is the set of tables and columns a world stores its data in. You write it as ordinary
SQLite `CREATE TABLE` statements in `.sql` files under `schema/`, inside the package. Seahaven
applies them to a blank database in filename order, which is what the `001_`, `002_` prefixes are
for.

```sql
CREATE TABLE notes (
    id TEXT PRIMARY KEY,
    body TEXT NOT NULL,
    -- Written by the tool from ctx.clock.iso(), never by a default in this file.
    created_at TEXT NOT NULL
) STRICT;
```

`seahaven.sql_files(__package__, "schema")` reads those files into the one string
`World(schema=...)` takes. It reads them through `importlib.resources`, so a world behaves the same
from a checkout, an installed wheel or a zip.

There is no Seahaven schema language, and there are no migrations. A world whose SQL does not
execute cannot be built at all: `World.__init__` applies the schema to an in-memory database to
compute its hash, and reports SQLite's own message when that fails.

A schema file may also seed static reference rows — currencies, plans, a lookup table the product
ships with — and they are part of the schema in the sense that matters: every instance of the world
starts with them. The connection those `INSERT`s run on carries the instance's clock and its seed,
so a seeded row built from `randomblob()` or `CURRENT_TIMESTAMP` replays like anything else a world
writes. The wall-clock rule below is unchanged and still refuses `CURRENT_TIMESTAMP` inside a
`CREATE` — a column default, a trigger body — so that one timestamp format is written everywhere;
what is sanctioned here is the `INSERT` the file runs itself. Rows that belong to one *scenario* are
a fixture's job, not the schema's.

### Three rules

`seahaven check` enforces these, and each one exists for a failure that is otherwise silent.

**Every table is `STRICT`.** Without it, SQLite stores whatever it is handed: a string in an
`INTEGER` column, a float in a `TEXT` one. An eval that grades on a column's value needs the column
to hold what the schema says. A `STRICT` table's columns must each be declared `INT`, `INTEGER`,
`REAL`, `TEXT`, `BLOB` or `ANY`, so a column written `VARCHAR(64)` becomes `TEXT`.

**Every table has an explicit primary key.** SQLite's implicit `rowid` does not count. A row with no
key cannot be identified in a change-log record, so writes to a table without one are invisible to
the eval grading the run. A join table takes a composite key: `PRIMARY KEY (issue_id, label_id)`.

**No expression reads the wall clock.** No `CURRENT_TIMESTAMP` default, no `datetime('now')` in a
trigger, and nothing like them in a view, a generated column or a partial index. The problem is the
text as much as the instant: SQLite writes `2026-06-01 09:00:00`, and the rest of the world writes
`2026-06-01T09:00:00.000Z`. Two formats in one column make a comparison fail for a reason nobody
finds quickly. Declare the column `NOT NULL` with no default, and pass the value in from
`ctx.clock.iso()`.

FTS5 virtual tables and their shadow tables are exempt from the first two rules, because a virtual
table cannot be `STRICT` and does not declare a primary key. [authoring.md](authoring.md) covers
full-text search.

### Timestamps

**Timestamps are `TEXT`**, in ISO 8601 UTC with milliseconds and a trailing `Z`:
`2026-06-01T09:00:00.000Z`. Every door of the world writes that format, so text comparison sorts
correctly. Presenting something friendlier to the agent, such as "3 days ago", belongs in the tool
that returns the row.

### The schema hash

Seahaven normalises the schema and hashes it. That hash is `world.schema_hash`, and it is recorded
in every fixture the world freezes. When you make an instance from a fixture, Seahaven compares the
two. A difference means the fixture was frozen from a different set of tables than the world now
declares, so the instance is refused rather than run against data that no longer fits.

That check is the reason a schema change costs every fixture. See
[When the schema changes](#when-the-schema-changes).

## What a fixture is

A **fixture** is a frozen starting state: an immutable directory holding a SQLite file and a sidecar
file describing it. Every instance is a copy of one.

```
fixtures/
  small_startup/
    state.sqlite      # checkpointed, vacuumed, sealed read-only
    fixture.yaml      # where this state came from, and what is in it
```

```yaml
created_at: '2026-09-13T08:11:06.580Z'
description: "A three-person startup's tracker: one engineering team, two active projects, ..."
file_sha256: c721f2d535d64ef52587b15664404294464c73d6b2ab6c1b54688e500b6a6257
format_version: 1
id: small_startup
now: '2026-06-01T09:00:00.000Z'
parent_id: null
schema_hash: eb3bc8f8a9033c1b633508c943d460c8eda52a2baa56b394120e3c7d8cbe33a9
world: projecttracker
world_version: 1.0.0
```

| Field | What it says |
|---|---|
| `id` | the fixture's name, and the directory it sits in |
| `world`, `world_version` | the world it was frozen from |
| `schema_hash` | the schema it conforms to |
| `now` | the instant every instance of this fixture is frozen at |
| `parent_id` | the fixture it was forked from, or `null` |
| `file_sha256` | the checksum of `state.sqlite`, verified before the first copy |
| `created_at` | real wall-clock time, and one of only two wall-clock reads a world makes |
| `description` | what is in the data, written for whoever is choosing between fixtures |

A world that **adds other worlds** freezes one state file per store into that same directory, under
one sidecar at `format_version: 2` with a `nodes` list describing them. Everything on this page
holds for it; the extra rules are in [composition.md](composition.md).

### The rules

**A fixture is never opened, only copied.** The first time a process copies a fixture it verifies
`file_sha256` and refuses on a mismatch, naming the fixture.

**A fixture's files are the files in its own directory.** A state file that is a symbolic link is
refused by name before it is hashed, and `seahaven check` reports it as `SH402`. That covers the
root's `state.sqlite` and every added node's state file alike. Without the rule, everything that
reads a fixture would follow the link, the checksum included, so a link planted here would leave
every check green while the instance ran on a database the fixture does not contain. Copy the file
in, or freeze the fixture again.

**Freezing is the only way to make one.** `inst.freeze(id, description)` checks the instance's
schema against the world's, checkpoints, vacuums, copies the file into `fixtures/<id>/`, writes the
sidecar with the instance's clock as `now` and the source fixture as `parent_id`, and seals the file
read-only. It refuses if the directory already exists.

**There is no in-place edit.** To change a fixture, fork it: make an instance from the parent,
change it, and freeze the result under a new id.

**A fixture is named by its id** everywhere: `world.instance("agency")`, `reset(fixture="agency")`,
`@pytest.mark.seahaven(fixture="agency")`, `seahaven fixture fork agency ...`. The id is also the
directory name, and it travels with the fixture, so it follows the same rule a world's name does: 1
to 128 characters of letters, digits, space, `.`, `-` and `_`, with no leading or trailing space or
dot, and no Windows device name. Anything else is refused with `not a fixture id`. A fixture frozen
under an id this rule now refuses still appears in `world.fixtures()` but no longer opens. Rename
its directory and its sidecar's `id` to reach it again.

## Building a fixture

Start from a **blank instance**: `world.instance()` with no fixture builds an empty database from
the schema alone. Its clock is the wall time at creation unless `now=` says otherwise, and freezing
bakes that value in for good.

Fill it in one of two ways. Use the world's own tools when the point is that the data is reachable
the way an agent would have made it. Use `inst.bulk()` when you are loading thousands of rows and a
tool call per row would spend its time on argument validation. `bulk()` yields the root node's
context, in one transaction per node, under the instance lock, with no call attached. Startup hooks
do not run again.

```python
from pathlib import Path

import seahaven

world = seahaven.World(
    name="notes",
    version="1.0.0",
    schema="CREATE TABLE notes (id TEXT PRIMARY KEY, body TEXT NOT NULL) STRICT;",
    fixtures_dir=Path("fixtures"),
    state_format="seahaven.state/1",
)

with world.instance(now="2026-06-01T09:00:00.000Z") as inst:
    with inst.bulk() as ctx:
        ctx.db.executemany(
            "INSERT INTO notes (id, body) VALUES (?, ?)",
            [(f"n{number}", f"note {number}") for number in range(1000)],
        )
    fixture = inst.freeze("seeded", "A thousand notes. Good for list and pagination scenarios.")

assert fixture.now == "2026-06-01T09:00:00.000Z"
assert fixture.parent_id is None

with world.instance("seeded") as inst:
    assert inst.inspect().one("SELECT count(*) AS n FROM notes") == {"n": 1000}
    # A fresh instance of a fixture has changed nothing yet.
    assert inst.change_log() == []
```

`freeze` cannot run inside a `bulk()` block, because the rows are not committed yet. Leave the block
first.

## The generator is committed

**Commit every fixture together with the script that generates it.** A binary file with no source is
one nobody can change, and a schema change means rebuilding all of them, which has to be a command
rather than a project.

`seahaven new` puts that script at `fixtures_src/generate.py`: one function per fixture, each taking
a live instance and filling it, which is the shape the CLI calls.

```sh
seahaven fixture freeze empty \
    --now 2026-06-01T09:00:00.000Z \
    --run fixtures_src.generate:empty \
    --description "The schema with no rows. Start here to write a history."

seahaven fixture fork empty startup_with_history \
    --run fixtures_src.generate:startup_with_history \
    --description "The empty tracker, plus one team and two months of issues."

seahaven fixture list
```

**Pass `--now` on every `freeze`, and pass the same value every time.** `freeze` starts from a blank
instance, and a blank instance with no `--now` takes the wall clock, so leaving the flag off dates
each fixture from the day it was built. Two fixtures of one world then sit at two different
instants, and `seahaven check` cannot warn you, because a wall-clock instant is a perfectly valid
timestamp. Choose the instant once, write it in the generator's docstring, and never move it.

`fork` needs no `--now`. A fork inherits its parent's clock, which is the point of forking.

The scaffold's `build(fixture_id, *, world=...)` is the same freeze with `--now` and the description
filled in for you. `world=` is there for tests: a fixture is frozen into `world.fixtures_dir`, so a
test that rebuilds one to compare it against the committed bytes passes in `copy.copy(world)`
pointed at a temporary directory. Moving the imported world's `fixtures_dir` instead would change it
for every other caller in the process.

## Writing a description

The `description` is the one field written for a person, or for an agent, choosing between fixtures.
It is what `seahaven fixture list` prints. Say what the data contains and what scenarios it
supports:

> A twelve-person agency: three teams, nine projects including a finished one, six hundred issues
> with a six-month history, comments, labels and an audit trail. Good for cross-team queries,
> reporting, bulk operations and search.

A description like "the big one" tells the next person nothing.

Most worlds want an `empty` fixture holding the schema and no rows. It is the starting point for
setup-flow evals and the obvious thing to fork from, and it makes the first fixture something the
generator built like every other. ProjectTracker ships one, although all three of its fixtures are
frozen from blank rather than forked: a generator that fills a blank instance from scratch is
simpler to read than a chain, and forking is for when the shared history is expensive to rebuild.

## When the schema changes

The schema hash is in every sidecar. Making an instance from a fixture whose hash differs from the
loaded world fails with a message saying the fixture needs regenerating, and `seahaven check`
reports the same thing as `SH403` before you get that far.

So: change the schema, delete every fixture directory, re-run the generator for each one in parent
order, and commit the new bytes. Nothing migrates a fixture that has already shipped. Version 1 of
Seahaven has no migration path at all, which is why the generator is committed.

An extension that brings its own `CREATE TABLE` text (see [extensions.md](extensions.md)) changes
the schema hash too. That is a real cost of using one, and the extensions page says so.

## Where fixtures live

`fixtures/` sits at the project root, beside `src/`. Seahaven finds it by walking up from the module
that constructed the `World` to the directory holding `pyproject.toml`. `World(fixtures_dir=...)`
names another directory outright.

**A world is checked out, not installed.** That is the supported deployment shape, and the only one.
The unit you deploy is the world's whole directory — `pyproject.toml`, `src/`, `fixtures/` — which
you clone, mount into a container, or `COPY` into an image, with the framework installed into that
checkout's environment. `pip install <world>` is not a way to deploy a world, and Seahaven has no
path that makes it one.

The reason is that `fixtures/` sits **outside** the package on purpose. It is data the world ships
rather than code it imports: the generator writes it, `freeze` adds to it, `seahaven check` hashes
it off the filesystem, and a `git diff` on a frozen fixture should show a directory of bytes rather
than a package resource. A wheel holds the package and nothing above it, so a built wheel carries
the schema — `schema/*.sql` is inside the package and is read through `importlib.resources` — and
carries no fixtures at all.

The consequence is sharp, and it stays quiet until an eval runs:

- `world.instance()` — **blank**, built from the schema — works from an install. Nothing about a
  blank instance touches the fixtures directory.
- `world.instance("empty")`, and every other **fixture-backed** instance, raises a `WorldBug` naming
  the directory it looked in and the two ways out. `world.fixtures()` is `[]`, and `seahaven fixture
  list` prints nothing.

  ```
  WorldBug: world 'projecttracker' has no fixture 'empty' in .../site-packages/fixtures;
  freeze one, or name the directory with World(fixtures_dir=...)
  ```

So a world that is installed rather than checked out serves blank instances and fails every eval
that starts from data. Over a server that shows up as a `reset(fixture=...)` failing for an agent
that can do nothing about it.

**If you really do have to install one**, `World(fixtures_dir=...)` is the escape hatch. Put
`fixtures/` wherever the deployment puts it and name that path. It is a supported argument and it
works. What it does not do is travel in the wheel with everything else, so the deployment owns
getting the directory there — a mounted volume, an image layer, a download at startup — and owns
keeping it in step with the package's schema hash. Read
[Publishing to a hub](serving_and_openenv.md#publishing-to-a-hub) before taking this route, because
`fixtures_dir=` moves one other thing with it.

## What `seahaven check` verifies

`seahaven check` has six fixture rules, and all six are errors:

| Code | What it catches |
|---|---|
| `SH401` | a sidecar that does not validate |
| `SH402` | a state file that does not match its `file_sha256`, because it was edited after freezing or is a symbolic link |
| `SH403` | a fixture frozen from a different schema than the world declares |
| `SH404` | a `now` that is not in the canonical format |
| `SH405` | a `-wal` or `-shm` file beside the state file, so it was opened for writing after freezing |
| `SH406` | a composite fixture's sidecar whose `nodes` list disagrees with the world's composition ([composition.md](composition.md)) |

[reference/lints.md](reference/lints.md) has the fix for each.
