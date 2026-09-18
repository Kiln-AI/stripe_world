# Authoring a world

This is the long page. It covers the package layout, the state format a world pins, tools,
arguments, results, transactions, errors, the error handler, middleware, startup hooks, the schema
files, and the things that go wrong quietly.

| Section | What it covers |
|---|---|
| [Scaffolding a world](#scaffolding-a-world) | `seahaven new`, the layout it writes, and three rules about it |
| [Pinning a state format](#pinning-a-state-format) | The one `World` argument with no default |
| [Writing a tool](#writing-a-tool) | Signatures, arguments, results, transactions, and what registration refuses |
| [Errors](#errors) | This world's error shapes, the framework's own, and the error handler |
| [Middleware](#middleware) | Wrapping every call |
| [Instance startup](#instance-startup) | Setting an instance up before the first call |
| [Adding another world](#adding-another-world) | The four authoring decisions composition asks of you |
| [The schema files](#the-schema-files) | Where the SQL files go, and full-text search |
| [Things that go wrong quietly](#things-that-go-wrong-quietly) | The mistakes `seahaven check` exists for |

## Scaffolding a world

Start by scaffolding. `seahaven new notes` writes a working world with one table, two tools, an
error handler and a passing test. Nothing in it is a placeholder you have to decode.

```sh
seahaven new notes
```

```
notes/
  pyproject.toml
  README.md                    # also the OpenEnv environment's README, the card a hub shows
  AGENTS.md                    # points at these docs, plus this world's own notes
  src/notes/
    __init__.py                # imports world, then tools and middleware; registers factories
    world.py                   # world = seahaven.World(...)
    errors.py                  # this world's error shapes
    openenv_app.py             # app = seahaven.openenv.app(world)
    schema/001_items.sql       # the SQL, applied in filename order
    tools/__init__.py          # imports every module beside it
    tools/items.py             # one module per resource
    middleware/__init__.py
    middleware/error_handler.py
  fixtures_src/generate.py     # the script every fixture is built by; committed
  fixtures/                    # empty until you freeze one
  tests/test_items.py
  tests/test_fixtures.py       # the fixture recipe above, run into a temporary directory
```

**Before you run the `uv sync` that `seahaven new` prints as its next step:** while Seahaven is
unpublished, that step does not do what it looks like. A scaffold depends on `seahaven~=0.0`, and
that resolves to a placeholder release on PyPI which contains none of the framework. `uv sync`
succeeds, and the world then fails at import with `ModuleNotFoundError: No module named
'seahaven.world'`, raised by the scaffold's own `middleware/error_handler.py`, which says nothing
about where the package came from. `seahaven check` does not even start, because the placeholder
ships no console script. Install the framework from a checkout instead (`uv pip install -e
/path/to/Seahaven`, with `[serve]` if the world will be served), or work in an environment that
already has it. This paragraph disappears when Seahaven is published.

Three rules about the layout are worth stating now.

**A world is a package**, never a directory loaded by path. The tooling finds a world by importing
it.

**Group tools by resource**, rather than one directory per tool. Write `tools/issues.py` with seven
tools in it, not `tools/get_issue/`.

**A world is checked out, not installed.** The unit you deploy is the whole directory, because
`fixtures/` sits outside the package and does not travel in a wheel. See
["Where fixtures live"](db_schema_and_fixtures.md#where-fixtures-live). It is the one thing about
the layout that
bites later rather than at import.

`src/notes/__init__.py` imports `world`, then the tool and middleware modules for their side
effects, then registers any imported factories. A module under `tools/` or `middleware/` that
nothing imports registers nothing, and `seahaven check` fails on it (`SH301`) rather than letting
the world quietly have one tool fewer than you think.

## Pinning a state format

`World(state_format=...)` is **required**, and it is the one constructor argument with no default.
It names the format `inst.state()` answers in, and a `World` built without it is refused at
construction with the built-in format names in the message. The scaffold writes
`state_format="seahaven.state/1"`, which is the format a harness that reads the state once at the
end of an episode wants. [state.md](state.md) covers the three built-in formats, and how to register
one of your own.

```py
world = seahaven.World(
    name="notes",
    version="1.0.0",
    schema=seahaven.sql_files(__package__, "schema"),
    state_format="seahaven.state/1",
)
```

Three things follow from the pin, and each of them catches an author out once:

- **The root decides.** An instance is made from one root world, and that root's pin is the
  instance's format. The pin of a world you added is not consulted, and neither are the formats it
  registered. Every world may be a root, which is why every world carries a pin.
- **A caller may override it per instance**, with `world.instance(state_format=...)` or
  `reset(state_format=...)`. `state_format` is therefore a reserved reset keyword beside `fixture`,
  `seed` and `now`: a startup hook that names a parameter `state_format` is refused at registration,
  and the keyword never reaches a hook.
- **Changing the pin changes what every eval built on the world saves**, so bump `world.version`
  when you change it. These docs recommend it; nothing enforces it.

## Writing a tool

A tool is a plain synchronous function. Its first parameter is the instance context; the rest are
its arguments.

```python
import seahaven

world = seahaven.World(
    name="notes",
    version="1.0.0",
    schema="""
    CREATE TABLE notes (
        id TEXT PRIMARY KEY,
        body TEXT NOT NULL,
        pinned INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    ) STRICT;
    """,
    state_format="seahaven.state/1",
)


@world.tool
def add_note(ctx: seahaven.Ctx, body: str, pinned: bool = False) -> dict[str, object]:
    """Write a note down.

    The whole docstring is the description an agent reads before deciding to call
    this tool, so write it for that reader: what it does, what the arguments mean,
    and what comes back.
    """
    note = {
        "id": ctx.ids.uuid(),
        "body": body,
        "pinned": pinned,
        "created_at": ctx.clock.iso(),
    }
    ctx.db.execute(
        "INSERT INTO notes (id, body, pinned, created_at) VALUES (?, ?, ?, ?)",
        note["id"],
        note["body"],
        1 if pinned else 0,
        note["created_at"],
    )
    return note


with world.instance(now="2026-06-01T09:00:00.000Z") as inst:
    listing = inst.tools()[0]
    assert listing["name"] == "add_note"
    assert listing["description"].startswith("Write a note down.")
    assert listing["input_schema"]["properties"]["pinned"]["default"] is False
    assert inst.call("add_note", body="buy milk")["pinned"] is False
```

The name defaults to the function name, and the description to the whole docstring. You can set both
explicitly, and the only other option is the transaction:

```py
@world.tool(name="notes.add", description="Write a note down.", transaction=False)
def add_note(ctx: seahaven.Ctx, body: str) -> dict[str, object]: ...
```

There is nothing else to configure. No groups, no visibility, no projections.

### Arguments

Seahaven builds one pydantic model per tool from the signature, at registration, and that model's
JSON schema is what the tool list publishes. The model is **strict**:

- `"5"` is not an `int`, `2.0` is not an `int`, and `1` is not a `bool`;
- an argument the tool does not declare is refused;
- every violation is reported at once, as a `seahaven.ArgumentError` carrying `violations`, so an
  agent can fix all of them in one turn.

Argument types are the JSON types, `Literal`, and nested pydantic models. Two kinds are refused at
registration, because strict validation could never satisfy them from a wire format: `datetime`,
`date` and `time`, and any `Enum` subclass, anywhere inside the annotation. Use `str` with a pattern
for a timestamp, and `Literal` for a closed set:

```py
from typing import Annotated, Literal

from pydantic import Field

Status = Literal["backlog", "todo", "done"]
Timestamp = Annotated[
    str,
    Field(
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$",
        description="A UTC timestamp with milliseconds, e.g. 2026-06-01T09:00:00.000Z.",
    ),
]
Limit = Annotated[int, Field(ge=1, le=250, description="How many rows to return, 1 to 250.")]
```

Declare these once in a module of their own and import them everywhere, so that five tools that take
a status take the same five strings.

Two escapes exist for products whose wire format is not Python's:

```python
from typing import Annotated

import seahaven
from pydantic import Field

world = seahaven.World(
    name="ledger",
    version="1.0.0",
    schema="CREATE TABLE entries (id TEXT PRIMARY KEY) STRICT;",
    state_format="seahaven.state/1",
)


@world.tool
def transfer(
    ctx: seahaven.Ctx,
    # `from` is a Python keyword, so the parameter is `from_` and the wire name
    # is set with an alias. The agent sends `from`; the function receives `from_`.
    from_: Annotated[str, Field(alias="from")],
    to: str,
    # A product that accepts "5" for a number relaxes that one argument.
    amount: Annotated[int, Field(strict=False)],
) -> dict[str, object]:
    """Move money between two accounts."""
    return {"from": from_, "to": to, "amount": amount}


with world.instance() as inst:
    schema = inst.tools()[0]["input_schema"]
    assert sorted(schema["required"]) == ["amount", "from", "to"]
    assert inst.call("transfer", **{"from": "a", "to": "b", "amount": "5"})["amount"] == 5
```

An alias replaces the Python name on the wire; it does not add to it. Calling that tool with
`from_=` is an `ArgumentError` for a missing `from` and an unexpected `from_`, which is the right
answer: the tool list said `from`.

### Results

A result is JSON-serialisable data: `dict`, `list`, scalars, `None`, and pydantic models and
dataclasses, which Seahaven renders for you. **`bytes` and `set` are refused.** `bytes` is not a
result type, and a `set` serialises in whatever order it iterates, which is exactly the
non-determinism this framework exists to prevent. The tool never sees the wire format.

### Transactions

By default **one call is one transaction**. Seahaven begins it before the tool runs, commits it when
the tool returns, and rolls it back if the tool raises. Nothing partial survives a tool that fails
half way.

Inside that, `ctx.db.transaction()` is a savepoint:

```py
with ctx.db.transaction():
    ctx.db.execute("UPDATE accounts SET balance = balance - ? WHERE id = ?", amount, source)
    ctx.db.execute("UPDATE accounts SET balance = balance + ? WHERE id = ?", amount, target)
```

`@world.tool(transaction=False)` opts out, for a product whose single call really does commit in
stages, or fails half way and leaves what it had already done. Seahaven then begins nothing,
statements autocommit, and the tool opens its own transactions where it wants them.

### What registration refuses

All of it happens at import, with a message naming the tool and the parameter. A tool is refused
when it:

- duplicates another tool's name;
- is named `reset`, `step`, `state` or `close` (OpenEnv reserves those), or the control tool that
  [reference/cli.md](reference/cli.md) names;
- is an `async def`, a generator, or an async generator;
- has no first parameter, or one that is not positional and either annotated `seahaven.Ctx` or left
  unannotated;
- has an argument with no type annotation, a positional-only argument, `*args` or `**kwargs`;
- has an argument annotated `datetime`, `date`, `time` or an `Enum` subclass, at any depth;
- has an argument whose annotation exists only under `TYPE_CHECKING`, in which case the message
  names the symbol;
- has an argument with a mutable default (`[]`, `{}`, `set()`);
- has an argument pydantic cannot give a JSON schema. The message names which one, found by
  rebuilding the fields one at a time.

A middleware is refused if it is not callable with three positional arguments. A startup hook is
refused unless the context is its one and only positional parameter — none, or two, or a `*args` is
refused alike — and if it names a parameter `fixture`, `seed`, `now` or `state_format`.

An `add_world` is refused when its `name` is not `^[a-z][a-z0-9_]*$`, contains `__`, or is `main` or
`temp`; when this world already adds one under that name; when `tool_allow_list` and
`tool_block_list` are both given, or either is a bare string; when `store` is given as anything but
a scope name; when a `startup` keyword is one the added world's own hooks do not accept; and when
the added world's tree contains this one. Everything that is a property of the whole tree — a list
naming a tool that does not exist, two routes producing one tool name, the attach bound — is checked
at the first use of the tree instead, and `seahaven check` is where you meet it
([composition.md](composition.md)).

All of these raise `seahaven.WorldBug`, which is Seahaven's word for "the author has made a
mistake". A `WorldBug` is never shown to an agent.

## Errors

A world declares the error shapes the real product returns, as ordinary exception classes in
`errors.py`, each subclassing `seahaven.ToolError(code, message, details=None)`. Nothing about them
is registered on the `World`. You raise them where they happen.

```python
import seahaven


class NotFound(seahaven.ToolError):
    """A record the caller named does not exist."""

    def __init__(self, kind: str, key: str) -> None:
        super().__init__("NOT_FOUND", f"{kind} {key} not found", {"kind": kind, "key": key})


world = seahaven.World(
    name="notes",
    version="1.0.0",
    schema="CREATE TABLE notes (id TEXT PRIMARY KEY, body TEXT NOT NULL) STRICT;",
    state_format="seahaven.state/1",
)


@world.tool
def get_note(ctx: seahaven.Ctx, note_id: str) -> dict[str, object]:
    """Fetch one note by id."""
    row = ctx.db.one("SELECT id, body FROM notes WHERE id = ?", note_id)
    if row is None:
        raise NotFound("note", note_id)
    return row


with world.instance() as inst:
    try:
        inst.call("get_note", note_id="nope")
    except seahaven.ToolError as error:
        assert error.code == "NOT_FOUND"
        assert error.to_dict() == {
            "code": "NOT_FOUND",
            "message": "note nope not found",
            "details": {"kind": "note", "key": "nope"},
        }
```

The codes and the message text are the world's own. In process, `inst.call(...)` raises. Over a
server, the same error arrives on the observation with no `result`: `error` carries OpenEnv's
`{error_type, message}` and `metadata["seahaven_error"]` carries the triple above. It is never a
protocol error and never closes the session.
[serving_and_openenv.md](serving_and_openenv.md#calls-results-and-errors) has the wire shape.

### The framework's own errors

Everything Seahaven raises is a `seahaven.SeahavenError`, with two branches:

- **`seahaven.WorldBug`** is framework misuse or a bug in world code: a registration error, a call
  on a destroyed instance, an unserialisable result, a bad fixture. It is never shown to an agent,
  because it is meant to reach you.
- **`seahaven.ToolError`** is anything an agent is meant to read. Seahaven's three subclasses are
  `seahaven.ArgumentError` (validation failed; `violations` lists every problem), `seahaven.DbError`
  (SQLite refused something; it carries `sqlite_message` but never puts engine text in the
  agent-facing message by itself) and `seahaven.UnknownTool` (a call naming a tool the world does
  not have).

`UnknownTool` is raised before any middleware, because there is no tool to wrap. Everything else
passes through the chain, so the error handler sees it.

### The error handler

Every world has `middleware/error_handler.py`, scaffolded by `seahaven new` and registered first, so
it is outermost. Its job is one sentence: **nothing reaches the agent that this world did not
choose.**

```py
@world.middleware
def error_handler(ctx: seahaven.Ctx, call: seahaven.Call, next_: Handler) -> Any:
    """Map everything that escapes a tool into one of this world's error shapes."""
    try:
        return next_(ctx, call)
    except seahaven.ArgumentError as error:
        # Every violation in one error: the framework collected them so the agent
        # can fix them in one turn.
        raise InvalidInput.from_violations(error.violations) from error
    except seahaven.DbError as error:
        # The engine's complaint is about SQL this world wrote. It goes to the log
        # with its traceback; the agent gets this product's wording.
        _log.error("%s: database error under %s", ctx.instance.id, call.name, exc_info=error)
        raise Internal() from error
    except seahaven.ToolError:
        raise  # this world's own shapes: written for the agent already
    except seahaven.WorldBug:
        raise  # the author's, not the agent's. Loudly, and unchanged
    except Exception as error:
        raise Internal() from error
```

Edit it to match the product. Two rules are worth keeping.

**Re-raise a `WorldBug` unchanged.** Turning it into a product error hides a bug from you and tells
the agent a lie.

**Let engine text reach an agent only where the product is a SQL door.** If you register
`seahaven.helpers.run_sql`, let its `DbError` through by tool name, because a SQL console's errors
are SQL errors and "database error" would tell an agent nothing about the syntax it got wrong.
Everywhere else a `DbError` means this world's own SQL was wrong, and the agent can do nothing with
that.

A tool that lets SQLite refuse something it could have refused itself — a missing parent row, a
duplicate unique key — turns a sentence the agent could have acted on into an internal error. Look
the parent up first and raise the world's own `NOT_FOUND`. Keep the foreign key as the second lock
on the door.

## Middleware

A middleware is anything callable as `(ctx, call, next_) -> result`. There is nothing to subclass;
the shape is checked at registration. It may inspect or replace arguments, short-circuit, transform
results, catch and re-raise errors, or time the call.

```python
import logging
from typing import Any

import seahaven

world = seahaven.World(
    name="notes",
    version="1.0.0",
    schema="CREATE TABLE notes (id TEXT PRIMARY KEY, body TEXT NOT NULL) STRICT;",
    state_format="seahaven.state/1",
)
seen: list[str] = []


@world.middleware
def audit(ctx: seahaven.Ctx, call: seahaven.Call, next_: seahaven.world.Handler) -> Any:
    """Record every call, then run the rest of the chain."""
    seen.append(call.name)
    return next_(ctx, call)


@world.middleware
def default_the_body(ctx: seahaven.Ctx, call: seahaven.Call, next_: seahaven.world.Handler) -> Any:
    """Fill in an argument the agent left out, before validation sees it."""
    if call.name == "add_note" and "body" not in call.arguments:
        call = call.with_arguments(body="(empty)")
    return next_(ctx, call)


@world.tool
def add_note(ctx: seahaven.Ctx, body: str) -> dict[str, object]:
    """Write a note down."""
    return {"id": ctx.ids.uuid(), "body": body}


with world.instance() as inst:
    assert inst.call("add_note")["body"] == "(empty)"
    assert seen == ["add_note"]
```

- **Order is registration order, outermost first.** The error handler is registered first in the
  scaffold and stays outermost.
- `call` is always `ctx.call`. A layer that rewrites arguments passes the new `Call` on, and the
  chain hands the next layer a `ctx` whose `call` is that one, so the two cannot diverge.
- `call.with_arguments(**changes)` merges over the existing arguments and returns a copy. A `Call`
  is frozen.
- Middleware runs for every tool call, including the helpers' and an extension's. It does **not**
  run for `UnknownTool`, for a tool listing, for startup hooks, or for the control tool.
- Arguments are raw until validation runs, which happens inside the chain. A layer that wants typed
  arguments first calls `call.tool.validate(call.arguments)` itself. The model is built once at
  registration, so that is cheap.

## Instance startup

`@world.instance_startup` registers a hook that runs once per instance, after the file is copied and
the connection is set up, and before the first tool call. Several may be registered — an extension
may bring one — and they run in registration order.

The hook's keyword arguments are the `reset()` arguments beyond `fixture`, `seed`, `now` and
`state_format`. That is how a per-instance parameter reaches world code:

```python
import seahaven

world = seahaven.World(
    name="notes",
    version="1.0.0",
    schema="CREATE TABLE notes (id TEXT PRIMARY KEY, body TEXT NOT NULL, author TEXT NOT NULL)"
    " STRICT;",
    state_format="seahaven.state/1",
)


@world.instance_startup
def remember_author(ctx: seahaven.Ctx, *, author: str = "anonymous") -> None:
    """Who this session is writing as. Spell the parameters out, one per line."""
    ctx.state["author"] = author


@world.tool
def add_note(ctx: seahaven.Ctx, body: str) -> dict[str, object]:
    """Write a note down, as whoever this session is."""
    return {"id": ctx.ids.uuid(), "body": body, "author": ctx.state["author"]}


with world.instance(author="ada") as inst:
    assert inst.call("add_note", body="hello")["author"] == "ada"

try:
    world.instance(auther="ada")  # a typo, caught before anything is copied
except seahaven.WorldBug as error:
    assert "unknown reset argument" in str(error)
```

**Spell the parameters out.** A hook that takes `**kwargs` accepts everything, which switches off
unknown-argument detection for the whole world. A misspelled `reset` argument would then silently do
nothing, instead of failing before the instance exists.

A hook may not name a parameter `fixture`, `seed`, `now` or `state_format`; those are `reset`'s
own. Hooks put what they worked out in `ctx.state`, and tools read it from there. A principal is
application code: the world stores `user_id`, and its tools read it. Rows a hook writes are *not*
in the change log, because no session is open while the hooks run.

If a hook cannot do its job — a `user_id` naming nobody — raise `seahaven.WorldBug`. Instance
creation then fails, and a run never starts against state that was set up wrong.

## Adding another world

`world.add_world(other.world, name="payments", tool_prefix="pay_")` is the fourth registration verb.
The added world's tools join this world's agent surface under a name of your choosing, its store
becomes a second SQLite file in every instance, and your own tools reach it through
`ctx.worlds.payments`. [composition.md](composition.md) is the page for the mechanism. Four
decisions belong here, because they are authoring decisions.

**Prefer the added world's tools over direct SQL on its store.** `ctx.worlds.payments.db` is a full
read-write `Db`, and it is there because a framework cannot predict every use. But a write through
it bypasses that world's handlers and therefore its invariants: clock stamps, id streams, audit
rows, FTS triggers. Call the tool where a tool exists, even when you have hidden it from the agent
with a `tool_allow_list`; host code can call every tool of a world it adds. Keep raw SQL for what no
tool covers, and expect the rows it writes to be missing whatever the world's own code would have
written alongside them.

**Declare prefixes and lists to match the client's real surface.** A composite has no single real
product to be faithful to, because the real client's agent surface is an assembly too. So
faithfulness splits: each added world is faithful to its own vendor, and you are faithful to what
the client's agent actually sees — your own tools, your composite tools, and the names, the
filtering and the *order* you declare for the added worlds. A prefix nobody's client uses, or a tool
left visible that the real integration never exposes, is your infidelity rather than the added
world's.

**Never assume you are the only writer to a world you add.** Sharing is the default: two worlds that
add the same package land on the same store, exactly as two integrations share one real account. A
tool that assumes every row in that store came from its own calls — a counter it believes it owns, a
"the last charge is mine" read — is wrong the first time a sibling writes there. Query for what you
put in, by id.

**Return models, not bare dicts.** An in-process call returns the tool's own object, so a tool
answering with a pydantic model hands its caller that model, with its fields completing and its
types checked. A tool answering with `dict[str, object]` hands over something a caller has to index
blind. Models cost nothing over the wire, because the OpenEnv layer serialises either.

## The schema files

The world's tables are declared as ordinary SQLite `CREATE TABLE` statements in `.sql` files under
`schema/`, applied in filename order, which is what the `001_`, `002_` prefixes are for.
`seahaven.sql_files(__package__, "schema")` reads them.
[db_schema_and_fixtures.md](db_schema_and_fixtures.md) is the full page: the three rules `seahaven
check` enforces, the timestamp format, and the schema hash.

Seahaven owns the connection setup — write-ahead logging, foreign keys, defensive mode, the clock
and random overrides — so world code never sets a pragma.

### Full-text search

**FTS5** is supported as far as a world's own search tool needs. A virtual table and its sync
triggers are allowed in the schema and are exempt from the `STRICT` and primary-key rules. FTS5's
shadow tables are known to Seahaven and stay out of the change log, out of the freeze comparison,
and out of `run_sql`'s default allowlist. Write the search tool in plain SQL with `MATCH`, `bm25()`
and `snippet()`. The ranking is FTS5's, which is not Lucene's, so an eval that grades on "the best
result" is grading on what this SQLite build computes.

A world's own search tool is the usual path, and ProjectTracker's `search_issues` is the pattern.
`MATCH` through `seahaven.helpers.run_sql` is possible too, and all it takes is listing what the
query touches. Nothing is inferred from a table's name, so the virtual table **and its shadow
tables** have to be in `tables`, and FTS5's functions in `functions`. A door that lists the virtual
table alone is refused with `read of table '<name>_idx'`, which is the shape of that mistake.

```python
import seahaven

world = seahaven.World(
    name="notes",
    version="1.0.0",
    schema="""
    CREATE TABLE notes (id TEXT PRIMARY KEY, body TEXT NOT NULL) STRICT;

    CREATE VIRTUAL TABLE notes_fts USING fts5 (body, content = 'notes', content_rowid = 'rowid');

    CREATE TRIGGER notes_fts_after_insert AFTER INSERT ON notes BEGIN
        INSERT INTO notes_fts (rowid, body) VALUES (new.rowid, new.body);
    END;
    """,
    state_format="seahaven.state/1",
)

world.tool(
    seahaven.helpers.run_sql(
        tables=[
            "notes",
            # The virtual table, then the shadow tables FTS5 keeps for it. Which
            # ones exist depends on `content=` and `columnsize=`, so list the ones
            # the table actually has.
            "notes_fts",
            "notes_fts_data",
            "notes_fts_idx",
            "notes_fts_docsize",
            "notes_fts_config",
        ],
        # `match` is what SQLite calls the MATCH operator.
        functions=("bm25", "snippet", "highlight", "match"),
    )
)

with world.instance() as inst:
    with inst.bulk() as ctx:
        ctx.db.execute("INSERT INTO notes (id, body) VALUES ('n1', 'the export is slow')")
        ctx.db.execute("INSERT INTO notes (id, body) VALUES ('n2', 'dark mode please')")

    found = inst.call(
        "run_sql",
        query="SELECT id FROM notes JOIN notes_fts ON notes.rowid = notes_fts.rowid"
        " WHERE notes_fts MATCH 'export' ORDER BY rank",
    )
    assert found["rows"] == [["n1"]]
```

Opening that door is a decision rather than a default. It is a second way to do what the world's
search tool already does, and an agent that can query the index can also read every document in it.
ProjectTracker deliberately leaves `issues_fts` out of its SQL door for that reason.

### Changing the schema costs every fixture

The schema hash is recorded in every fixture's sidecar, and an instance refuses a fixture whose hash
does not match the loaded world. Adding a column means regenerating every fixture. That is a command
rather than a project, because every fixture is committed together with the script that generates
it. See [db_schema_and_fixtures.md](db_schema_and_fixtures.md).

## Things that go wrong quietly

**Ordering within one run.** The clock does not move, so every row one run writes carries the same
`created_at`, and ordering by it is not an order. For raw SQL the answer is `ORDER BY created_at,
rowid`. For a *tool* there is no complete answer: a keyset cursor has to carry its tiebreaker as a
value, `rowid` is not a column a world projects, and `ctx` offers no monotonic per-instance counter
to page on instead. Order by `(created_at, id)`, say so in the tool's docstring, and grade evals on
the state document rather than on the order of an activity feed.

**Lists without a tiebreak.** `ORDER BY created_at DESC` over rows that share an instant is not
deterministic. Always order by a column *and* by the id.

**Time and randomness from outside `ctx`.** `datetime.now()` and `uuid.uuid4()` work, and dated
fixture-relative data taken from the machine's clock is nearly always a mistake. `seahaven check`
warns (`SH201`, `SH203`) rather than refusing, because it is occasionally deliberate. Inside
`middleware/`, where a real clock timing a real call is exactly right, `SH201` does not fire at all.

**A module nobody imports.** A tool module under `tools/` that the package `__init__` does not
import registers nothing, and the symptom is an `unknown_tool` from an eval weeks later. `seahaven
check` reports it as `SH301`, an error.

**An empty description.** The description is what an agent reads to decide whether to call the tool.
`SH205` warns about an empty one.

Run `seahaven check` before every commit. Every rule it has is in
[reference/lints.md](reference/lints.md), with why it exists and what to change.
