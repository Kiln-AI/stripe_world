---
status: complete
---

# Component: world definition and dispatch

Modules: `seahaven/world.py`, `tool.py`, `call.py`, `ctx.py`, `errors.py`. What a world author
touches, and the path a call takes from name and arguments to a tool function and back. Tools are
registered on a `World` with typed parameters, and their JSON schemas are derived from those
parameters rather than hand-written.

## 1. `world.py`

```python
class World:
    def __init__(self, name: str, version: str, schema: str, *,
                 description: str | None = None,
                 fixtures_dir: Path | str | None = None,
                 work_dir: Path | str | None = None,
                 untracked_tables: Sequence[str] = ()) -> None
    name: str; version: str; schema: str; schema_hash: str
    description: str | None                   # the OpenEnv metadata's one line; free text, unvalidated
    fixtures_dir: Path; work_dir: Path | None; untracked_tables: tuple[str, ...]
    tools: Mapping[str, Tool]                 # read-only view, insertion ordered, control tools included
    middlewares: Sequence[Middleware]
    startup_hooks: Sequence[StartupHook]

    # registration: decorator or call
    def tool(self, obj: Callable | Tool | None = None, /, *, name: str | None = None,
             description: str | None = None, transaction: bool = True) -> Any
    def middleware(self, obj: Middleware | None = None, /) -> Any
    def instance_startup(self, obj: StartupHook | None = None, /) -> Any

    # runtime
    def instance(self, fixture: str | None, *, seed: int | bytes | None = None,
                 now: str | datetime | None = None,
                 **startup_kwargs: Any) -> Instance
    def fixtures(self) -> list[Fixture]
    def __copy__(self) -> World                 # `copy.copy(world)`: same registrations, its own instances
    chain: Handler                              # the middleware chain, rebuilt on each middleware registration

type Handler = Callable[[Ctx, Call], Any]
type Middleware = Callable[[Ctx, Call, Handler], Any]
type StartupHook = Callable[..., None]
```

### 1.1 Construction

1. `schema_hash = sha256(re.sub(r"\s+", " ", schema).strip())`.
2. `build_blank(":memory:", schema)` to prove the DDL executes and to have a schema to hash.
   Construction fails if the DDL does not execute (the SQLite error is the message). No lint runs
   here: the DDL rules belong to `seahaven check`.
3. `fixtures_dir`: explicit path, else derived. Derivation: the module that called `World(...)` is
   found from the call stack (`sys._getframe(1).f_globals["__file__"]`); walk up from that file's
   directory until a directory containing `pyproject.toml` is found; `fixtures/` beneath it. If no
   `pyproject.toml` is found — which is the case for an installed wheel, whose module sits in
   `site-packages` — `fixtures/` beside the package directory. Construction never fails on this:
   a world that only makes blank instances never reads the directory, and a fixture that cannot be
   found names `World(fixtures_dir=...)` in its message. Cached on the instance.
4. Control tools (`controller_run_sql`, `controller_changes`) are registered immediately with
   `control=True`.
5. `description`: kept exactly as given, and `None` when it is not given. It is the one-line
   description the OpenEnv metadata publishes (`components/openenv.md` §2) and nothing else reads
   it. Deliberately unvalidated: unlike `name`, which becomes a path component, it is free text
   with nothing for a rule to protect, and `None` and any blank string alike mean the fallback at
   publication.

### 1.1.1 Copying

