# API reference

The public API is the names exported from `seahaven`, the `seahaven.helpers`, `seahaven.sandbox` and
`seahaven.fixtures` modules, the type aliases in `seahaven.world`, `seahaven.openenv` (in the
`serve` extra), and the pytest plugin's two fixtures. A name that is not below is internal and may
change without notice.

| Section | What it covers |
|---|---|
| [`World`](#world) | The declaration, and `add_world` |
| [`Instance`](#instance) | What `world.instance(...)` returns |
| [`Ctx`](#ctx) | What every tool receives |
| [`Worlds` and `WorldHandle`](#worlds-and-worldhandle) | Reaching an added world |
| [`Db`](#db), [`Clock`](#clock), [`Ids`](#ids) | The database, the time and the seeded stream |
| [`Call`](#call), [`Tool`](#tool) | One call as dispatch sees it, and one registered tool |
| [`LogRecord`](#logrecord), [`CallRecord`](#callrecord) | One row a call changed, and one call the instance was given |
| [The composition report](#the-composition-report) | The stores one instance holds |
| [`Fixture`](#fixture), [`seahaven.fixtures`](#seahavenfixtures) | Fixtures, and the module that reads them |
| [Errors](#errors) | The exception hierarchy |
| [`seahaven.helpers`](#seahavenhelpers) | `run_sql` and `describe_schema` |
| [`seahaven.sandbox`](#seahavensandbox) | Agent SQL containment |
| [`seahaven.openenv`](#seahavenopenenv-the-serve-extra) | The server and the client |
| [The pytest plugin](#the-pytest-plugin) | Two fixtures, one marker, one option |
| [The concurrency gate](#the-concurrency-gate) | `set_concurrency` and friends |
| [Middleware and hook types](#middleware-and-hook-types) | `Handler`, `Middleware` and `StartupHook` |

Ten names on this page are not exported from `seahaven/__init__.py`, and there are two reasons for
that:

- Seven of them are public by the repository's own rule. `seahaven.world.Handler`, `Middleware` and
  `StartupHook` are the type aliases the scaffolded error handler imports, and
  `seahaven.fixtures.load`, `load_all`, `verify` and `freeze` read a fixture directory. Both sets
  are part of their module's stated interface, in `components/world_and_dispatch.md` §1 and
  `components/fixtures_instances.md` §1. `architecture.md` §1 makes that the rule: a name the
  component document lists as part of a module's interface is public, and `seahaven/__init__.py`
  re-exports only the subset worth a short import.
- The other three are `seahaven.instances.default_concurrency`, `concurrency` and `set_concurrency`,
  the concurrency gate. No component document lists them, so **this page declares them public on its
  own authority**. The gate is on in every process, and `serve --concurrency` is otherwise the only
  documented way to change it, which leaves an in-process harness with a real control and no name
  for it. That goes further than the specification, and this page says so rather than implying the
  rule above covers it.

```py
import seahaven

seahaven.World, seahaven.Instance, seahaven.Ctx, seahaven.Tool, seahaven.Call
seahaven.Worlds, seahaven.WorldHandle
seahaven.Db, seahaven.Clock, seahaven.Ids, seahaven.LogRecord, seahaven.CallRecord
seahaven.Fixture
seahaven.SeahavenError, seahaven.WorldBug
seahaven.ToolError, seahaven.ArgumentError, seahaven.DbError, seahaven.UnknownTool
seahaven.sql_files, seahaven.helpers, seahaven.sandbox
seahaven.__version__
```

## `World`

```py
class World:
    def __init__(
        self,
        name: str,
        version: str,
        schema: str,
        *,
        state_format: str | None = None,
        description: str | None = None,
        fixtures_dir: Path | str | None = None,
        work_dir: Path | str | None = None,
        untracked_tables: Sequence[str] = (),
    ) -> None: ...
    def add_world(
        self,
        world: World,
        /,
        *,
        name: str | None = None,
        store: str | None = None,
        tool_prefix: str | None = None,
        tool_allow_list: Sequence[str] | None = None,
        tool_block_list: Sequence[str] | None = None,
        startup: Mapping[str, Any] | None = None,
    ) -> None: ...
```

One per world package, built at import in `world.py`. `schema` is the world's `CREATE TABLE`
statements as one string, usually from `sql_files`. `name` and `version` are informational and
appear in the OpenEnv metadata and in every fixture's sidecar. `state_format` is required: it is
the format `inst.state()` answers in, and a world constructed without one is refused with the
built-in names in the message. `description` is the one-line description the OpenEnv metadata
publishes. It is a free string, unvalidated, and the only thing that sets that line; a world that
gives none, or gives a blank string, publishes `Seahaven world <name>`. `fixtures_dir` defaults to
`fixtures/` at the project root, found by walking up from the
constructing module to the directory holding `pyproject.toml`, and to `fixtures/` beside the package
where there is none. `work_dir` is where instance copies are kept; the default is a per-process
directory under the system temp directory, which is swept of previous processes' leftovers, and a
directory you name is used exactly as given and never swept. `untracked_tables` names tables the
change log's sessions do not attach.

A `World` whose schema does not execute cannot be constructed: the schema is built in memory to
compute the schema hash, and SQLite's own message is reported.

Nor can one be constructed whose `name` is not a single directory name that every platform carries
unchanged. The name is a path component of the default working directory, and it travels with every
fixture the world freezes. The rule is 1 to 128 characters of letters, digits, space, `.`, `-` and
`_`, not starting or ending with a space or a dot, and not a name Windows reserves for a device
(`con`, `prn`, `aux`, `nul`, `com1`–`com9`, `lpt1`–`lpt9`, with any extension). Anything else is
refused with `not a world name` and the clause it broke: an empty name, a separator, a drive letter,
a NUL, an accent, a non-Latin script. A **fixture id** follows the same rule, and is refused with
`not a fixture id`.

| Member | What it is |
|---|---|
| `world.tool(obj=None, *, name=None, description=None, transaction=None)` | register a tool, as a decorator or a call. Passing a built `Tool` together with any of the three keywords is refused, because the factory that built it decided them |
| `world.middleware(obj=None)` | register a middleware, as a decorator or a call. Order is registration order, outermost first |
| `world.instance_startup(obj=None)` | register a startup hook, as a decorator or a call |
| `world.add_world(other, *, name=None, store=None, tool_prefix=None, tool_allow_list=None, tool_block_list=None, startup=None)` | add another world: its tools join this world's surface, its store becomes a node of every instance. Call it; there is nothing to decorate |
| `world.state_format(name)` | register a state format of this world's own, as a decorator. The name is `<family>/<major>` and may not begin with `seahaven.` |
| `world.resolve_state_format(name)` | the formatter a name answers to: a built-in, or one this world registered. Asked of the root of an instance and of nothing else |
| `world.instance(fixture=None, *, seed=None, now=None, state_format=None, **startup_kwargs)` | make an instance; a context manager. `state_format` answers in another of this world's formats, in place of the pin |
| `world.fixtures()` | every fixture in the fixtures directory, as a list sorted by id. A world with no fixtures directory has none, which is not an error |
| `copy.copy(world)` | this world with the same registrations and its own instances: set `fixtures_dir` on the copy to freeze somewhere else without moving the imported world's |
| `world.tools` | the registry, in registration order. Read-only, and this world's **own** tools: the composite surface an agent sees is `inst.tools()`, or `world.composition().tools` |
| `world.tools_by_fn` | the registry by the function each tool was built from, multi-valued because one function may be registered as two tools. This world's own tools only, contributed or not. The control tool is not in it |
| `world.middlewares` | the middleware, outermost first |
| `world.startup_hooks` | the hooks, in registration order |
| `world.accepted_startup_kwargs` | every keyword some hook names |
| `world.added_worlds` | what `add_world` recorded, in registration order. Read-only |
| `world.composition()` | the sealed tree: its nodes, their paths and the flat tool surface. Sealed lazily and cached until the next registration anywhere in the process |
| `world.name`, `world.version`, `world.description`, `world.schema`, `world.schema_hash`, `world.fixtures_dir`, `world.pinned_state_format` | as given, plus the hash of the normalised schema |

Registration validates immediately and raises `WorldBug`; the full list of what is refused is in
[../authoring.md](../authoring.md).

### `add_world`

| Parameter | What | Default |
|---|---|---|
| first positional | the added world's `World` object, normally the `world` its package exports | required |
| `name` | this host's internal identity for it, used as a path segment, a file name and a schema name. `^[a-z][a-z0-9_]*$`, no `__`, not `main` or `temp`. Never agent-visible | the added world's own `name` |
| `store` | the account scope this world **and its whole subtree** belong to. `None` keeps the adder's scope, which is what shares a store | `None` |
| `tool_prefix` | prepended to every contributed tool name | none |
| `tool_allow_list` | only these tools are contributed to the agent's surface, named as the added world contributes them: after any prefix applied inside its own subtree, before this `tool_prefix` | all |
| `tool_block_list` | every tool but these. Mutually exclusive with the allow list | none |
| `startup` | keyword arguments bound to that node's startup hooks, not overridable by a `reset()` keyword of the same name | none |

What is checked at the call is what the two `World` objects know: the name, the lists, the scope
name, the `startup` keywords against the added world's own hooks, and the absence of a cycle.
Everything that is a property of the whole tree is checked at the first use of it. See
[../composition.md](../composition.md), and `SH504` in [lints.md](lints.md).

### `sql_files`

```py
def sql_files(package: str | None, directory: str) -> str: ...
```

Every `*.sql` in a package directory, in sorted filename order, joined with newlines. Sorted order
is what the `001_`, `002_` prefix convention is for. The files are read through
`importlib.resources`, so a world works the same from a checkout, an installed wheel or a zip.
`seahaven.sql_files(__package__, "schema")` is the spelling. `__name__` is a module inside the
package and is refused, as is a directory that leaves the package.

## `Instance`

Made by `world.instance(...)`, never by hand. A context manager; leaving the block destroys it.

| Member | What it is |
|---|---|
| `inst.call(name, /, **arguments)` | run one tool: validation, the middleware chain, the tool, on the calling thread, under the instance's lock. Raises the world's `ToolError` subclasses |
| `inst.call(fn, /, *args, **arguments)` | the same call, named by the tool's own function: the arguments are checked by a type checker and the result is the tool's own object. Resolves over the composite surface, so it reaches a tool of any world this one adds |
| `inst.tools()` | the tool list with JSON schemas, as `{"name", "description", "input_schema"}`. One flat list over every world this one adds. Control tools are never in it |
| `inst.inspect()` | a read-only `Db` on a second connection: every table, the instance clock, opened once and kept. Every added world's store is attached read-only under its path. Never a tool |
| `inst.change_log()` | every row this instance has changed, one record per row per call, in call order, as `list[LogRecord]`, covering every store |
| `inst.call_log()` | every call dispatched to this instance, in dispatch order, as `list[CallRecord]`, so `[i]` is call `i` |
| `inst.call_count` | how many calls have been dispatched: `len(inst.call_log())` |
| `inst.state(format=None)` | the state document as a plain dict: the envelope, and `state` from this instance's format. `format` answers in another of the world's formats instead. Refused inside `bulk()` or a tool call |
| `inst.composition()` | what this instance is running against: one `NodeReport` per store, root first |
| `inst.freeze(id, description)` | mint a fixture from the current state; returns the `Fixture`. Refuses inside `bulk()` |
| `inst.bulk()` | a context manager yielding the instance's own `Ctx`, in one transaction, for loading rows fast |
| `inst.destroy()` | close everything and remove the working directory. Idempotent, and waits for a call in flight |
| `inst.id`, `inst.fixture`, `inst.seed` | the instance id, the fixture id (or `None`), the derived seed bytes |
| `inst.state_format` | the format `inst.state()` answers in, fixed for the instance's life |
| `inst.clock`, `inst.world`, `inst.state_path` | the clock, the world, and the instance's own database file |

The tool name is positional-only, so a world may have a tool argument called `name`.

## `Ctx`

What every tool, middleware and startup hook receives. Nothing else is exposed: no working
directory, no other instance, no process.

| Member | What it is |
|---|---|
| `ctx.db` | the `Db` for this instance |
| `ctx.clock` | the `Clock` |
| `ctx.ids` | the `Ids` |
| `ctx.state` | a `dict` that lives as long as the instance |
| `ctx.call` | the current `Call`, or `None` outside one (a startup hook, `bulk()`) |
| `ctx.instance` | `id`, `fixture` and `seed`, read-only |
| `ctx.worlds` | the `Worlds` this node adds, by name. For a world that adds none it answers nothing: every name on it is a `WorldBug` |

`Ctx` is generic in what `ctx.worlds` is. Almost every tool writes a bare `seahaven.Ctx` annotation,
and `seahaven.Ctx[CompanyWorlds]` opts into a type checker knowing the child names (below). The
parameter is annotation-only, nothing in the runtime reads it, and the registration check accepts
either.

## `Worlds` and `WorldHandle`

`ctx.worlds` is a `Worlds`: attribute access, `ctx.worlds.payments`, and item access,
`ctx.worlds["payments"]`, both answering the `WorldHandle` for that child. A name no `add_world`
registered is a `WorldBug`.

| Handle member | What it is |
|---|---|
| `handle.call(name, /, **arguments)` | run one tool of that node, by the added world's **own** unprefixed name. Neither list filters this: host code can call every tool of a world it adds |
| `handle.call(fn, /, *args, **arguments)` | the same, named by the function, resolved over that handle's whole subtree |
| `handle.db` | that node's `Db`, `conn` included |
| `handle.state` | that node's own `ctx.state` dict |
| `handle.worlds` | that node's own children |

A handle belongs to one **activation** of the instance: the outermost call, or the `bulk()` block,
that was running when the handle was made. Every member raises `WorldBug` after that activation
ends, so a handle is not a reference to keep.

`Worlds` is also the base a world subclasses to declare its children to a type checker. The subclass
carries one `<name>: seahaven.WorldHandle` annotation per child, is never instantiated, and is named
in a `Ctx[...]`. `seahaven check` binds it to the registrations (`SH502`, `SH503`). The worked
example is in [../composition.md](../composition.md), which is the page for all of this.

## `Db`

```py
class Db:
    def one(self, sql, *params): ...  # the first row as a dict, or None
    def rows(self, sql, *params): ...  # every row, each a dict keyed by column name
    def execute(self, sql, *params): ...  # for effect; returns Exec(rowcount, last_rowid)
    def executemany(self, sql, rows): ...  # one statement per row of bindings; rows changed
    def transaction(self): ...  # BEGIN at the top level, SAVEPOINT when nested

    conn: apsw.Connection  # the raw connection
    in_transaction: bool  # whether one is open
```

Parameters are positional `?` bindings. Every SQLite failure arrives as `seahaven.DbError`, so world
code catches one type.

`Exec.last_rowid` is `sqlite3_last_insert_rowid` as SQLite reports it: it belongs to the connection
rather than the statement, so read it straight after an `INSERT`.

`db.conn` is there for what the wrapper does not cover, such as blob I/O or an exec trace. The
invariants: **do not close it, change its pragmas or its authorizer, or open a second connection to
the instance file.** The clock functions, the change log's per-call session and the per-call
transaction all use that one connection.

## `Clock`

```py
class Clock:
    def now(self): ...  # an aware UTC datetime
    def iso(self): ...  # the canonical text: 2026-06-01T09:00:00.000Z
```

Static for the life of the instance. Every connection overrides SQLite's `current_timestamp`,
`current_date`, `current_time` and the `'now'` argument of `datetime`, `date`, `time`, `strftime`,
`julianday`, `unixepoch` and `timediff` to return it, so SQL sees the same instant world code does.
The overrides are registered as innocuous, so schema objects may reference them. What they return
compares and sorts correctly against the canonical text a world stores.

## `Ids`

```py
class Ids:
    def uuid(self): ...  # a UUIDv4-shaped identifier from the seeded stream

    random: random.Random  # seeded per instance
```

Product-shaped keys are the world's own business. `ENG-13` and a sequential invoice number are built
on `ids.random` or on the world's own tables, and this is the stream they draw from.

SQLite's `random()` and `randomblob()` are overridden on every connection an instance opens, each
from a stream of its own derived from the instance seed, so SQL replays as `ctx.ids` does. No two
doors onto one instance hand out the same values, and neither does `ctx.ids`. The overrides are
registered as innocuous, so a `DEFAULT` clause and a trigger may call them, and deliberately not as
deterministic, so SQLite asks them for every call.

## `Call`

```py
call.name  # the name the caller asked for
call.arguments  # raw until validation runs inside the chain, validated after
call.tool  # the Tool that was found
call.node  # the canonical path of the node that owns the tool; "main" for a world that adds none
call.with_arguments(**changes)  # a copy with changes merged over the arguments
```

A `Call` is frozen. A middleware that wants typed arguments before validation calls
`call.tool.validate(call.arguments)` itself. The model is built once at registration, so that is
cheap.

## `Tool`

```py
class Tool:
    @classmethod
    def from_function(cls, fn, *, name=None, description=None, transaction=True): ...
    def validate(self, arguments): ...  # the arguments as the parameters, or ArgumentError
    def listing(self): ...  # {"name", "description", "input_schema"}

    name: str
    description: str
    fn: Callable[..., Any]
    params: type[pydantic.BaseModel]
    schema: dict[str, Any]
    transaction: bool
```

`from_function` is what a tool factory builds its tool with, whether that factory is one of
Seahaven's helpers or an extension's. `control` is Seahaven's own flag for its control tool and
cannot be set through it.

## `LogRecord`

```py
record.i  # the ordinal of the call that changed the row, or None for an inst.bulk() write
record.world  # the store: the node's path, "main" for the root
record.table  # the table
record.op  # "insert" | "update" | "delete"
record.key  # the row's primary key columns, as a dict
record.before, record.after  # row dicts, or None where not applicable
record.to_dict()  # the published shape an eval reads, with "i" first
```

`world` is `main` on every record of a world that adds none, and the owning node's canonical path
otherwise. That is what tells two tables of the same name in two stores apart
([../composition.md](../composition.md)).

An insert and a delete carry the whole row in `after` and `before`. An update carries exactly the
non-key columns the call changed, old values in `before` and new in `after`, and never the key,
which `key` already holds. Flattening the two would turn "changed the assignee" into "rewrote the
row".

## `CallRecord`

```py
call_record.tool  # the tool's name as the caller gave it
call_record.arguments  # a copy of the arguments the call carried, never re-serialised
call_record.error  # the message of whatever the call raised, or None
call_record.tool_error  # whether that was a ToolError: an error the world wrote for the agent
call_record.to_dict()  # the published shape, as `seahaven.state+calls/1` writes it
```

`error` is the real message whichever class of error it was, because an author debugging their own
world reads it. `to_dict()` is the boundary: it publishes a `ToolError`'s message as written, and
anything else — a `WorldBug`, a Python exception the world did not plan for — as `internal error`
([../state.md](../state.md#reconcile-a-trace-the-document-cannot-see-seahavenstatecalls1)).

`inst.call_log()` answers these in dispatch order, so `[i]` is the call a log record's `i` names.
Under composition `tool` is the name on the root's tool surface, prefix included, rather than the
owning world's own name for it. `arguments` is copied when the call is made, so a tool that changes
the dict it was handed does not change the record, and it is never re-serialised: an in-process
caller who passed something JSON cannot carry gets it back as it was. Only dispatched calls are in
the log: a name the world refused, a tool listing and a control tool are not calls.

## The composition report

`inst.composition()` returns one record per store, root first, in `seahaven.composition`:

```py
report.path  # the node's canonical path, "main" for the root
report.world, report.world_version  # the world there, at the version installed
report.scope  # the account scope it resolved into, or None for the unnamed one
report.aliases  # every other route that reaches it, as "<parent path>/<name>"
report.schema_hash  # that world's schema hash
report.frozen_world_version  # what the fixture recorded, when that is not what is installed
```

It describes the stores this instance holds, which is the set it was created with rather than
whatever the world's seal says now. `frozen_world_version` is `None` for a blank instance, and also
wherever the fixture and the installed world agree. A version difference under a matching schema
hash is reported, never refused. The state document carries the same records as `composition`, keyed
by `path` rather than listed ([../state.md](../state.md)).

## `Fixture`

```py
fixture.id, fixture.now, fixture.parent_id, fixture.description, fixture.state_path
```

What `world.fixtures()` and `inst.freeze(...)` return.

## `seahaven.fixtures`

```py
from seahaven import fixtures


def load(fixture_dir: Path) -> Fixture: ...
def load_all(fixtures_dir: Path) -> dict[str, Fixture]: ...
def verify(fixture: Fixture) -> None: ...
def freeze(
    instance: Instance, fixture_id: str, description: str, *, fixtures_dir: Path
) -> Fixture: ...
```

These read a fixture directory directly, for tooling that works on fixtures rather than on a world.
`load` reads one sidecar without opening the state file. `load_all` returns every fixture in a
directory, keyed by id; an empty directory is not an error, dot-directories are skipped (including a
`.pending-*` freeze in flight), and two fixtures claiming one id is an error. `verify` raises unless
every state file is a real file in the fixture's own directory, rather than a symbolic link of any
kind, and is the one its sidecar's `file_sha256` describes. `freeze` is what `Instance.freeze`
delegates to.

A world does not need any of them. `world.fixtures()`, `world.instance(id)` and `inst.freeze(...)`
are the ordinary path, and `world.instance(id)` verifies for you. Listing deliberately does not
verify: `world.fixtures()` is `load_all`, and `load` never opens the state file, so a fixture whose
`state.sqlite` was modified is listed without complaint and is refused when an instance is made from
it. Reach for the module when you are checking a fixture directory in a test or a script, as the
reference world's own `tests/test_fixtures.py` does. A malformed sidecar, an unknown
`format_version`, a duplicate id and a modified state file are each a `WorldBug` naming the file.

## Errors

```
SeahavenError
├── WorldBug                  the author's mistake; never shown to an agent
└── ToolError(code, message, details=None)
    ├── ArgumentError         .violations: every problem with the arguments
    ├── DbError               .sqlite_message, .sqlite_code, .refusals
    └── UnknownTool           .details["name"]
```

`ToolError.to_dict()` is `{"code", "message", "details"}`, the same shape in process and over the
wire, where it travels in the observation's `metadata["seahaven_error"]`. Seahaven's own three codes
are `invalid_arguments`, `db_error` and `unknown_tool`. A world's codes are its own.

`ToolError.error_type` is the framework's coarse category for the same failure, published beside the
message as OpenEnv's `error_type`. It is `tool_not_found` on `UnknownTool`, `invalid_args` on
`ArgumentError` and `execution_error` on everything else, including every error a world defines. A
world does not set it: a subclass that declares its own `error_type` is refused with a `WorldBug`
where it is written.
([../serving_and_openenv.md](../serving_and_openenv.md#calls-results-and-errors) says why).

`DbError` carries SQLite's text but never puts it in the agent-facing message by itself. A world's
SQL door is where engine text is the right answer, and the `run_sql` tool puts SQLite's message on
the error it raises before the error handler ever sees it. Everywhere else, `error.sqlite_message`
is there for a handler that wants to log it.

## `seahaven.helpers`

```py
def run_sql(
    *,
    name: str = "run_sql",
    tables: Sequence[str],
    read_only: bool = True,
    max_rows: int | None = None,
    max_bytes: int | None = None,
    functions: Sequence[str] = (),
    description: str | None = None,
) -> Tool: ...
```

A tool taking one `query` string in the SQLite dialect and returning `{"columns": [...], "rows":
[[...]], "row_count": n, "truncated": bool}`. Exactly one statement per call.

Seahaven owns the containment. A SQLite authorizer allows the listed tables plus `sqlite_master`,
`sqlite_schema`, `json_each` and `json_tree`, and **denies** everything else. It denies rather than
ignores. `ATTACH`, `PRAGMA`, `load_extension` and schema changes are refused whatever `read_only`
says, and with `read_only=True` every write is refused as well. A statement-level check backs the
authorizer, and a fixed, non-configurable cap on the size of a single SQL value stops a query
materialising an enormous one. `read_only=False` allows writes to the listed tables, which commit
with the call like any other tool's.

`max_rows` and `max_bytes` are the world's truncation policy, for a product that truncates; unset
means the whole result. `functions` allows SQLite functions beyond the default list. `random()` and
`randomblob()` are on that list: an agent may draw from them because what it draws is the instance's
seeded stream, not the host's entropy.

Nothing is inferred from a name. An FTS5 virtual table and its shadow tables are denied like any
other table the world did not list, and allowed when it does list them. So full-text `MATCH` does
not work through a door that lists only the world's ordinary tables, which is the default and the
reason a world's own search tool is the usual path. A world that wants `MATCH` here lists the
virtual table **and its shadow tables**, and adds the search functions. The recipe, with a worked
example, is in [../authoring.md](../authoring.md).

```py
def describe_schema(
    *, name: str = "describe_schema", tables: Sequence[str], description: str | None = None
) -> Tool: ...
```

No arguments, writes nothing, and returns, from the live schema: `{"tables": [{"name", "columns":
[{"name", "type", "nullable", "primary_key"}], "foreign_keys": [{"columns", "references_table",
"references_columns"}]}]}`. Register it beside any SQL door, so that an agent can read the tables
before it queries them.

## `seahaven.sandbox`

These names are public so that an extension serving another SQL dialect can run its translated
statement through the same containment Seahaven's own helper uses.

```py
class Authorizer:
    def __init__(self, tables, *, read_only=True, functions=ALLOWED_FUNCTIONS) -> None: ...


def run_statement(
    db, sql, params=(), *, authorizer, max_rows=None, max_bytes=None
) -> SqlResult: ...


class SqlResult:
    def __init__(self, columns, rows, truncated) -> None: ...

    row_count: int


def refusal_kind(refusal) -> Refusal: ...  # "read" | "write" | "function" | "action" | ...


REFUSALS: frozenset[str]
ALLOWED_FUNCTIONS: frozenset[str]
MAX_VALUE_BYTES: int
```

A refusal is classified by what the authorizer recorded, not by the message text. SQLite reports its
own refusals inconsistently: a denied table read raises `apsw.AuthError` and a denied function
raises `apsw.SQLError`, and the two are siblings in APSW's flat hierarchy.

## `seahaven.openenv` (the `serve` extra)

```py
def app(
    world, *, include_control_tools=False, max_concurrent_envs=500, session_timeout=3600.0
) -> FastAPI: ...


class SeahavenClient:  # .reset(...), .call(tool, /, **arguments), .list_tools(), .state()
    ...


# Also exported: SeahavenEnv, SeahavenObservation, SeahavenState, its four nested models
# WorldRef, NodeRef, FixtureRef and FileRef, and OpenEnv's own CallToolAction,
# ListToolsAction and ListToolsObservation.
```

`SeahavenClient.call(...)` answers a `SeahavenObservation`. On a failed call its `error` is
OpenEnv's own `{error_type, message}` model and its `seahaven_error` is the world's
`{code, message, details}` triple, read from `metadata["seahaven_error"]`.

`SeahavenClient.state()` answers a `SeahavenState`: the state document, plus OpenEnv's
`step_count`. Every envelope field of the document is a typed field on it, with `world` a
`WorldRef`, `composition` a `dict[str, NodeRef]`, `fixture` a `FixtureRef` whose `nodes` is a
`dict[str, FileRef]`, and `state` a `dict[str, Any]` because its shape is the format's.
`state().model_dump(exclude={"step_count"})` is the document as `inst.state()` answers it in
process.

See [../serving_and_openenv.md](../serving_and_openenv.md) and [../state.md](../state.md).

## The pytest plugin

Installing `seahaven` activates the plugin. It adds two fixtures, `world` (session-scoped) and
`instance` (one per test); one marker, `@pytest.mark.seahaven(fixture, seed=None, now=None,
**startup_kwargs)`; and one option, `--seahaven-world module:attr`. See
[../testing.md](../testing.md).

## The concurrency gate

```py
def default_concurrency() -> int: ...  # min(cpus, 16), following CPU affinity
def concurrency() -> int: ...  # the size in force, or 0 for no gate
def set_concurrency(size: int) -> None: ...  # resize it; 0 removes it
```

These are in `seahaven.instances` rather than on the package root, and this page documents them on
its own authority (see the top). `serve --concurrency` is `set_concurrency`, and an in-process
harness that drives many instances on threads has the same gate and the same control. The gate is
process-wide and on by default. A resize does not affect calls already running, and nothing is ever
rejected. Read the gate's section in [../serving_and_openenv.md](../serving_and_openenv.md) before
changing it: the gate is unfair whenever it binds, and that is a known defect rather than a tuning
question.

## Middleware and hook types

```py
from seahaven.world import Handler, Middleware, StartupHook
```

`Handler` is `(ctx, call) -> Any`, which is what the rest of the chain looks like from inside a
middleware. `Middleware` is `(ctx, call, next_) -> Any`. `StartupHook` is `(ctx, **kwargs) -> None`.
They are type aliases for annotating your own code. Nothing subclasses them, and the shape is
checked structurally at registration.
