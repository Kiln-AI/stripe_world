---
status: complete
---

# Phase 2: world definition and dispatch

## Overview

The layer a world author actually touches, and the path one call takes from a name and a mapping of
arguments to a tool function and back: `ctx.py`, `tool.py`, `call.py` and `world.py`, per
`components/world_and_dispatch.md`. Phase 1 built the runtime a call *arrives* at (`Db`, `Clock`,
`Ids`, the sandbox, the errors); this phase builds what decides which function runs, with which
arguments, inside which transaction, wrapped in which middleware.

Nothing here creates or owns an instance. `Ctx` is defined and can be built by hand, and everything
in the call path takes it as a parameter, but the object that materialises a database, runs startup
hooks and holds the lock is `instances.py`, in Phase 3. That is the seam the component spec already
draws: `invoke` matches `Handler = Callable[[Ctx, Call], Any]` precisely so that nothing in the call
path needs an `Instance`.

## Steps

1. **`src/seahaven/ctx.py`** — `InstanceInfo` (`id`, `fixture`, `seed`) and `Ctx`
   (`db`, `clock`, `ids`, `state`, `instance`, `call=None`), both frozen dataclasses, with
   `Ctx.with_call(call) -> Ctx` via `dataclasses.replace` so `state` and `db` stay shared
   references. `Call` is imported under `TYPE_CHECKING` only: `ctx` is the bottom of this phase's
   import order and `call.py` sits above it.

2. **`src/seahaven/tool.py`** — the frozen `Tool` dataclass (`name`, `description`, `fn`, `params`,
   `schema`, `transaction=True`, `control=False`), hashable by name, and:

   ```python
   @classmethod
   def from_function(cls, fn, *, name=None, description=None, transaction=True) -> Tool
   def validate(self, arguments: Mapping[str, Any]) -> dict[str, Any]
   def listing(self) -> dict[str, Any]
   ```

   `from_function` follows §2.1 step by step: refuse `async def`, a generator and an async
   generator; `inspect.signature(fn, eval_str=True)` with a `NameError` turned into a `WorldBug`
   naming the symbol (the `TYPE_CHECKING`-only annotation); the first parameter is the context
   (positional, annotated `Ctx` or unannotated); every other parameter is keyword-capable
   (`architecture.md` §3 step 1 — so neither variadic nor positional-only), annotated, has no
   mutable default, and is not a `datetime`/`date`/`time`/`Enum` anywhere in its annotation. Then
   `pydantic.create_model(f"{name}Arguments", __config__=ConfigDict(extra="forbid", strict=True),
   **fields)`, and the schema from `model_json_schema(mode="validation")` with the top-level `title`
   dropped and `additionalProperties: false` asserted.

   A failure out of `create_model` is re-raised as a `WorldBug` carrying pydantic's own message,
   prefixed with the tool and the argument it belongs to: the argument is found by rebuilding the
   model one field at a time, on the error path only, which is what makes "the prefix is the whole
   mechanism" (§2.1) work for an unsupported annotation and for a field name that shadows
   `BaseModel`. Building the schema is wrapped the same way and through the same helper, so it
   names the argument too, because pydantic defers some failures until then: `type Price = Decimal`,
   where `Decimal` is imported only under `TYPE_CHECKING`, resolves to the alias object at signature
   time and fails when pydantic looks through it. The one-field probe `_blame` uses therefore builds
   the model *and* asks it for its schema.