`copy.copy(world)` is how a caller says "this world, writing somewhere else": the copy carries the
identity, the schema and copies of the three registries, and `fixtures_dir` is then set on it. It
is what a world's fixture test builds through, so that rebuilding the committed fixtures into a
temporary directory does not move the imported world's directory for every other caller in the
process (`fixtures_src/generate.py`'s `build(fixture_id, *, world=...)`).

The three registries are copied and not shared: a copy is a **snapshot of them taken at copy
time**, severed in both directions. Nothing registered on the copy reaches back into the original,
and nothing registered on the original afterwards reaches the copy — whose `chain` stays as it was
at the copy. It is the one place a world stops being open for registration for the life of the
process (§1.3), so a copy is taken after import-time registration is complete. The instance manager
is **not** carried over: a manager hands the world it was made for to every instance it creates,
and that world is the one an instance freezes into — a copy that inherited one would freeze back
into the directory it was copied from, silently.

`copy.deepcopy(world)` is not supported and is not made to be: a world holds a lock and, once it
has made instances, live SQLite connections, and the copy that a caller pointing a world somewhere
else wants is the shallow one — `copy.copy` is that seam.

*Added 2026-09-13 — `World.__copy__` and, with it, the scaffold's and the reference world's
`build(fixture_id, *, world=...)`: a plain shallow copy inherits the instance manager, which hands
the *original* world to every instance it makes and so freezes back into the original's fixtures
directory. Closes `BACKLOG.md` B19.*

*Corrected 2026-09-13 — `World` gained `description`, an optional plain string, in §1's sketch and
§1.1's construction list. **It replaces the derivation of the OpenEnv one-line description from the
world's README**, which `components/openenv.md` §2 specified as four CommonMark rules and now does
not; that reader is deleted. Nothing here validates or copies it specially — `__copy__` carries it
with the rest of `__dict__`, as it does `name` and `version`.*

### 1.2 Registration

The decorator-or-call shape, shared by the three verbs:

```python
def tool(self, obj=None, /, **options):
    if obj is None:                      # @world.tool(transaction=False)
        return lambda fn: self._register_tool(fn, **options)
    return self._register_tool(obj, **options)   # @world.tool  or  world.tool(factory(...))
```

`_register_tool(obj, **options)`: if `obj` is a `Tool`, it is registered as it is and options must
be empty (`WorldBug` otherwise). The factory decided them, and `schema` and the argument model were
built from them, so replacing `name` or `description` afterwards would leave the `Tool` disagreeing
with its own schema; a factory that offers the choice takes `name=` and `description=` itself, as
`run_sql` and `describe_schema` do. If `obj` is callable, `Tool.from_function` (section 2) builds it.
Then: name collision → `WorldBug("tool 'x'
is registered twice")`; reserved names (`reset`, `step`, `state`, `close`) and the control tools'
names → error. Returns the original function (so a decorated function stays callable in tests) or
the `Tool`.

`middleware(obj)`: `inspect.signature(obj)`; it must accept three positional arguments (parameters
with kind `POSITIONAL_ONLY`/`POSITIONAL_OR_KEYWORD` count ≥ 3, or a `*args`); otherwise
`WorldBug("middleware must be callable as (ctx, call, next_)")`. Appended to the list and
`World.chain` is rebuilt.

`instance_startup(obj)`: the signature must have exactly one positional parameter (the context) and
otherwise only keyword-only parameters or `**kwargs`. A parameter named `fixture`, `seed` or `now`
is a `WorldBug`: those are `reset`'s own. The set of keyword names it accepts (or "any", with
`**kwargs`) is recorded; `World.accepted_startup_kwargs` is the union, used to reject unknown
`reset` arguments before an instance is created. A hook taking `**kwargs` makes that union "any",
which switches off unknown-argument detection for the whole world; the docs say so.

### 1.3 Registration is open

There is no freeze. A tool registered after instances exist is available to them on the next call
(the registry is read at call time); a middleware registered later applies to them too (the chain
is read at call time). Import-time registration is the convention the scaffold and the coverage lint
encourage, not a rule the runtime enforces. Registering while calls are in flight is unsupported and
undefined; the registry is not locked (`architecture.md` §5.1).

## 2. `tool.py`

```python
@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    fn: Callable[..., Any]
    params: type[pydantic.BaseModel]          # the argument model
    schema: dict[str, Any]                    # JSON schema, computed once
    transaction: bool = True
    control: bool = False                     # framework-only; not settable through from_function

    @classmethod
    def from_function(cls, fn, *, name=None, description=None, transaction=True) -> Tool
    def validate(self, arguments: Mapping[str, Any]) -> dict[str, Any]      # raises ArgumentError
    def listing(self) -> dict[str, Any]       # {"name", "description", "input_schema"}
```

