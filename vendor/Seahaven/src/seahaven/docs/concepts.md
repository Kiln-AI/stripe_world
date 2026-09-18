# Concepts

Seahaven uses nine words over and over. This page defines them once. Every other page then uses them
without explaining them again, so read this page first.

| Word | In one line |
|---|---|
| [World](#world) | The Python package that declares a synthetic product |
| [Tool](#tool) | A Python function the agent can call |
| [Schema](#schema) | The tables the world stores its data in |
| [Fixture](#fixture) | A frozen database an eval starts from |
| [Instance](#instance) | A private copy of a fixture, for one run |
| [Context](#context) | The `ctx` object every tool receives |
| [Clock](#clock) | The instance's frozen point in time |
| [Reproducibility](#reproducibility) | Why the same run replays the same way |
| [The change log](#the-change-log) | What the agent changed, call by call |

## World

A **world** is a Python package that exposes one `seahaven.World` object, by convention as the
attribute `world` on the package. The object holds the world's name, its version, its schema, and
everything registered against it: tools, middleware and instance startup hooks.

```py
# src/notes/world.py
import seahaven

world = seahaven.World(
    name="notes",
    version="1.0.0",
    schema=seahaven.sql_files(__package__, "schema"),
    state_format="seahaven.state/1",
)
```

The agent sees exactly the tools the world registers, and nothing else. There is no second way in.

Build the `World` at import time, in a module of its own, so that every tool module can import it
without an import cycle. Registration is validated straight away, so a mistake in a tool's signature
raises at import and names the tool and the parameter. You do not find out on the first call.

Everything about a world is written in Python. There is no `world.yaml` and no `[tool.seahaven]`
table.

## Tool

A **tool** is a synchronous Python function whose first parameter is the instance context. The rest
of its parameters are the tool's arguments. Seahaven builds a pydantic model and a JSON schema from
that signature, and the whole docstring becomes the description an agent reads.

```py
@world.tool
def get_issue(ctx: seahaven.Ctx, key: str) -> dict[str, object]:
    """Fetch one issue by its product key, such as ENG-12."""
    ...
```

Arguments are validated **strictly** before the tool runs. `"5"` is not an `int`, and `1` is not a
`bool`. An argument the tool does not declare is refused. Every violation is reported at once, so an
agent can fix all of them in one turn instead of one per round trip.

By default one call is one transaction. Seahaven begins it before the tool runs, commits it when the
tool returns, and rolls it back if the tool raises. [authoring.md](authoring.md) covers tools in
full.

## Schema

A **schema** is the set of tables and columns a world stores its data in. You write it as ordinary
SQLite `CREATE TABLE` statements, in `.sql` files under `schema/`, and Seahaven applies them in
filename order to a blank database. `seahaven.sql_files(__package__, "schema")` reads those files
into the one string `World(schema=...)` takes.

Three rules apply to every table, and `seahaven check` enforces all three:

- every table is `STRICT`, so SQLite stores what the column says rather than whatever it was handed;
- every table has an explicit primary key, because a row with no key cannot be identified in the
  change log;
- no expression anywhere reads the wall clock — no `CURRENT_TIMESTAMP` default, and no
  `datetime('now')` in a trigger. World code writes timestamps, from the instance's clock, in one
  format.

**Timestamps are `TEXT`**, in ISO 8601 UTC with milliseconds and a trailing `Z`:
`2026-06-01T09:00:00.000Z`. Every part of the world writes that format. Showing the agent something
friendlier, such as "3 days ago" or a local time, is the world's job in the tool that returns it.

[db_schema_and_fixtures.md](db_schema_and_fixtures.md) is the full page on the schema and the
database under it.

## Fixture

A **fixture** is a frozen starting state: a directory holding a SQLite file and a small YAML file
that describes it. It is the data an eval starts from, such as a workspace with twelve people and
six hundred issues in it, or an empty one.

Seahaven never opens a fixture. It copies it. The only way to make a fixture is to freeze an
instance, and the YAML file records where the state came from: the world and version, the schema it
conforms to, the frozen clock, the fixture it was forked from, a checksum of the database file, and
a description written for whoever is choosing between fixtures.

You name a fixture by its id everywhere: `world.instance("agency")`, `reset(fixture="agency")`,
`@pytest.mark.seahaven(fixture="agency")`, `seahaven fixture list`.

A world that adds other worlds freezes one file per store into the same directory, under one
description file. See [db_schema_and_fixtures.md](db_schema_and_fixtures.md) and
[composition.md](composition.md).

## Instance

An **instance** is a private copy of a fixture's SQLite file, plus one connection, one clock, one id
generator and the change log it keeps. It lives for the length of one run, in a working directory
under the system temp directory, and it is what a run actually drives.

```python
import projecttracker

with projecttracker.world.instance("small_startup", seed=7) as inst:
    issue = inst.call("get_issue", key="ENG-12")
    assert issue["key"] == "ENG-12"
    assert inst.fixture == "small_startup"
    assert inst.clock.iso() == "2026-06-01T09:00:00.000Z"
```

Leaving the `with` block destroys the instance: connections closed, directory removed.
`inst.destroy()` does the same explicitly, and waits for a call in flight to finish first.

Instances are cheap, and a process may hold as many as it likes. Calls into one instance run one at
a time under that instance's lock. Calls into different instances do not wait for each other. Over a
server, one session holds one instance.

One thing does bound calls across instances, and it is on by default in **every** process, not only
under `seahaven serve`. A process-wide gate of `min(cpus, 16)` slots lets that many tool calls run
at once. Nothing is ever rejected; extra calls queue. A harness driving many instances on threads
shares those slots, and the gate is unfair whenever it binds.
`seahaven.instances.set_concurrency(n)` resizes it, and `0` removes it. See
[The concurrency gate](serving_and_openenv.md#the-concurrency-gate) for the measurements and the
known defect.

An instance of a world that **adds other worlds** is the same thing once per **store**, where a
store is one database owned by one world in the tree. Each store gets its own file, connection and
id stream, and each is recorded in the change log, under one clock, one seed and one lock. Every
sentence above still holds, which is what [composition.md](composition.md) is about.

## Context

Every tool, middleware and startup hook receives the same `ctx` object for the length of one call.
It is the whole of what world code is given. Seahaven exposes nothing else: no working directory, no
other instance, no process.

| Member | What it is |
|---|---|
| `ctx.db` | The connection: `one`, `rows`, `execute`, `executemany`, `transaction()`, and `conn` for the raw APSW connection |
| `ctx.clock` | The instance's frozen instant: `now()` for an aware UTC `datetime`, `iso()` for the canonical text |
| `ctx.ids` | The seeded stream: `uuid()` and `random`, a `random.Random` seeded per instance |
| `ctx.state` | A plain `dict` that lives as long as the instance; where a startup hook leaves what it worked out |
| `ctx.call` | The current call: `name`, `arguments`, `tool`, `node`, and `with_arguments(**changes)` |
| `ctx.instance` | `id`, `fixture` (or `None` for a blank instance) and `seed`, read-only |
| `ctx.worlds` | The worlds this one adds, by name. `ctx.worlds.<name>` is that store's tools, `db` and `state` for the length of the call. A world that adds none has no name to ask for, and every name raises `WorldBug` ([composition.md](composition.md)) |

## Clock

An instance's clock is **static**. It holds the fixture's frozen `now` for the instance's whole
life. Every connection overrides SQLite's own date and time functions to return that instant —
`CURRENT_TIMESTAMP`, `datetime('now')`, `strftime`, `julianday` and the rest — so SQL reads the same
time world code does. Nothing in the data path reads the wall clock.

A blank instance takes its clock from the wall time at creation, and `now=` overrides it. Freezing
bakes that value into the fixture, where it never moves again. Those two are the only wall-clock
reads a world makes: a blank instance's default clock, and the `created_at` stamped on a fixture
when you freeze it.

The clock is fixed *before* the blank database is built, not after, so a schema file that seeds
reference rows of its own — `INSERT INTO plans VALUES ('free', ...)` — stamps them at that instant
as well. A fixture frozen from such an instance carries the rows the caller's `now=` dated.

Design around one consequence: **every row that one run writes carries the same timestamp.** A
timestamp cannot order them. See
["Things that go wrong quietly"](authoring.md#things-that-go-wrong-quietly) in authoring.md.

## Reproducibility

Each instance derives a seed from the fixture id (or the world name, for a blank instance) and the
caller's optional `seed=`. `ctx.ids.uuid()` and `ctx.ids.random` draw from that seed, so the same
fixture, the same seed and the same calls produce the same ids.

```python
import projecttracker

world = projecttracker.world


def first_issue_id() -> str:
    with world.instance("small_startup", seed=11) as inst:
        project = inst.inspect().one("SELECT id FROM projects ORDER BY id LIMIT 1")
        return str(inst.call("create_issue", project_id=str(project["id"]), title="A")["id"])


assert first_issue_id() == first_issue_id()
```

SQL is seeded from the same place. Every connection an instance opens overrides SQLite's `random()`
and `randomblob()`, in the same way it overrides the date and time functions. A `DEFAULT
(randomblob(8))` in a world's schema replays, and so does a `SELECT random()` an agent wrote. The
connection that applies the DDL is one of them, so a `randomblob()` a schema file runs while it
builds replays too. Each connection draws from a stream of its own, derived from the instance seed
and separate from `ctx.ids`, so an agent rolling dice in SQL does not shift the identifiers world
code mints afterwards, and does not read out what an `inspect()` handle will draw.

```python
import projecttracker

world = projecttracker.world


def roll() -> int:
    with world.instance("small_startup", seed=11) as inst:
        rolled = inst.inspect().one("SELECT random() AS value")
        assert rolled is not None
        return int(rolled["value"])


assert roll() == roll()
```

**Seahaven offers reproducibility. It does not enforce it.** You get a frozen clock and a seeded
stream, and those are deterministic. A world that calls `datetime.now()` or `uuid.uuid4()` gets
exactly what it asked for, and `seahaven check` warns about both (`SH201`, `SH203`) rather than
refusing them, because a wall-clock read is occasionally deliberate. Two more things are yours to
get right. A list a world returns needs a deterministic tiebreak: order by a column *and* by the id,
or two identical runs will disagree about rows that share a value. And anything outside the world,
such as a live external tool an eval also gives the agent, is outside the promise.

## The change log

`inst.change_log()` returns every row the instance has changed since it was created, one record per
row per call, in call order. A record carries the call's ordinal `i`, the store, the table, the
operation, the row's key, and the row before and after. The store is `record.world`. A world that
adds no other worlds has one store, so every record says `main`. A world that adds others has one
store per added world, and `record.world` names the one the row belongs to
([composition.md](composition.md)).

```python
import projecttracker

with projecttracker.world.instance("small_startup") as inst:
    issue = inst.call("get_issue", key="ENG-3")
    inst.call("transition_issue", issue_id=issue["id"], status="done")
    changed = {(record.table, record.op) for record in inst.change_log()}
    assert ("issues", "update") in changed
```

**A record is the net of its own call, and the log is never folded across calls.** Inside one call:

- a write that leaves a value unchanged records nothing;
- an insert followed by an update of the same row is one insert;
- a call that rolled back leaves no trace.

And over the instance:

- rows written by startup hooks are not in the log, because no session is open while they run, and
  those rows are the world's setup rather than the agent's work;
- tables the world names in `World(untracked_tables=...)` are not in it, and neither are FTS5's
  shadow tables;
- an added world's store is in it, under its own path, with that world's own exclusions applied.

Two traps come out of "never folded across calls", and [state.md](state.md) has both: counting
records is not counting rows, and two episodes with the same end state can have different logs.

This is what an eval grades on: the state the run left behind, rather than the transcript of how it
got there. `inst.state()` is the document that carries the log, with the provenance needed to read
it. [state.md](state.md) is the page on the document, the formats it comes in, and the fold that
turns a log into the net difference an episode made.

## What Seahaven does not do

Seahaven does not put a time limit on a tool call, filter or project tools per instance, mock a
tool's response, move a clock forward, or generate data for you. It contains agent-written SQL and
nothing else. None of these is planned. If your eval needs one, build it into the harness around the
world.