3. **`src/seahaven/call.py`** — frozen `Call` (`name`, `arguments`, `tool`) with
   `with_arguments(**changes)`; `build_chain(middlewares, innermost)`, which normalises the context
   as it descends (`mw(ctx if ctx.call is call else ctx.with_call(call), call, next_)`); and
   `invoke(ctx, call)` as §3 writes it — validate, rebuild the call and the context from the
   validated arguments, then run the tool inside `ctx.db.transaction()` when `tool.transaction`,
   with serialisation *inside* the transaction.

   Serialisation is `pydantic_core.to_jsonable_python` behind one check: the result is walked first
   and `bytes`, `bytearray`, `set` and `frozenset` raise `WorldBug`. The walk is necessary because
   `to_jsonable_python` accepts both silently — bytes become text and a set becomes a list in
   whatever order the set iterates — and the spec refuses both (§3: bytes raise, sets are rejected
   for order).

   The walk descends everything `to_jsonable_python` descends, or it would only cover the shapes it
   happened to think of: dicts and mappings, lists and tuples, a model's fields *and its extras and
   computed fields* (neither is in `__dict__`), dataclass fields, and any other iterable, which a
   model can declare as a field type (a `deque[str]`) and pydantic will render. A generator or
   iterator is refused outright rather than descended: reading it is what empties it, and a result a
   tool cannot look at twice is a bad result anyway. The walk carries the ancestors of the value it
   is looking at, so a result that contains itself raises rather than hanging while a value shared
   down two branches — which is not a cycle — passes. A value pydantic cannot render at all is a
   `WorldBug` too, rather than a `PydanticSerializationError` escaping the framework: "a result it
   cannot serialise" is one of the misuses `errors.py` §5 names.

   An exception from a tool that is not a `ToolError` is logged at `ERROR` with `exc_info`, the tool
   and the instance id, and re-raised unchanged (`architecture.md` §4), so the world's error handler
   still sees it and the traceback is on record whatever the handler does with it.

4. **`src/seahaven/world.py`** — the `World` class per §1: construction (schema hash, the DDL proof
   through `build_blank(":memory:", …)`, `fixtures_dir` derivation, `work_dir`,
   `untracked_tables`), the three registration verbs sharing one decorator-or-call shape, the
   read-only `tools` view, `middlewares`, `startup_hooks`, `accepted_startup_kwargs` and `chain`.

   Registration refusals, all `WorldBug`: a duplicate name; a reserved name (`reset`, `step`,
   `state`, `close`) or a control tool's name; options passed alongside a `Tool` from a factory; a
   middleware not callable as `(ctx, call, next_)`; a startup hook with more than one positional
   parameter or with a parameter named `fixture`, `seed` or `now`.

   A startup hook is stored as a `RegisteredStartupHook` — a frozen, callable wrapper carrying
   `fn`, `accepts` and `takes_var_kwargs`, which is the shape Phase 3 reads when it filters
   `reset` keyword arguments per hook (`components/fixtures_instances.md` §2.2, `hook.accepts`).

5. **`src/seahaven/__init__.py`** — add `World`, `Ctx`, `Tool` and `Call` to the exports. The rest
   of `architecture.md` §1's list still belongs to the phases that build those names.

## Deferred to later phases

Named here so a reviewer can tell a gap from an omission.

- **`World.instance(...)` and `World.fixtures()`** (§1) need `Instance`, `InstanceManager` and
  `Fixture`, which are Phase 3 (`components/fixtures_instances.md`). `World` already holds
  everything they read: the registry and the chain are read at call time, from the world, so Phase 3
  adds the two methods and nothing else here changes.
- **Registering the control tools at construction** (§1.1 step 4) needs `control.py`, which is
  Phase 4. Their names are reserved here — `CONTROL_TOOL_NAMES` in `world.py` — so a world that
  registers `controller_run_sql` is refused today, which is the half of the behaviour that is
  observable without the tools themselves.
- **`sql_files(package, dir)`** (`architecture.md` §1) is the schema loader a world package calls in
  its `world.py`. Nothing in this phase needs it; the phase that first builds a world package
  (Phase 5) is where it belongs.
- **The per-call `INFO` log** (`architecture.md` §6: instance id, world, tool, duration, outcome) is
  written by `Instance.call`, which owns the duration and sees the outcome after the chain has run.
  Phase 3.

## Decisions a reviewer should check

- **The schema carries no top-level `description`.** `architecture.md` §3 step 3 says the tool's
  description is set on the schema; `components/world_and_dispatch.md` §2.2, which is the detailed
  document for this component, says only: drop `title`, set `additionalProperties`, keep `$defs`.
  The component document wins, and the duplication would be pointless anyway — `listing()` already
  carries `description` beside `input_schema`, and that is the shape OpenEnv consumes.