`Tool` and `Tool.from_function` are public: an extension's tool factory builds its tool with them.
`control` is the framework's own flag for the control tools and cannot be set through
`from_function`.

### 2.1 Building the argument model

```
if inspect.iscoroutinefunction(fn) or inspect.isgeneratorfunction(fn):
    error "a tool is a plain synchronous function"
sig = inspect.signature(fn, eval_str=True)          # PEP 563/649 friendly
params = list(sig.parameters.values())
if not params: error "a tool takes the context as its first parameter"
ctx_param, arg_params = params[0], params[1:]
if ctx_param.kind not in (POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD) or (
        ctx_param.annotation not in (empty, Ctx)):
    error "the first parameter is the context: positional, annotated Ctx or unannotated"
for p in arg_params:
    if p.kind in (VAR_POSITIONAL, VAR_KEYWORD): error "tools take named arguments only"
    if p.annotation is inspect.Parameter.empty: error f"argument {p.name!r} needs a type annotation"
    if p.annotation is a datetime/date/time/Enum type:
        error f"parameter {p.name!r} is annotated {ann}; use `str` with a pattern or `Literal`"
fields[p.name] = (p.annotation, p.default if p.default is not empty else ...)
model = pydantic.create_model(f"{name}Arguments",
                              __config__=ConfigDict(extra="forbid", strict=True), **fields)
```

- `Annotated[T, Field(description=..., ge=..., pattern=...)]` passes straight through pydantic and
  lands in the schema. Strictness is in the model's config, so `Field(strict=False)` on one
  argument relaxes that argument and nothing else.
- `Field(alias=...)` is supported for wire names Python cannot spell (a keyword such as `from`) or
  that pydantic reserves; `populate_by_name=False`, so the wire carries the alias and the function
  receives its Python name. This needs no support code: it is pydantic's own behaviour, documented
  so worlds use it.
- An annotation that resolves only under `TYPE_CHECKING` raises `WorldBug` naming the symbol, rather
  than a bare `NameError` out of `eval_str=True`.
