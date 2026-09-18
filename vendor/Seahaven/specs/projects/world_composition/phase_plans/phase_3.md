---
status: complete
---

# Phase 3: typed access

## Overview

Phases 1 and 2 built the tree and made an instance be it. Everything reaching a tool so far has
gone by *name*, and every result has come back as rendered data. This phase adds the other way in:
a tool is reached by the **function itself**, with its arguments checked and its result typed, at
the instance and through a handle — architecture §8.

Four pieces, and they only add up as a set:

- **`invoke` returns the tool's object** (§8.4). Without this `R` is a lie: a tool that returns a
  `Charge` hands its in-process caller a dict. The serialisation stays inside the transaction, so
  a result that cannot be rendered still rolls the call back; only the value is discarded. The
  OpenEnv layer serialises for the wire, which is where the wire format belongs.
- **The types travel.** `Tool[**P, R]`, `Tool.from_function` preserving them, and — not in §16 but
  required for any of it to reach a world author — `World.tool` typed so that the decorator returns
  the function *as itself* rather than as `Any`. Without that last one every tool in every world is
  an untyped callable and `R` resolves to `Any` everywhere.
- **The overloads** on `Instance.call` and `WorldHandle.call` (§8.1), over `Concatenate` +
  `ParamSpec`.
- **Resolution by function** (§8.2): `Composition.by_fn` at the instance, and the per-world
  `fn → Tool` map deferred out of phase 1 in the handle's subtree, each with its own ambiguity
  error.

Also here, both deferred out of earlier phases and both public signatures: `World.tools_by_fn`
(phase 1 shipped no per-world map rather than a single-valued one that loses a tool), and the two
`Ctx.with_call` overloads that recover `Self` for a world annotating `Ctx[CompanyWorlds]`
(phase 2).

Out of scope: `SeahavenState.composition` (phase 5), the lint codes that bind a `Worlds` subclass
to the registrations (SH502/SH503, phase 5), and the documentation of any of it (phase 6).

## Steps

1. **`src/seahaven/tool.py`** — the tool carries its own signature.

   ```python
   @dataclass(frozen=True, eq=False)
   class Tool[**P = ..., R = Any]:
       name: str
       description: str
       fn: Callable[Concatenate[Ctx[Any], P], R]
       ...

       @classmethod
       def from_function[**Q, S](
           cls, fn: Callable[Concatenate[Ctx[Any], Q], S], *, name=None, description=None,
           transaction=True,
       ) -> Tool[Q, S]: ...

       def arguments(self, positional: Sequence[Any], keywords: Mapping[str, Any]) -> dict[str, Any]
   ```

   - `**P = ...` and `R = Any` (PEP 696) so a bare `Tool` annotation is what it was, and every
     `dict[str, Tool]` in the framework keeps working unchanged.
   - `from_function` builds `Tool(...)` rather than `cls(...)`: the parameters have to reach the
     return type, and `Self` cannot carry them. Nothing subclasses `Tool`.
   - `arguments` binds positional arguments to their parameter names, in the model's field order,
     which is the signature's order after the context. `*args: P.args` makes a parameter the tool
     declares positionally legal to pass positionally, so the by-name path underneath has to accept
     one; too many, or one given twice, is a `WorldBug`.
   - `_check_context_parameter` accepts `Ctx[X]` — `get_origin(annotation) is Ctx` — and says so in
     its message (§6.3).

2. **`src/seahaven/ctx.py`** — two overloads on `with_call`.

   ```python
   @overload
   def with_call(self, call: Call | None) -> Self: ...
   @overload
   def with_call(self, call: Call | None, *, worlds: Worlds) -> Ctx[Any]: ...
   ```

   The widening to `Ctx[Any]` is real only when the activation changes the `worlds` object; a
   middleware of a world that declared `Ctx[CompanyWorlds]` keeps its parameter, which is the point
   of §8.3. `call.rebind` becomes generic in the context for the same reason.

3. **`src/seahaven/call.py`** — `invoke` returns what the tool returned.

   ```python
   result = tool.fn(ctx, **call.arguments)
   serialise(result)   # inside the transaction: an unrenderable result still rolls the call back
   return result
   ```

   `serialise` rather than `to_jsonable_python` as §8.4's sketch has it, because `serialise` is
   where this framework's extra refusals (`bytes`, `set`, iterators) live and none of them may be
   lost. Also `name_of(tool)`, the one spelling of what a call is logged as: the name asked for, or
   the function's `__qualname__`.

4. **`src/seahaven/world.py`** — the per-world map and the typed decorator.

   ```python
   @property
   def tools_by_fn(self) -> Mapping[Callable[..., Any], tuple[Tool, ...]]
   ```

   Maintained in `_add` beside `_tools` and snapshotted by `__copy__`. Multi-valued, because a world
   may register one function as two tools and a single-valued map would silently lose one — the
   reason phase 1 shipped none. Control tools are not in it: they are the framework's, not a world's
   surface, and `WorldHandle.call` refuses them by name already.

   `World.tool` gains three overloads — a `Tool` in and the same `Tool[P, R]` out, a function in and
   the same function out, and the keywords-only form returning a decorator — so a `@world.tool`
   function keeps its signature instead of becoming `Any`.