- **`datetime`/`Enum` are refused anywhere in an annotation**, not only as the whole annotation:
  `Annotated[datetime, Field(...)]`, `datetime | None` and `list[datetime]` are all refused with the
  message §2.1 specifies. Under strict validation every one of them is unsatisfiable from JSON, so
  refusing at registration is the same service in each case. "Anywhere" includes three carriers that
  are not the class itself and each publish a schema the tool would then refuse:
  `Literal[Colour.RED]`, which holds the enum *member* and publishes `"const": "red"` while
  accepting only `Colour.RED`; `type Stamp = datetime` (PEP 695); and `NewType("Stamp", datetime)`.
  The last two are looked through by `__value__` and `__supertype__`, with a `seen` set so an alias
  defined in terms of itself terminates.
- **Construction prefixes SQLite's DDL error** with the world's name rather than raising it bare
  ("the SQLite error is the message", §1.1): the SQLite text is the whole rest of the message, and
  a bare `near ")": syntax error` with no subject is a poor thing to read at import time. It is a
  `WorldBug`, like every other registration failure.
- **`invoke` replaces the call's arguments with the validated ones. Both specs say to merge them,
  and this deviates from both.** `components/world_and_dispatch.md` §3 and `architecture.md` §4
  both write `call = call.with_arguments(**validated)`, which merges the validated arguments over
  the raw ones. That is a bug wherever a wire name differs from its parameter's: with
  `Field(alias="from")` — a feature the same section specifies — the raw mapping holds `from` and
  the validated one holds `from_`, the merge carries both, and `tool.fn(ctx, **call.arguments)`
  raises `TypeError: got an unexpected keyword argument 'from'` on the tool's first call. The two
  specified behaviours cannot both hold, and the alias contract is the one with a stated purpose
  ("the wire carries the alias and the function receives its Python name"), so the merge goes.
  Nothing is lost by replacing: the validated arguments are always the model's complete set of
  fields, `extra="forbid"` having refused everything else, so there is never anything in the raw
  mapping that a merge would have preserved. The end-to-end test in `test_call.py` that calls an
  aliased tool through `invoke` fails if this is put back to a merge. **Do not "restore spec
  compliance" here without fixing aliases some other way.**
- **`World.tool`'s `transaction` is typed `bool | None = None`**, where §1 publishes
  `transaction: bool = True`. The default is still `True` in effect. §1.2 requires refusing options
  passed alongside a `Tool` a factory built, and with a `bool` default there is no way to tell
  `world.tool(built, transaction=True)` — which must be refused, because the factory already
  decided — from a caller who passed nothing.
- **A `Tool` from a factory is registered without re-validating its `params` model.**
  `architecture.md` §3 step 5 says it is registered "after the same validation of its `params`
  model"; `components/world_and_dispatch.md` §1.2 says it "is registered as it is". Same conflict
  class as the schema `description` above, resolved the same way: the component document is the
  refinement layer for its own component. The check would also have little to check — the model was
  built by `Tool.from_function` in the factory, and a hand-built `Tool` is framework-level code.
- **`Handler` and `Middleware` are defined in `call.py`**, where `build_chain` needs them, rather
  than in `world.py` where §1 lists all three aliases beside the `World` they describe. `world.py`
  re-exports both, so a world author annotating a middleware finds them where the spec says they
  are. Neither is in `seahaven/__init__`, which is right: `architecture.md` §1's export list does
  not carry them.
- **A `Tool` is hashable by name and equal only to itself.** `architecture.md` §2 asks for
  "hashable by name" and says nothing about equality. Field equality would work — a dict field
  compares fine — but it is not what a tool is: two tools built from the same function are two
  registrations, and name equality would make two unrelated tools with one name the same object.
  Identity is the choice, and `replace(tool) != tool` pins it.
- **The `datetime`/`Enum` refusal stops at the tool's own parameters.** A nested model with a
  `datetime` field is as unsatisfiable under strict validation, but the model is the world's own
  declaration and may be used elsewhere for other things; walking into it would refuse annotations
  the spec does not, and pydantic's own validation error names the field when a call arrives.
  `test_the_refusal_stops_at_the_tools_own_parameters` pins the boundary.
- **Note on §2.1's shadowing list, for whoever writes the docs.** Of the names it says pydantic
  refuses, only `model_*` raises on pydantic 2.13: `schema`, `json`, `copy` and `dict` build with a
  `UserWarning` and validate correctly. The implementation needs nothing for this — "the prefix is
  the whole mechanism" — but the docs should not promise a check pydantic does not make.

## Tests