- A mutable default (`list`, `dict`) is rejected at registration ("use `None` and default inside the
  tool"): it keeps the published `default` honest, and it mirrors Python's own rule about mutable
  defaults.
- Anything pydantic refuses at model build — an unsupported annotation (a class it cannot build a
  schema for), a field name that shadows a `BaseModel` attribute (`schema`, `json`, `copy`,
  `model_*`) — fails at registration with pydantic's own message prefixed by the tool and argument
  name. There is no separate check for these; the prefix is the whole mechanism.
- The description: explicit `description=`, else the whole docstring, dedented
  (`inspect.getdoc`), else `""`. The whole docstring is what the agent reads. A lint (`SH205`) warns
  on an empty description.

### 2.2 Schema

`schema = model.model_json_schema(mode="validation")`, then: drop the top-level `title`; set
`additionalProperties: false` explicitly (pydantic emits it for `extra="forbid"`; asserted);
`$defs` kept. It is a plain dict, computed once and shared; the docs say not to mutate it.
`listing()` emits `{"name", "description", "input_schema"}`, which is OpenEnv's own `Tool` shape;
that model forbids extra keys, so nothing else goes on the wire.

### 2.3 Validation and invocation

```
def validate(self, arguments):
    try:
        model = self.params.model_validate(dict(arguments))
    except pydantic.ValidationError as e:
        raise ArgumentError(tool=self.name, violations=[
            {"path": ".".join(str(p) for p in err["loc"]), "message": err["msg"], "type": err["type"]}
            for err in e.errors(include_url=False)])
    return {name: getattr(model, name) for name in self.params.model_fields}
```

Validation is **strict**, from `ConfigDict(strict=True)` on the model: `"5"` and `2.0` for an `int`
and `1` for a `bool` are refused, so the tool list is the contract. LLM callers emit `2.0` for a
`limit` routinely, so a world mimicking a product that coerces relaxes that one argument with
`Annotated[int, Field(strict=False)]`, which works because the call site passes no `strict=`
argument of its own. Strict mode means argument types are JSON types: `str`, `int`, `float` (an
`int` is accepted for a `float`), `bool`, `None`, `list`, `dict`, `Literal[...]` for enumerations,
nested pydantic models or TypedDicts for objects (both accept a plain dict under strict validation).
Timestamps are `str` with a pattern (the canonical format), never `datetime`; `Enum` classes are
not used for arguments (strict mode would demand the member object). Both are refused at
registration (section 2.1).

## 3. `call.py`

```python
@dataclass(frozen=True)
class Call:
    name: str
    arguments: Mapping[str, Any]
    tool: Tool
    def with_arguments(self, **changes) -> Call      # merges changes over the current arguments

def build_chain(middlewares: Sequence[Middleware], innermost: Handler) -> Handler
def invoke(ctx: Ctx, call: Call) -> Any              # the innermost handler; matches Handler
```

`invoke`, precisely:

```
def invoke(ctx, call):
    tool = call.tool
    validated = tool.validate(call.arguments)             # ArgumentError here passes back up the chain
    call = call.with_arguments(**validated); ctx = ctx.with_call(call)
    if tool.transaction:
        with ctx.db.transaction():
            return to_jsonable_python(tool.fn(ctx, **call.arguments))
    return to_jsonable_python(tool.fn(ctx, **call.arguments))
```

Serialisation is inside the transaction, so a result that cannot be serialised rolls the call back
rather than committing a write whose answer never reached the caller.

`build_chain` normalises the context as it descends: each layer is called as
`mw(ctx if ctx.call is call else ctx.with_call(call), call, next_)`, so `call` is always `ctx.call`,
including after a middleware rewrites arguments with `with_arguments` and passes the new `Call` on.

Order matters: validation happens inside the chain (so the error handler sees `ArgumentError`),
after the lock is held (so `Ctx` per call is not shared). A middleware sees `call.arguments` as the
raw mapping before `invoke` and cannot observe the validated form; a middleware that needs typed
arguments calls `call.tool.validate(call.arguments)` itself (cheap, cached model).

`to_jsonable_python` from `pydantic_core`: `bytes` raise (`WorldBug("tool results cannot
contain bytes; encode them")`); `datetime` to ISO 8601; models and dataclasses to dicts; sets are
rejected (order). `None` is a valid result.

## 4. `ctx.py`

```python
@dataclass(frozen=True)
class InstanceInfo:
    id: str; fixture: str | None; seed: bytes      # the derived instance seed, not the caller's seed=

@dataclass(frozen=True)
class Ctx:
    db: Db; clock: Clock; ids: Ids
    state: dict[str, Any]                 # shared per instance (same dict object across calls)
    instance: InstanceInfo
    call: Call | None = None
    def with_call(self, call: Call) -> Ctx      # dataclasses.replace; state and db are shared references
```

`state` is a plain dict on purpose; typed access is the world's business. It is created at instance
creation, populated by startup hooks, and lives until destroy.

## 5. `errors.py`

```python
class SeahavenError(Exception): ...          # root: anything Seahaven raises
class WorldBug(SeahavenError): ...           # framework misuse / bug in world code; never shown to an agent

class ToolError(SeahavenError):
    code: str; message: str; details: Any
    def __init__(self, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)            # so str(e) and a traceback read as the message
        self.code, self.message, self.details = code, message, details
    def __repr__(self) -> str                # class name plus code, message and details
    def to_dict(self) -> dict[str, Any]

class ArgumentError(ToolError):     # code "invalid_arguments"
    tool: str; violations: list[dict[str, str]]
    def __init__(self, tool: str, violations: list[dict[str, str]]) -> None
    # message: "invalid arguments: <path>: <message>; <path>: <message>"
class DbError(ToolError):           # code "db_error"
    sqlite_message: str; sqlite_code: int | None; refusals: tuple[str, ...]
    def __init__(self, sqlite_message: str, sqlite_code: int | None = None,
                 refusals: tuple[str, ...] = ()) -> None
    # message: "database error" when refusals empty; "not allowed: <first refusal>" otherwise
class UnknownTool(ToolError):       # code "unknown_tool"
    name: str
    def __init__(self, name: str) -> None    # message: "unknown tool: <name>"
```

Every subclass calls `ToolError.__init__` with its fixed code and the message it builds, so the base
contract holds for all of them. `WorldBug` is what the framework raises for misuse: a registration
error, a call on a destroyed instance, a result it cannot serialise, a `describe_schema` table that
is not in the schema, a malformed fixture.

A world's errors are its own `ToolError` subclasses in `errors.py`, imported where raised; the
scaffold generates `NotFound(kind, key)`, `InvalidInput(field, why)` with a `from_violations`
classmethod the error handler uses, and `Internal(message="...")`. `DbError.sqlite_message` is
never the default `message` for a `DbError` raised by `Db` in world code (that says "database
error"), so raw engine text reaches an agent only if the world's handler chooses to include it or a
helper explicitly does (`run_sql` in the SQLite dialect does, by design).

## 6. Test plan

- `test_world.py`: construction fails on DDL that does not execute, with SQLite's message, and does
  not run the DDL lint; `fixtures_dir` derivation from a `src/` layout, from an explicit path, and
  from a module with no `pyproject.toml` above it (`fixtures/` beside the package, no error);
  duplicate name, reserved name, control-tool name; a `Tool` from a factory is registered as it is
  and options passed with it are refused; a tool and a middleware registered after an instance exists
  are used by that instance; decorator and call forms of all three verbs return the right objects; middleware shape
  check accepts functions, callables with `__call__`, and `*args`; rejects two-parameter callables;
  startup hook signature rules, refusal of `fixture`/`seed`/`now` parameters, and
  `accepted_startup_kwargs`; a copy with a moved `fixtures_dir` freezes there and leaves the
  original's directory empty, and a tool registered on a copy does not appear on the original.
- `test_tool.py`: argument models for `str`, `int`, `float`, `bool`, `None` unions, `list[str]`,
  `dict[str, int]`, `Literal`, a nested `BaseModel`, a `TypedDict`, `Annotated` with description and
  constraints; `Enum` and `datetime` parameters **rejected** at registration; defaults and required;
  mutable default refused; missing annotation refused; `*args` refused; `async def` and a generator
  function refused; a `TYPE_CHECKING`-only annotation refused by name; `Field(alias="from")` puts
  the alias on the wire and the Python name in the call; schema has `additionalProperties: false`,
  no title, descriptions present, and the whole docstring as the description; `validate` returns
  models for nested types; `ArgumentError.violations` lists every problem for a call with three
  mistakes; strict (`"5"` and `2.0` refused for `int`, `1` refused for `bool`, `1` accepted for
  `float`) and `Field(strict=False)` relaxes one argument; `listing()` has exactly `name`,
  `description`, `input_schema`.
- `test_call.py`: chain order (three middlewares recording entry and exit); a middleware
  short-circuits; a middleware rewrites arguments via `with_arguments` and `call is ctx.call` in
  every layer below it; `ArgumentError` passes through the chain; `transaction=True` rolls back a
  tool that wrote then raised; `transaction=False` leaves the write; an unserialisable result rolls
  the transaction back; result serialisation of model, dataclass, datetime, `None`; bytes refused;
  `ctx.state` shared across calls and per-call `ctx.call` distinct.
- `test_errors.py`: `to_dict` shapes; `str(e)` is the message and `repr(e)` shows all three fields;
  every framework subclass's constructor; a world subclass with a fixed code; default messages hide
  SQLite text; `WorldBug` and `ToolError` are both `SeahavenError` and are not each other.