5. **`src/seahaven/instances.py`** — `Instance.call` by reference.

   ```python
   @overload
   def call[**P, R](self, tool: Callable[Concatenate[Ctx[Any], P], R], /,
                    *args: P.args, **kwargs: P.kwargs) -> R: ...
   @overload
   def call(self, tool: str, /, **arguments: Any) -> Any: ...
   ```

   `_target` accepts either. A function resolves through `Composition.entry_for` (step 6); a name
   resolves as it does today. Everything after resolution — validation, the chain, the transaction,
   the log line — is the by-name path unchanged, and the line names the *exposed* name, so an eval
   grouping on it cannot tell the two ways in apart.

6. **`src/seahaven/composition.py`** — `Composition.entry_for(fn) -> Contributed`: `by_fn`, with
   the two errors of §8.2. Absent is "not a tool of this world or anything it adds"; more than one
   names each candidate as `<exposed name> at <path>` and points at `ctx.worlds.<name>.call` or the
   exposed name.

7. **`src/seahaven/handles.py`** — `WorldHandle.call` by reference, with the same overloads.
   Resolution is over the handle's **subtree** — the node and everything it adds, transitively,
   each world's `tools_by_fn` — because a handle names an account and by a function reference there
   is no name to collide; `WorldHandle.call(name)` stays the node's own registry only, as §8.2
   says. The owning node may therefore be a descendant, and the call runs on *its* chain, its
   context and its store. The subtree walk lives here rather than in `composition.py`: that module
   imports this one at run time.

8. **`src/seahaven/openenv/env.py`** — `step` serialises the observation's result. Pulled forward
   out of phase 5, because step 3 is what makes it necessary: without it the wire would carry
   whatever object a tool returned.

9. **`tests/typed_calls_fixture.py`** — the §8.1 gate: a module ty checks and nothing runs, calling
   the committed composite world's tools by reference. `assert_type` pins the resolved `R`; the
   calls that must *fail* carry a `# ty: ignore[...]`, which ty reports as unused if it ever stops
   flagging them. It is inside the repo-wide `ty check`, so CI is the gate, and
   `test_typed_call.py` runs ty over it as well so the gate is also a test.

## Tests

`tests/test_typed_call.py`:

- `test_a_tool_is_called_by_its_function_at_the_instance` — the root's own, and a contributed one.
- `test_a_call_by_reference_runs_against_the_owning_nodes_store` — the row lands in the added
  world's file, not the root's.
- `test_a_call_by_reference_is_logged_under_the_exposed_name`.
- `test_positional_arguments_are_bound_to_their_names` — and too many, and one given twice.
- `test_a_function_of_a_world_outside_the_tree_is_a_world_bug`.
- `test_a_function_whose_world_is_a_node_twice_is_ambiguous` — both paths in the message.
- `test_an_ambiguous_function_is_reachable_through_a_handle` — the handle resolves what the
  instance cannot.
- `test_a_handle_resolves_a_function_of_a_world_in_its_subtree` — a grandchild through the child.
- `test_a_handle_reaches_a_tool_the_agent_cannot_see_by_reference` — the empty allow list again.
- `test_a_handle_refuses_a_function_no_world_in_its_subtree_owns`.
- `test_a_handle_refuses_a_function_two_nodes_of_its_subtree_own`.
- `test_a_control_tools_function_is_not_reachable_by_reference` — from either.
- `test_a_nested_call_by_reference_runs_only_the_owning_chain`.

`tests/test_call.py`:

- `test_invoke_returns_the_object_the_tool_returned` — a model comes back a model.
- `test_results_are_rendered_as_data` becomes `test_a_result_is_proved_to_render_and_handed_back`:
  the object is returned and `serialise` of it is the data it used to return.

`tests/test_world.py`:

- `test_tools_by_fn_inverts_the_registry` — including one function registered as two tools.
- `test_tools_by_fn_holds_no_control_tool`.
- `test_a_copy_snapshots_tools_by_fn`.

`tests/test_tool.py`:

- `test_a_parameterised_ctx_is_accepted_as_the_context_parameter` — `Ctx[Worlds]` and a subclass.
- `test_something_that_is_not_a_ctx_is_still_refused`.

`tests/test_ctx.py` (or `test_instances.py`, where `with_call` is covered): `with_call` with no
`worlds` keeps the context's own `worlds`; with one, replaces it.

`tests/test_env.py`: `test_the_observation_carries_rendered_data` — a tool returning a model is
rendered on the observation.

Plus `test_typed_call.py::test_ty_resolves_the_result_type_of_a_call_by_reference`, which runs
`ty check` over the fixture module of step 9 and asserts it is clean.