`tests/test_tool.py`

- argument models for `str`, `int`, `float`, `bool`, an optional union, `list[str]`,
  `dict[str, int]`, `Literal`, a nested `BaseModel` and a `TypedDict`
- `Annotated` with a description and constraints reaches the schema; constraints are enforced
- `Enum`, `datetime`, `date` and `time` parameters refused at registration, including inside
  `Annotated`, a union and a `list[...]`
- a positional-only argument refused at registration, naming the parameter
- a nested model whose *own* field is a `datetime` registers cleanly: the refusal is about the
  tool's parameters
- `Literal[Colour.RED]`, `type Stamp = datetime` and `NewType("Stamp", datetime)` refused: three
  carriers that publish a schema a strict argument would refuse
- a mutable default of every kind (`list`, `dict`, `set`, `bytearray`) refused
- the schema is the *validation* schema: a `Field(validation_alias=...)` argument publishes the wire
  name, which the serialisation view would not
- a `Tool` is frozen, hashes by name, and is not equal to a field-for-field copy of itself
- required and defaulted arguments; a mutable default refused; a missing annotation refused
- `*args` and `**kwargs` refused; no parameters at all refused; a non-positional first parameter
  refused; a first parameter annotated as something other than `Ctx` refused
- `async def`, a generator function and an async generator function refused
- a `TYPE_CHECKING`-only annotation refused, naming the symbol
- an annotation pydantic cannot build refused, naming the tool and the argument; a field name that
  collides with a `BaseModel` member (`model_dump`) refused the same way
- an alias to a `TYPE_CHECKING`-only symbol refused as a `WorldBug` naming the tool, the argument
  and the symbol, rather than escaping as pydantic's own error; an alias defined in terms of itself
  registers
- `Field(alias="from")` puts the alias on the wire and the Python name in the call (and
  `test_call.py` runs one through `invoke`, which is where a merge rather than a replacement shows)
- the schema has `additionalProperties: false`, no top-level `title`, and `$defs` for a nested model
- the description is the explicit one, else the whole dedented docstring, else `""`
- `validate` returns a nested `BaseModel` as a model, not a dict
- `ArgumentError.violations` lists all three mistakes of a call that makes three
- strict: `"5"` and `2.0` refused for `int`, `1` refused for `bool`, `1` accepted for `float`;
  `Annotated[int, Field(strict=False)]` relaxes exactly that argument
- `listing()` has exactly `name`, `description` and `input_schema`
- a `Tool` hashes by name and is equal only to itself; a callable with no `__name__` needs `name=`

`tests/test_call.py`

- chain order: three middlewares record entry and exit and the order is outermost-first
- a middleware short-circuits without calling `next_`
- a middleware rewrites arguments with `with_arguments` and `call is ctx.call` in every layer below
- `ArgumentError` from validation passes up through the chain
- `transaction=True` rolls back a tool that wrote and then raised; `transaction=False` leaves the
  write in place
- an unserialisable result rolls the transaction back
- results serialise: a pydantic model, a dataclass, a `datetime`, `None`, nested containers
- a tool is called with exactly the validated arguments end to end, and `ctx.call.arguments` holds
  the same: an aliased argument under its Python name and not its wire name, a default the call did
  not carry, a nested model as a model, a relaxed argument coerced
- `bytes` refused, at the top level and nested and inside a model, in a model's extras, in a
  computed field, as a mapping *key*, and inside a container a model declares; a `set` refused; a
  generator refused; a cyclic result refused and a value shared down two branches accepted; a value
  pydantic cannot render refused
- `ctx.state` is the same object across calls and `ctx.call` differs per call
- a non-`ToolError` exception is logged at `ERROR` with a traceback and reaches the caller unchanged;
  a `ToolError` is not logged
- `Call.with_arguments` merges rather than replaces and leaves the original frozen
- `Call`, `Ctx` and `InstanceInfo` are frozen: a layer rewrites by copying

`tests/test_world.py`

- construction: `schema_hash` is stable under whitespace, differs for different DDL; DDL that does
  not execute raises `WorldBug` carrying SQLite's message; a DDL rule the lint owns (a table with no
  `STRICT`) does *not* fail construction
