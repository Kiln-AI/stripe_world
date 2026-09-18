# The state document

An eval grades the state a run left behind. `inst.state()` is where it reads it: one plain dict,
JSON-serialisable with the standard library, holding the provenance of the episode and every row
the episode changed.

```python
import json

import projecttracker

with projecttracker.world.instance("small_startup", seed=7) as inst:
    issue = inst.call("get_issue", key="ENG-3")
    inst.call("transition_issue", issue_id=issue["id"], status="done")
    document = inst.state()

assert document["format"] == "seahaven.state/1"
assert document["world"] == {"name": "projecttracker", "version": "1.0.0"}
assert document["call_count"] == 2
assert json.dumps(document)  # nothing in it needs an encoder of yours
```

Save that dict as the episode's `final_state` and grade against it, now or in years. Over a server
the same document arrives on the `state` message; see
[serving_and_openenv.md](serving_and_openenv.md).

| Section | What it covers |
|---|---|
| [The envelope and `state`](#the-envelope-and-state) | The two halves of the document, and every field of the first |
| [Choosing a format](#choosing-a-format) | The three built-in formats, their reasons, and registering your own |
| [Where the format is chosen](#where-the-format-is-chosen) | The world's pin, the overrides, and when a bad name is caught |
| [When to read the document](#when-to-read-the-document) | Between calls, never inside one |
| [The change log](#the-change-log) | One record per row per call: the shape, the rules and the order |
| [The fold, and the two traps](#the-fold-and-the-two-traps) | The net of a whole episode, and the two mistakes it prevents |
| [Values](#values) | SQLite's storage classes as JSON |
| [The state the episode started from](#the-state-the-episode-started-from) | What the document points at instead of carrying it |
| [The compatibility contract](#the-compatibility-contract) | What a reader may rely on for as long as a document exists |
| [Why nothing derivable is in the document](#why-nothing-derivable-is-in-the-document) | The rule behind everything the document leaves out |

## The envelope and `state`

A document has two halves, and each half has its own owner.

```jsonc
{
  "format": "seahaven.state/1",
  "seahaven_version": "0.0.1",
  "world": {"name": "projecttracker", "version": "1.0.0"},     // the root world
  "composition": {                                             // every node, keyed by path
    "main": {"world": "projecttracker", "world_version": "1.0.0", "scope": null,
             "aliases": [], "schema_hash": "…", "frozen_world_version": null}
  },
  "fixture": {                                                 // null for a blank instance
    "id": "small_startup",
    "nodes": {"main": {"file_sha256": "…"}}
  },
  "episode_id": "…",
  "seed": 7,                                                   // as given; null if none
  "now": "2026-06-01T09:00:00.000Z",
  "startup": {},                                               // the reset keywords, if any
  "call_count": 2,
  "state": {"db": {"log": [ … ]}}                              // the formatter's output
}
```

Everything above `state` is the **envelope**. The framework writes it, and it is the same under
every format, built in or your own.

| Field | What it is |
|---|---|
| `format` | The name of the format that produced `state`, always first. A reader checks this before reading `state`, and keys on nothing else for it |
| `seahaven_version` | The Seahaven version of the process that produced the document. Informational; never key on it |
| `world` | `{name, version}` of the **root** world. The version is the world's contract: a schema, tool or format change bumps it |
| `composition` | One entry per node of the instance, keyed by canonical path, with the world there, its version, the scope, the aliases, the schema hash and `frozen_world_version`. A world that adds no other world has the one entry `main` |
| `fixture` | `{id, nodes}` from the fixture's description file, or `null` for an instance built from the schema. `nodes` is keyed by the same path, and holds each node's `file_sha256` |
| `episode_id` | Over a server, the session's episode id: the one `reset` was given, or the one it minted. In process an instance is an episode, so it is the instance id |
| `seed` | The `seed=` the caller gave, or `null`. Not the derived per-instance seed |
| `now` | The instance's frozen clock as an ISO-8601 instant, the same string `inst.clock.iso()` answers |
| `startup` | The reset keywords beyond `fixture`, `seed`, `now` and `state_format`, rendered as JSON at instance creation. A hook receives the value the caller passed; the document carries that value's JSON rendering, so a `datetime` or a model is text or an object here. Empty when there were none |
| `call_count` | How many calls have been dispatched to the instance. The last call's ordinal is one less. Over a server this is **not** `step_count`, which counts tool listings as well |
| `state` | The formatter's output, and the only part of the document `format` describes |

`state` is the **format's** half. A formatter answers the value of `state` and nothing else, so a
format can neither omit provenance nor misspell it.

A **path** is the join key across the whole document: `composition[path]` describes a node,
`fixture["nodes"][path]` names the file that node started from, and a log record's `world` names
one. The root's path is `main`. A world that adds other worlds has one more path per node; see
[composition.md](composition.md).

Read fields you know and ignore fields you do not. The envelope gains fields over time, and the
[compatibility contract](#the-compatibility-contract) is what that promise is worth.

## Choosing a format

Four cases, in the order you are likely to meet them.

### Read it once at the end: `seahaven.state/1`

The usual case, and the one the scaffold pins. A harness runs an episode, reads the document when
the episode is over, and saves it as `final_state`. `state` is `{"db": {"log": [...]}}`: the whole
[change log](#the-change-log), every row the episode changed.

```python
import projecttracker

with projecttracker.world.instance("small_startup") as inst:
    issue = inst.call("get_issue", key="ENG-3")
    inst.call("transition_issue", issue_id=issue["id"], status="done")
    log = inst.state()["state"]["db"]["log"]

closed = [entry for entry in log if entry["table"] == "issues"]
assert [entry["op"] for entry in closed] == ["update"]
assert closed[0]["after"]["status"] == "done"
```

### Read it after every step: `seahaven.state+last_step/1`

A rollout harness that stores the state in every step's record — OpenEnv's does — would store the
whole log once per step under `seahaven.state/1`, which grows quadratically over an episode. This
format has the same envelope and the same `state` shape, with one difference: `state.db.log` holds
only the records of the most recent call.

```python
import projecttracker

world = projecttracker.world

with world.instance("small_startup", state_format="seahaven.state+last_step/1") as inst:
    first = inst.call("get_issue", key="ENG-3")
    inst.call("transition_issue", issue_id=first["id"], status="done")
    after_the_first_write = inst.state()["state"]["db"]["log"]

    second = inst.call("get_issue", key="ENG-4")
    inst.call("transition_issue", issue_id=second["id"], status="canceled")
    after_the_second_write = inst.state()["state"]["db"]["log"]

assert {entry["i"] for entry in after_the_first_write} == {1}
assert {entry["i"] for entry in after_the_second_write} == {3}
```

The scope is the call counter and not the last read, so two reads between calls answer the same
document, and an episode's per-step documents concatenate into the whole log. A step the harness
did not read shows as a jump in `call_count`. A write made with no call in flight — an
`inst.bulk()` write — is never in this format, and neither is anything before the first call.

The trade is the one the name implies: a harness that keeps only the last document has only the
last call's changes. Keep every step's document, or pin `seahaven.state/1`.

### Reconcile a trace the document cannot see: `seahaven.state+calls/1`

`seahaven.state/1`'s `state`, plus one key: `calls`, the call log. One entry per dispatched call,
in dispatch order, so a log record's `i` is its index in `calls`.

```python
import projecttracker
import seahaven

with projecttracker.world.instance("small_startup", state_format="seahaven.state+calls/1") as inst:
    issue = inst.call("get_issue", key="ENG-3")
    inst.call("transition_issue", issue_id=issue["id"], status="done")
    try:
        inst.call("get_issue", key="ENG-999")
    except seahaven.ToolError:
        pass
    calls = inst.state()["state"]["calls"]

assert [entry["tool"] for entry in calls] == ["get_issue", "transition_issue", "get_issue"]
assert calls[0]["arguments"] == {"key": "ENG-3"}
assert calls[2]["error"] == "issue ENG-999 not found"
```

An entry is `{tool, arguments, error}`. `tool` is the name the caller used, which under composition
is the name on the root's tool surface, prefix included. `arguments` is what the call carried,
copied when the call was made and never re-serialised, so a tool that changes the dict it was
handed does not change the record.

`error` is `null` when the call returned. Otherwise it depends on what the call raised, because the
document is read by a harness and a harness may render it back into a model's context:

| What the call raised | What `error` says |
|---|---|
| a `ToolError`, the world's own or the framework's | its message, as the world wrote it |
| a `WorldBug` | `internal error` |
| anything else | `internal error` |

A `ToolError` is written for the agent and the agent has already read it on the observation, so it
is published as it is. The other two are written for the author, and the author reads them in the
server's log with a traceback and a correlation id, not here. In process nothing is hidden:
`inst.call_log()` answers `CallRecord`s whose `error` is the real message whatever the class was,
which is what a world's own tests read.

There is no result, because the harness already has it verbatim and a result can be a whole
listing, and there are no counts, because every count is one pass over `calls`. Only dispatched
calls are here: a name the world refused never reached the world, and a tool listing is not a call.

Most harnesses do not need this format. It exists for a consumer that cannot line its own trace up
with the document — a trace that spans two worlds, or that mixes real tools with synthetic ones —
and needs the document to carry its own account of what was asked. Seahaven keeps the call log for
every instance whether or not this format is read, so `inst.state(format="seahaven.state+calls/1")`
answers on any instance.

### Something else: register your own

A world may publish formats of its own, for a caller that none of the three built-in formats fits.

```python
from typing import Any

import seahaven

world = seahaven.World(
    name="notes",
    version="1.0.0",
    schema="CREATE TABLE notes (id TEXT PRIMARY KEY, body TEXT NOT NULL) STRICT;",
    state_format="notes.touched/1",
)


@world.tool
def add_note(ctx: seahaven.Ctx, body: str) -> dict[str, str]:
    """Write a note down and return it."""
    note = {"id": ctx.ids.uuid(), "body": body}
    ctx.db.execute("INSERT INTO notes (id, body) VALUES (?, ?)", note["id"], note["body"])
    return note


@world.state_format("notes.touched/1")
def touched(world: seahaven.World, instance: seahaven.Instance | None) -> dict[str, Any]:
    """Which rows the episode touched, and nothing about how."""
    if instance is None:
        return {"touched": []}
    rows = {(record.world, record.table, str(record.key["id"])) for record in instance.change_log()}
    return {
        "touched": [{"world": node, "table": table, "id": key} for node, table, key in sorted(rows)]
    }


with world.instance() as inst:
    inst.call("add_note", body="buy milk")
    document = inst.state()

assert document["format"] == "notes.touched/1"
assert [row["table"] for row in document["state"]["touched"]] == ["notes"]
assert document["seahaven_version"] == seahaven.__version__
```

The rules:

- The name is `<family>/<major>`: one `/`, then a positive integer. `seahaven.` is reserved for the
  framework's own formats, and a duplicate name is refused.
- The function takes the world and the instance and returns a JSON-serialisable dict: **the value
  of `state`, and nothing else.** The framework writes the envelope around it.
- The instance is `None` for the state a server answers before its first `reset`. Decide what your
  format's "no episode yet" value is. A formatter that raises on `None` makes that read a
  `WorldBug`, which is the formatter author's contract to keep.
- To publish a variation of a built-in format, read one and edit it:
  `instance.state(format="seahaven.state/1")["state"]` is a copy of the built-in's output. To merge
  two of them, read both.
- A formatter reads every node's rows, so write one that tolerates a `world` naming more than the
  root. The built-ins do.
- Registration is open for the life of the world, as it is for tools and middleware, and an
  extension may register a format. Registering one does not invalidate a sealed composition.
- A formatter runs under the instance lock and never inside a transaction. It reads; it does not
  write. One that writes raises `WorldBug`.

## Where the format is chosen

| Written | What it does |
|---|---|
| `World(state_format="seahaven.state/1")` | **required**: the world's pin, and what every instance of it answers in |
| `world.instance(..., state_format="…")` | overrides the pin for that instance alone |
| `reset(state_format="…")` | the same, over a server, for that episode |
| `inst.state(format="…")` | answers in another format for the same instance — a built-in, or one this world registered — leaving the instance's own format alone. In process only: the `state` message carries no arguments |
| `inst.state_format` | the format this instance answers in, fixed for its life |
| `world.pinned_state_format` | the pin this world was constructed with |

There is no default and no fallback. A `World` built without `state_format` raises, and the message
names the built-in formats.

**The root decides.** An instance is made from one root world, and the root's pin is the instance's
format. An added world's pin is not consulted, and neither are the formats it registered: a
formatter reads every node's rows, and one written for a world on its own has no business being
reachable from a tree that world never knew about. Every world may be a root, which is why every
world carries a pin.

```python
import seahaven

payments = seahaven.World(
    name="payments",
    version="1.0.0",
    schema="CREATE TABLE charges (id TEXT PRIMARY KEY, amount INTEGER NOT NULL) STRICT;",
    state_format="seahaven.state+calls/1",
)


@payments.tool
def create_charge(ctx: seahaven.Ctx, amount: int) -> dict[str, object]:
    """Take a payment and return the charge."""
    charge = {"id": ctx.ids.uuid(), "amount": amount}
    ctx.db.execute("INSERT INTO charges (id, amount) VALUES (?, ?)", charge["id"], charge["amount"])
    return charge


shop = seahaven.World(
    name="shop",
    version="1.0.0",
    schema="CREATE TABLE orders (id TEXT PRIMARY KEY, total INTEGER NOT NULL) STRICT;",
    state_format="seahaven.state/1",
)
shop.add_world(payments, name="payments", tool_prefix="payments_")


@shop.tool
def place_order(ctx: seahaven.Ctx, total: int) -> dict[str, object]:
    """Record an order and take the payment for it."""
    order = {"id": ctx.ids.uuid(), "total": total}
    ctx.db.execute("INSERT INTO orders (id, total) VALUES (?, ?)", order["id"], order["total"])
    ctx.worlds.payments.call("create_charge", amount=total)
    return order


with shop.instance() as inst:
    inst.call("place_order", total=500)
    document = inst.state()

# The root's pin, although the added world pins another format.
assert document["format"] == "seahaven.state/1"
assert "calls" not in document["state"]
# One call, two nodes, one ordinal: the nested call belongs to the call that made it.
assert sorted(document["composition"]) == ["main", "payments"]
assert [
    (entry["i"], entry["world"], entry["table"]) for entry in document["state"]["db"]["log"]
] == [
    (0, "main", "orders"),
    (0, "payments", "charges"),
]
```

`state_format` is a reserved reset keyword, beside `fixture`, `seed` and `now`: a startup hook that
names a parameter `state_format` is refused at registration, and the keyword never reaches a hook.

When a bad name is caught depends on what is wrong with it. A name that is not spelled
`<family>/<major>` is refused at `World(...)`, and so is a `seahaven.` name the framework does not
publish. Whether a well-spelled name of your own was ever *registered* can only be checked when an
instance is made, because a world registers its formats after its `World(...)` line has run — so
`World(state_format="acme.state/1")` is accepted, and the first `world.instance(...)` or `reset`
fails if nothing has registered it by then. Either way the refusal comes before anything is copied.
Over a server it arrives as `EXECUTION_ERROR` and the session stays open for another `reset`.

Changing a world's pin changes what every eval built on that world saves, so bump `world.version`
when you change it. These docs recommend it; nothing enforces it.

## When to read the document

**Read it between calls, never inside one.** `inst.state()` raises `WorldBug` inside a transaction
— inside `inst.bulk()`, or inside a tool call — because the rows written there are not committed
yet and no document could describe them: the log would be missing rows that the same block can
already read through `ctx.db`, with nothing to say so. Leave the block, or let the call return,
and then read.

```py
with inst.bulk() as ctx:
    ctx.db.execute("INSERT INTO notes (id, body) VALUES (?, ?)", "n1", "seeded")
    inst.state()  # WorldBug: state cannot run inside a transaction

document = inst.state()  # here
```

The same rule is why a formatter never runs in a transaction. Reading the document costs
serialisation and nothing else: each call's records were rendered when that call committed, so
`inst.state()` does no database work however long the episode ran.

## The change log

All three built-in formats fill `state.db.log` with the same kind of record. One record is one row
changed by one call.

```jsonc
{"i": 1, "world": "main", "table": "issues", "op": "update",
 "key": {"id": "iss_3"},
 "before": {"status": "backlog", "updated_at": "2026-05-28T11:21:55.412Z"},
 "after":  {"status": "done",    "updated_at": "2026-06-01T09:00:00.000Z"}}
```

| Field | What it is |
|---|---|
| `i` | The ordinal of the call that changed the row, counting every dispatched call from 0, or `null` for a write made with no call in flight (`inst.bulk()`) |
| `world` | The canonical path of the node the row belongs to: `main` for the root, the added node's path otherwise |
| `table` | The table name inside that node |
| `op` | `"insert"`, `"update"` or `"delete"` |
| `key` | The row's primary key, as `{column: value}`, in primary-key column order |
| `before` | `null` for an insert. The whole row for a delete. For an update, exactly the non-key columns that call changed, with their old values |
| `after` | `null` for a delete. The whole row for an insert. For an update, the same columns as `before`, with their new values |

In process the same records are objects rather than dicts. `inst.change_log()` answers
`list[LogRecord]`, and `record.to_dict()` is the shape above. The list is the caller's; the records
in it are the instance's, so read them rather than editing them.

**A record is the net of its own call.** A call is one transaction per node it touches, and its
records are the difference between the instance before the call and after it, row by row: a write
that leaves a value unchanged records nothing, an insert followed by an update of the same row is
one insert with the final values, a row inserted and deleted in the same call leaves no record, and
a call that raised and rolled back leaves none. A read-only call has no records.

**The log is not net across calls.** A row changed by two calls appears twice, once per call. That
is where both [traps](#the-fold-and-the-two-traps) below come from.

Four more rules are worth knowing:

- **The key is never repeated inside an update.** It is in `key`, which is where a reader joins on
  it. An insert and a delete carry the whole row, key columns included, because that is the row.
- **A primary-key rewrite is a delete plus an insert**, never an update.
- **Every node is in one list.** A call that writes to the root and to an added node produces
  records for both, under the same `i`, told apart by `world`. A tool that reaches another node
  through `ctx.worlds.<name>.call(...)` takes no ordinal of its own: the caller made one call, and
  the rows that tool wrote carry that call's `i`.
- **Startup writes are not in the log.** Startup hooks run when the instance is created, before the
  first call, so what they wrote is starting state rather than the agent's work. Every write after
  that is in the log, `inst.bulk()` writes included, which carry `i: null`.

What is tracked is every table of every node, except the tables that node's world named in
`World(untracked_tables=...)`, FTS5's shadow tables, and virtual tables. A table with no explicit
primary key is refused when the instance is created rather than tracked silently, which is what
`SH102` checks before you ever run one. Nothing is capped or truncated: the log is as long as the
episode made it. A consumer that wants less filters the log afterwards, or registers a format of its
own.

### Order

Records are ordered by call: every record of call 0 before every record of call 1. A record with
`i: null` sits where its transaction committed, relative to the calls around it. Within one call,
records are sorted by `world`, then `table`, then the key's values in primary-key column order,
ascending. A blob key sorts by its base64 text, which is the only form a reader of the document
holds. `main` therefore sorts alphabetically among the other paths rather than first; a reader that
wants the root first has the field to do it with. Two identical episodes produce byte-identical
logs.

## The fold, and the two traps

The net difference an episode made is the **fold** of its log. The format defines the fold so that
any reader, in any language, computes the same one:

1. Group records by `(world, table, key)`, and fold each group in log order, starting from
   "untouched".
2. Compose. **Untouched plus any record is that record**, whatever its `op`: a row the fixture
   already held opens its group with an update or a delete, not an insert. From there: insert plus
   update is the insert with the update's `after` overlaid; insert plus delete is untouched; update
   plus update is an update with `before` from the first and `after` from the last, dropping any
   column whose folded `after` equals its folded `before`; update plus delete is a delete whose
   `before` is the deleted row with the update's `before` values overlaid; delete plus insert is an
   update if the rows differ, reduced to the differing non-key columns, and untouched if they do
   not.
3. Drop the groups that folded to untouched, and sort what is left the way records are sorted
   within a call.

Seahaven does not ship the fold. It is a page of code wherever you grade, and Seahaven's test suite
carries one, checked against SQLite's own cumulative changeset over the same episode.

**Trap one: counting straight off the log overcounts.** Two calls that change the same row leave
two records, so a count of records is not a count of rows.

```python
import projecttracker

with projecttracker.world.instance("small_startup") as inst:
    issue = inst.call("get_issue", key="ENG-3")
    inst.call("update_issue", issue_id=issue["id"], title="First")
    inst.call("update_issue", issue_id=issue["id"], title="Second")
    log = inst.state()["state"]["db"]["log"]

updates = [entry for entry in log if entry["table"] == "issues"]
assert len(updates) == 2
assert len({entry["key"]["id"] for entry in updates}) == 1  # one row, twice
```

Fold, or deduplicate by `(world, table, key)`, before you count.

**Trap two: two episodes with the same end state can have different logs.** One agent closes an
issue. Another closes it, reopens it and closes it again. The end states match and the logs do not.
"Same end state" compares folds, never logs.

## Values

Every value in a record is a JSON value, mapped from SQLite's storage classes:

| SQLite | JSON |
|---|---|
| `INTEGER` | number. SQLite integers are 64-bit, so a JavaScript reader loses precision above 2^53. The format does not work around it |
| `REAL` | number, except that an infinity becomes `null` |
| `TEXT` | string |
| `BLOB` | string, base64 |
| `NULL` | `null` |

The infinity rule is worth stating plainly. JSON carries neither infinities nor NaN, and SQLite
itself already stores NaN as `NULL`, so this is SQLite's own coercion one step further. A reader
cannot tell a `NULL` column from an infinite one. That is less precise, not wrong, and a column a
world stores infinities in is a column to grade some other way.

## The state the episode started from

The document says what changed, not what the instance began with. That is deliberate, and the
envelope carries the lookup instead: `composition` and `fixture["nodes"]`, both keyed by the same
path. Together they name, for every node, the world and version that ran there, the schema hash it
ran against, and the hash of the exact file it started from.

```python
import projecttracker

with projecttracker.world.instance("small_startup") as inst:
    document = inst.state()

assert document["fixture"]["id"] == "small_startup"
assert len(document["fixture"]["nodes"]["main"]["file_sha256"]) == 64
assert document["composition"]["main"]["world"] == "projecttracker"

with projecttracker.world.instance() as blank:
    assert blank.state()["fixture"] is None  # built from the schema
```

For a blank instance, `fixture` is `null` and the starting state is the world's schema plus
whatever the startup hooks wrote, with `startup` recording the keywords it was created with. **The
format does not promise that starting state is reproducible.** A startup hook is ordinary world
code and may read anything, and a blank instance's clock is wall time unless `now=` was given.
Grade against a frozen fixture when the starting state has to be pinned down.

## The compatibility contract

Two contracts, one per half of the document.

**The envelope is the framework's.** A field is only ever added: never removed, renamed, retyped or
rebuilt. Key on a field's presence, and on `seahaven_version` for information only.

**`state` is the format's**, and `format` is the only thing to key on for it. Within a published
format, no field of `state` is removed or renamed, none changes type, and the construction of an
existing field does not change even where its type would stay the same. A reader written against
`seahaven.state/1` on the day it shipped reads every `seahaven.state/1` document ever produced. A
field may be added only if a reader that ignores it loses nothing.

**So ignore the fields you do not know**, in the envelope and in `state` alike. Anything the rules
above forbid is a new major: `seahaven.state/2`, a new formatter, with the old one still callable
and frozen. Removing a published format would be a breaking Seahaven release, and none is planned.

`world.version` is the root world's contract and `composition[path]["world_version"]` each node's:
a judge may assume that two documents with the same composition were produced by the same schemas
and the same tools.

## Why nothing derivable is in the document

There is no fold, no count, no end-state row and no diff against the fixture. Each of those is
computable from what is already there, and each would be a second thing to keep true: a field a
later release could get subtly wrong while the log beside it stayed right, and a reader with two
sources to reconcile. The log and the provenance are the primitives, a grader computes what it
needs from them, and the framework does not guess which.

That is also why the document costs no database work to produce, however long the episode ran.

Recording is not free at the other end: a session is opened on each node for each call, and what it
recorded is rendered when the call commits. The Seahaven repository carries a probe that measures
what that costs, under `bench/`. Run the probe with
`uv run python -m bench recording --calls 1000 --repeats 9`.

The figures below are approximate. They come from a shared virtual machine, and the probe prints
the machine it ran on. Read the figures as the size of the cost, not as the cost.

A write-heavy call on ProjectTracker's `agency` fixture costs about 47% more than the single
long-lived session per node that Seahaven kept before this release. Reading the changeset and
rendering the records is about 33 of those 47 percentage points, and that work is what an eval's
own read of the changeset used to do once an episode. A read-only call records nothing, so it adds
less than a write call does. A read-only call is also the harder figure to pin down: repeats of the
same command put it between about 5% and 18% above the same long-lived shape, on a call of
under 0.1 ms.

A composite instance opens a session on every node of the tree for every call. The four-node
`emporium` world used in the framework's own tests came out at 45% to 67% on its write calls,
which overlaps the one-node figure above, so the share does not grow in proportion to the node
count even though the work does.