- `fixtures_dir`: explicit path used as given; derived from a `src/` layout by finding
  `pyproject.toml`; `fixtures/` beside the package when there is no `pyproject.toml` above it, with
  no error
- `work_dir` and `untracked_tables` are carried as given
- `tools` is read-only, insertion ordered
- duplicate name, reserved OpenEnv name, control tool name all refused
- a `Tool` from a factory is registered as it is; options passed with it are refused
- the decorator and the call form of all three verbs return the right object, and a decorated
  function stays callable
- `@world.tool(name=..., description=..., transaction=False)` reaches the `Tool`
- middleware shape: a function, a callable object and `*args` accepted; a two-parameter callable
  refused; the chain is rebuilt on registration and a middleware registered after the first call
  applies to the next one
- startup hooks: registration order preserved; `accepts` and `takes_var_kwargs` recorded;
  `accepted_startup_kwargs` is the union; a second positional parameter refused; `fixture`, `seed`
  and `now` refused
- a tool registered after a call has already run is visible to the next call (the registry is read
  at call time)
- a tool carrying `control=True` may take a control tool's name: the seam Phase 4 registers
  through; it may still not take an environment verb, which belongs to the wire
- a tool registered with a plain `@world.tool` runs in a transaction: the default the `bool | None`
  signature gives up and `World.tool` restores
- a world built where there is no module file (a REPL, an `exec`) derives from the project root
  above the working directory, or from the working directory itself when there is none

## Mutation check

Every line of `tool.py`, `call.py`, `world.py` and `ctx.py` that can stand alone was replaced with
`pass` in turn and the phase's tests re-run. Statement deletion cannot reach a decorator's keyword,
a ternary, or an argument inside a multi-line call, so those were mutated by hand too: `eq=False`
and `frozen=True` on all four dataclasses, `mode="validation"` changed to `"serialization"`,
`extra="forbid"`, `strict=True`, `_MUTABLE_DEFAULTS`, the `transaction` default, the carrier
unwrapping and the enum-member refusal.
Everything this plan names as a behaviour or records as a decision is killed by at least one test.
What survives, and why each is a mutant with no behaviour to kill:

- annotation-only imports (`Db` in `ctx.py`, `Tool` in `call.py`, and the rest): nothing in these
  modules evaluates their annotations at runtime. `ty` catches these — deleting one is an
  `unresolved-reference` — which is why they are checked and not tested.
  *(Amended in Phase 5.* The reason first recorded here was "annotations are lazy on 3.14, so
  nothing evaluates them at runtime", stated as a general rule. It is not one. PEP 649 defers
  evaluation, it does not prevent it, and Seahaven evaluates annotations at registration in two
  places: `World.middleware` calls `inspect.signature(obj)`, and `Tool.from_function` calls
  `inspect.signature(fn, eval_str=True)` to build the argument model. An annotation-only import that
  a *registered* callable's annotations depend on — a world's middleware or tool module, and
  `seahaven/control.py`, which registers the two control tools at import time — is therefore
  load-bearing, and deleting it raises `NameError` or `WorldBug` out of the import.

  The distinction that is true is **a name that appears only in the annotations of functions nothing
  registers**. It is worth stating that narrowly rather than per-module: `ctx.py` and `call.py`
  happen to register nothing at all, so "a module nothing registers" would hold for the imports
  listed here, but it does not generalise — Phase 4's records were amended a second time for
  believing it, having named three modules that all register. The conclusion stands unchanged: each
  of these imports really does survive, and each is still an equivalent mutant. Only the stated
  reason was wrong, and it was wrong in a way a later phase could have read as settled licence to
  drop such an import anywhere.)
- `__all__` lists and the `StartupHook` alias: they change `from … import *` and type checking,
  neither of which has runtime behaviour to observe.
- `return None` at the end of `_children` and `_fixtures_dir_at_project_root`, and the
  `list | tuple` fast path in `_children`, which the generic iterable branch below it answers
  identically and more slowly.
- `include_url=False` in `Tool.validate`: the violation comprehension names the three keys it
  wants, so pydantic's `url` key is dropped whether or not it was ever added.
- *Deleting* `mode="validation"` rather than changing it: validation is pydantic's own default, so
  the keyword is worth writing for the reader and there is nothing behind it to break. Changing it
  to `"serialization"` is what the schema test kills.
