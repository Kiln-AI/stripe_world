---
status: complete
---

# Phase 9: the XML-RPC example extension

## Overview

Extensibility is a first-class requirement of this framework (`project_overview.md` §3): a world that
meets a protocol Seahaven has never heard of must be able to serve it from an ordinary Python
package, written by anyone, without forking anything. `functional_spec.md` §21 writes that down as a
contract — five seams, a dependency direction, and two prohibitions — and then says the repository
ships one worked example of it, XML-RPC.

This phase builds that example. It is not framework code: nothing under `src/seahaven/` changes
except one sentence of a stub docs page that can now name where the example lives. What the phase
produces is a second kind of package in this repository — neither the framework nor a world, but an
*extension* — and the proof that the contract as specified is sufficient to carry a protocol end to
end: parse, dispatch, render, fault, log.

XML-RPC is a good choice for the example because it is genuinely foreign to everything Seahaven
knows. Its request is one XML document, not a set of named arguments; its method namespace is its
own, not the tool registry's; its failure mode is a `<fault>` *response*, not a raised error. If the
seams carry that, they carry a vendor's query dialect or an internal RPC format.

### The shape of the thing

One tool and one middleware, and the split between them is not a matter of taste:

- **The tool owns the protocol.** `xmlrpc_call(methods=...)` returns a `Tool` whose one argument is
  the `<methodCall>` document. It parses it, finds the handler the world supplied, checks the
  parameters against that handler's signature, calls it, and renders the `<methodResponse>`. Every
  failure it meets — a document that is not well-formed, a method nobody registered, parameters a
  handler cannot take — is raised as `XmlRpcFault`.
- **The middleware owns the response.** `render_faults(tools=[...])` catches `XmlRpcFault` and
  `DbError` and returns the `<methodResponse><fault>` document as the call's result.

The reason fault rendering is *not* in the tool is the transaction. `invoke` runs a tool inside
`ctx.db.transaction()`; a tool that caught its own fault and returned a fault document would return
normally, and the transaction would **commit** whatever the handler wrote before it failed. A faulted
RPC must leave nothing behind. So the fault leaves the tool as an exception, the transaction rolls
back, and the middleware — which runs outside it — turns it into the document the caller reads. That
is also why `functional_spec.md` §21 names a middleware for `DbError` specifically: a `UNIQUE`
violation under a handler is the commonest way an XML-RPC method fails, and it has to roll back
before it is rendered.

### Which seams the example exercises

§21 lists five things an extension may rely on. The example uses four of them, and each is load
bearing rather than decorative:

1. **The instance context.** The tool's function takes `ctx`; every handler the world supplies takes
   `ctx` as its first parameter, which is how a method reaches the database, the clock and the ids.
2. **The middleware shape.** `render_faults` returns a `(ctx, call, next_)` callable, checked by
   `World.middleware` at registration.
3. **Instance startup and `reset()` keyword arguments.** `remember_client` is a startup hook the
   world registers; it reads `xmlrpc_client=` off `reset()`/`world.instance(...)` and puts it in
   `ctx.state`, which is what the call log records. An eval that wants two instances driven by two
   different clients spells it in the `reset` call.
4. **DDL as text.** `CALL_LOG_DDL` is a string the extension exports and the world concatenates into
   its schema, with the schema-hash cost §21 point 4 describes — visible here, because the copy world
   is a copy precisely so that adding it does not force ProjectTracker's committed fixture to be
   regenerated.

The fifth, `seahaven.sandbox`, is for an extension serving another *SQL* dialect. XML-RPC is not one,
and reaching for the sandbox to tick a box would be a worse example than leaving it out; the seam is
already proven by `helpers/run_sql.py` and `control.py`, which are the framework's own two users of
it.

### What the tests are

`extensions/seahaven-xmlrpc/tests/` drives everything through Seahaven's pytest plugin against
`tracker_rpc` — a copy of ProjectTracker: the reference world's own schema and its own error handler,
imported rather than retyped, plus the extension's table, the XML-RPC tool and the fault middleware.
The copy exists because the extension must not change ProjectTracker: adding a table to the reference
world would change its schema hash and invalidate the `empty` fixture on disk, and Phase 10 owns that
world besides.

The other half of the test design is that the assertions are written with `xmlrpc.client` on the
other side of the wire — `dumps` to build the request, `loads` to read the answer, and
`xmlrpc.client.Fault` to catch the failure. A test that built its own XML and matched it with a
regular expression would prove the extension agrees with the test. Driving it with the standard
library's client proves the documents are XML-RPC.

## Steps

1. **`extensions/seahaven-xmlrpc/pyproject.toml`** — a workspace member, laid out like
   `worlds/projecttracker`: hatchling, `requires-python = ">=3.14"`, `dependencies = ["seahaven"]`
   with `[tool.uv.sources] seahaven = { workspace = true }`, and its own
   `[tool.pytest.ini_options]` so the directory is the rootdir of its own suite:

   ```toml
   testpaths = ["tests"]
   addopts = "-q --seahaven-world tracker_rpc:world"
   pythonpath = ["tests"]
   ```

   `--seahaven-world` is the plugin's override, and this is exactly the layout it exists for: the
   project's `[project] name` is the *extension*, and the world the fixtures act on is a test asset
   beside it. `pythonpath` is what makes `tracker_rpc` importable from the rootdir.

2. **Root `pyproject.toml`** — `[tool.uv.workspace] members` gains `"extensions/*"`; the dev group
   gains `seahaven-xmlrpc` with a `[tool.uv.sources]` entry, so the workspace environment has it the
   way it has `projecttracker`; `[tool.ruff] src` gains the extension's `src` and `tests`;
   `[tool.ty.src] include` gains `"extensions"`. `uv.lock` is relocked — additively, the new member
   only; the pins `BACKLOG.md` B17 is about are not touched.

3. **`extensions/seahaven-xmlrpc/src/seahaven_xmlrpc/faults.py`** — the fault vocabulary.

   ```python
   PARSE_ERROR = -32700
   INVALID_REQUEST = -32600
   METHOD_NOT_FOUND = -32601
   INVALID_PARAMS = -32602
   INTERNAL_ERROR = -32603
   APPLICATION_ERROR = -32500

   class XmlRpcFault(seahaven.ToolError):
       def __init__(self, fault_code: int, fault_string: str) -> None: ...
       def document(self) -> str: ...

   def fault_document(fault_code: int, fault_string: str) -> str: ...
   ```

   The negative codes are Dan Libby's "XML-RPC Fault Code Interoperability" set, which is what
   XML-RPC implementations agree on; a world's own methods raise their own positive codes, which is
   what real XML-RPC products do. `XmlRpcFault` subclasses `ToolError` because §21 says an extension
   may, and because a world that registers the tool *without* the middleware should still get
   something an agent can read rather than a traceback.

4. **`extensions/seahaven-xmlrpc/src/seahaven_xmlrpc/documents.py`** — the two directions of the wire.

   ```python
   def parse_method_call(body: str) -> tuple[str, tuple[Any, ...]]: ...
   def render_response(value: Any, *, method: str, allow_none: bool = False) -> str: ...
   ```

   `parse_method_call` refuses a document type declaration before it parses anything: `xmlrpc.client`
   is expat with entity expansion on, and the document here is written by the agent under test, so a
   billion-laughs document would be a denial of service against the whole process. Everything expat,
   `ResponseError` or a value conversion raises becomes `PARSE_ERROR`; a document with no
   `methodName` — a `methodResponse`, or a `methodCall` missing it — becomes `INVALID_REQUEST`.

   `render_response` turns a `TypeError` out of `dumps` into a `WorldBug`: a handler that returned a
   value XML-RPC cannot carry is the author's mistake, not the agent's, and that is the framework's
   own rule for a tool result.

5. **`extensions/seahaven-xmlrpc/src/seahaven_xmlrpc/tool.py`** — the factory.

   ```python
   type MethodHandler = Callable[..., Any]

   def xmlrpc_call(
       *,
       methods: Mapping[str, MethodHandler],
       name: str = "xmlrpc_call",
       description: str | None = None,
       transaction: bool = True,
       allow_none: bool = False,
       max_body: int = MAX_BODY,
       log_calls: bool = False,
   ) -> Tool: ...
   ```

   Everything that can be wrong with the registration is found at factory time, which is the rule
   `Tool.from_function` and `run_sql` already follow: no methods, a method with no name, a handler
   that is not callable, a handler whose signature cannot take the context. Each raises `WorldBug`.

   `max_body` is spelled as `Field(max_length=...)` on the `body` argument rather than as a check in
   the function, so the limit is in the published JSON schema and an oversized document is an
   ordinary `ArgumentError` the world's own error handler restates.

   Per call: parse, look the method up (`METHOD_NOT_FOUND`), bind the parameters against the
   handler's signature (`INVALID_PARAMS`) — `signature.bind`, not a bare `except TypeError` around
   the call, which would misreport a `TypeError` raised *inside* a handler as a parameter error —
   log if the world asked for it, call, render.

6. **`extensions/seahaven-xmlrpc/src/seahaven_xmlrpc/middleware.py`** —
   `render_faults(*, tools, db_fault=APPLICATION_ERROR, db_message=None) -> Middleware`.
   Named tools only: a `DbError` under a tool that
   is not an XML-RPC endpoint is the world's business and must reach the world's handler unchanged.
   `XmlRpcFault` and `DbError` are the only two it catches; a `WorldBug`, an `ArgumentError` and any
   other exception go past it, because an XML-RPC fault is a *protocol* outcome and those are not.

7. **`extensions/seahaven-xmlrpc/src/seahaven_xmlrpc/call_log.py`** — `CALL_LOG_DDL` (one STRICT
   table with an explicit primary key and no wall-clock default, so a world that includes it passes
   SH101–SH103), `remember_client` (the startup hook) and `record` (the insert the tool makes when
   `log_calls=True`). A faulted call rolls its log row back with everything else it did,
   which is the honest behaviour and is tested.

8. **`extensions/seahaven-xmlrpc/src/seahaven_xmlrpc/__init__.py`** — the extension's whole public
   surface, and a module docstring that is the worked example: the five lines a world writes to
   register it.

9. **`extensions/seahaven-xmlrpc/tests/tracker_rpc/`** — the copy of ProjectTracker, in the §2.1
   layout so that `seahaven check` has something to check: `world.py` (ProjectTracker's schema via
   `seahaven.sql_files("projecttracker", "schema")` plus `CALL_LOG_DDL`), `methods.py` (the handlers
   over `users`, plus `system.listMethods`), `startup.py`, `tools/rpc.py` (the tool, registered under
   the name `rpc` to show the world choosing it), `middleware/error_handler.py` (ProjectTracker's own
   handler, imported and registered) and `middleware/faults.py` (`render_faults(tools=["rpc"])`),
   registered in that order so the product's handler is outermost.

10. **`extensions/seahaven-xmlrpc/README.md`** — what the package is, the five-line registration, and
    the ordering rule for the two middleware.

11. **`src/seahaven/docs/extensions.md`** — one sentence naming where the example lives. The page's
    prose is Phase 12's and stays a stub.

12. **`.github/workflows/ci.yml`** — one step, `uv run pytest extensions/seahaven-xmlrpc`, beside the
    reference world's.

## Tests

All under `extensions/seahaven-xmlrpc/tests/`, all on the plugin's `world` and `instance` fixtures,
all driving the endpoint with `xmlrpc.client` as the client. 55 in total.

**`conftest.py`** — `NOW` and `RPC`; `method_call(method, *params)`; `rpc(instance, method, *params)`
which calls the tool and loads the answer, raising `xmlrpc.client.Fault`; `probe`, a throwaway world
around the handlers a copy of ProjectTracker should not carry.

**`test_dispatch.py`** — the call path.

- `test_a_method_call_reaches_the_handler_and_comes_back_as_a_method_response`.
- `test_every_xml_rpc_type_survives_the_round_trip` — int, string, boolean, double, array, struct,
  and text XML has to escape.
- `test_a_handler_reads_the_instances_clock_and_its_ids`.
- `test_the_method_namespace_is_the_protocols_and_not_the_tool_registrys` — one tool in the listing,
  five methods behind it.
- `test_a_method_nobody_registered_is_a_method_not_found_fault`.
- `test_parameters_the_handler_cannot_take_are_an_invalid_params_fault` — too few, too many.
- `test_a_type_error_inside_a_handler_is_not_reported_as_a_parameter_error` — why `signature.bind`.
- `test_a_document_that_is_not_well_formed_is_a_parse_error`.
- `test_a_document_with_no_method_name_is_an_invalid_request`.
- `test_a_response_document_sent_as_a_call_is_an_invalid_request` — and the fault it carried is not
  re-raised as this server's.
- `test_a_document_type_declaration_is_refused_before_it_is_parsed` — the billion-laughs guard, with
  an entity that would otherwise expand.
- `test_a_body_longer_than_the_tool_accepts_is_an_argument_error` and
  `test_an_empty_body_is_refused_by_the_argument_model` — a malformed *tool call* is not an XML-RPC
  request, so both are the world's `INVALID_INPUT` and not a fault.

**`test_registration.py`** — what the factory publishes and what it refuses.

- `test_the_tool_publishes_a_schema_an_agent_can_read` — one `body` string, capped, required.
- `test_the_default_description_names_the_methods` — the surface the JSON schema cannot carry.
- `test_the_world_chooses_the_name_the_description_and_the_cap`.
- `test_the_method_table_is_copied_and_not_kept`.
- `test_the_registered_tool_is_the_one_the_factory_built`.
- `test_an_endpoint_that_cannot_work_is_refused_at_registration` — seven ways: no methods, a nameless
  method, a whitespace name, a handler that is not callable, a handler that cannot take the context,
  a handler taking it by keyword, a cap of nothing.
- `test_a_handler_taking_the_parameters_variadically_is_accepted`.
- `test_render_faults_must_name_a_tool`.

**`test_faults.py`**

- `test_a_fault_a_handler_raises_carries_the_worlds_own_code` — `404` from `user.get`.
- `test_a_db_error_under_a_handler_comes_back_as_an_application_error_fault` — a `UNIQUE` violation
  and a `CHECK` violation; the `DbError`'s own message and never SQLite's.
- `test_a_faulted_call_writes_nothing` — the rows and the changeset, for the `users` table.
- `test_a_handler_that_returns_what_xml_rpc_cannot_carry_is_a_world_bug` — a set, and an int too big
  for XML-RPC's 32-bit `<int>`.
- `test_an_unexpected_exception_reaches_the_worlds_error_handler` — ProjectTracker's `INTERNAL`.
- `test_the_middleware_leaves_every_other_tool_alone`.
- `test_without_the_middleware_a_fault_is_raised_rather_than_rendered`.
- `test_the_products_error_handler_stays_outermost` — the chain read back by name, and a fault
  document coming out of it.

**`test_call_log.py`**

- `test_a_call_is_logged_with_the_method_and_the_instances_clock`.
- `test_the_client_defaults_when_reset_does_not_name_one`.
- `test_the_client_comes_from_the_reset_argument` — `xmlrpc_client=` on the marker.
- `test_a_faulted_call_leaves_no_log_row`.
- `test_the_log_is_off_unless_the_world_asks_for_it` — on a world with no log table, which is the
  only way to assert the default from outside.

**`test_contract.py`** — §21 itself, which is what this package exists to prove.

- `test_importing_the_extension_registers_nothing`.
- `test_seahaven_does_not_import_the_extension` — read off the framework's source, because
  `sys.modules` has been polluted by the test that reads it.
- `test_the_extension_does_not_monkeypatch_the_framework`.
- `test_the_extension_imports_only_published_names` — every name taken from Seahaven is in the
  `__all__` of the module it came from.
- `test_the_extension_depends_on_nothing_but_seahaven_and_the_standard_library`.
- `test_the_world_built_on_the_extension_passes_seahaven_check`.
- `test_the_extensions_table_is_in_the_worlds_schema_hash` — §21 point 4's cost, made concrete.

## Two things implementation added to the plan

1. **`OverflowError` is the same mistake as a `TypeError`.** XML-RPC's `<int>` is 32 bits, so a
   handler answering with a rowid or a millisecond timestamp raises `OverflowError` out of `dumps`,
   not `TypeError`. Both are the author's mistake and both are a `WorldBug`.

2. **The DOCTYPE guard has a false positive and keeps it.** It is a text search over the whole
   document, so a document carrying the characters `<!DOCTYPE` anywhere is refused. The alternative
   is to parse first and decide afterwards, which is the parse the guard exists to prevent; the
   trade is named in the code, in the same way `lint/ddl.py` names its `'now'` one.

## What the code review changed

Four rounds. Round 1 found four defects around the edges of a design it endorsed; rounds 2 and 3
each found the same Critical again, one layer further out, until the check stopped being a list and
became a rule; round 4 came back clean.

### Round 1

Round 1 endorsed the central claim — that fault rendering is a middleware because `invoke`'s
transaction is what rolls a faulted handler's writes back — after verifying it in both directions,
and found that the edges around it did not hold up.

- **Classifying by exception type was wrong, and the fix is to stop doing it.** `parse_method_call`
  caught `(ExpatError, ResponseError, ValueError)`, which is not what `xmlrpc.client.loads` raises:
  a `<struct>` member with a `<value>` and no `<name>` is an `IndexError`, and `<boolean>7</boolean>`
  is a `TypeError`. Both are well-formed XML that is not XML-RPC, both are the caller's document, and
  both escaped as world bugs — the agent was told the *server* broke and the framework logged a
  traceback, which an agent could provoke at will from a 200-byte string. The list is nowhere
  documented and a future release may add to it, so the clause is now `except Exception`: the only
  thing inside the `try` is the caller's document, so anything that comes out of it is about that
  document by definition. Both cases are tested.
- **`log_calls=True` without `CALL_LOG_DDL` masqueraded as a protocol outcome.** "no such table" is a
  `DbError`, `render_faults` rendered it as a plausible `APPLICATION_ERROR` fault, and nothing was
  logged, because a `DbError` is a `ToolError` and `invoke` re-raises those without a traceback.
  Every call of such an eval would answer with what looks like the product refusing. `record` now
  re-raises a missing-table `DbError` as a `WorldBug` naming the fix — which goes past `render_faults`
  and past the world's handler untouched, and surfaces on the first call.
- **`transaction=False` is refused rather than documented.** It was a pass-through to
  `Tool.from_function`, and it switches off the one guarantee the endpoint is built on: verified, a
  handler that inserts a row and then raises still gets its fault rendered *and* commits the row. The
  parameter is kept and the value refused, rather than dropped, because a world author will try the
  option every other factory has and a `WorldBug` explaining why is worth more than a `TypeError`.
- **The monkeypatch test did not test what its name said.** It flagged only `seahaven.X = ...`, which
  is the one spelling nobody would use; `seahaven.world.World.tool = ...`, `setattr(...)`,
  `Tool.validate = ...` after `from seahaven import Tool`, and an annotated or augmented assignment
  all passed unflagged. The walk now roots an attribute chain at its `Name` and asks whether that
  name is bound to something of Seahaven's, reading the module's own imports for the answer, and
  covers `Assign`, `AnnAssign`, `AugAssign` and `setattr`/`delattr`. A second test runs it over
  source that *does* patch the framework, so a walk that flagged nothing could no longer pass.

Smaller points, all taken:

- The nesting bomb, the other half of the XML-bomb family the DOCTYPE comment reasons about, was
  unmentioned: a 1,400-deep array is ~60 KB, inside the default cap, parses fine (expat and
  `Unmarshaller` are iterative) and then blows the stack in `dumps` when a handler echoes it.
  `render_response` now answers a `RecursionError` with an `INTERNAL_ERROR` fault — the nesting came
  from the caller, so it is not charged to the author and does not reach the framework as a logged
  traceback.
- The DOCTYPE comment overstated its own false positive: a conforming client escapes `<`, so a
  parameter carrying `<!DOCTYPE` passes cleanly and what is left to trip on is CDATA or a hand-built
  document. Corrected, because the comment is written to be the thing a reader trusts.
- `test_call_log.py` ordered by `called_at` on a frozen clock, which is not a total order. `rowid`.
- Nothing asserted the packaging half of "the dependency graph is extension → seahaven"; one
  assertion over `importlib.metadata.distribution("seahaven").requires` closes it.
- The README said how to register the pair and not how a method signals failure, so an author would
  reach for their world's own `ToolError` and get a Seahaven error shape out of an endpoint whose
  contract is fault documents. One sentence.

### Round 2

Round 2 re-ran every round-1 reproducer and cleared the two new behaviours, then found the same
failure shape once more, from the other side.

- **The inbound and outbound sides disagreed about what XML-RPC carries.**
  `xmlrpc.client.loads` takes no `allow_none` at all and unmarshals `<nil/>` to `None` whatever the
  server thinks, and it converts `<int>`/`<i4>` with a bare `int()` while `dumps` enforces the 32-bit
  bounds. So a document the parser accepted could not be rendered: through any handler that answers
  with what it was given — `tracker.ping` is exactly that shape, and `render_response`'s own
  docstring names the `*.update` case — `<nil/>` and an out-of-range integer became `TypeError` and
  `OverflowError` out of `dumps`, which this module reads as the *author's* mistake. The agent was
  told the world broke over a value it had chosen itself, with a logged traceback, from a ~180-byte
  document against the shipped example world.

  Fixed where the design already puts this judgement: `parse_method_call` takes `allow_none` and
  walks the parsed parameters, refusing an unenabled `None` as `INVALID_REQUEST` (the document is
  well formed; `<nil/>` is an extension this endpoint does not speak) and an out-of-range integer as
  `PARSE_ERROR`. The walk is iterative with an explicit stack, because a deeply nested document is
  one this parser accepts by design and a recursive walk would fall over on exactly the case the
  check exists to keep off the author's side of the line. Tested over all five reproducing bodies,
  plus a round trip proving `allow_none=True` carries `<nil/>` both ways.

  This also restores the truth of `render_response`'s docstring: with the inbound check in place, a
  `TypeError` or an `OverflowError` out of `dumps` really does mean the handler picked the type.

### Round 3

Round 3 verified the round-2 fix on all five reproducers, confirmed the walk is genuinely
non-recursive, confirmed `bool`-before-`int` and the bounds against `dump_long`, and then found the
same Critical on two more tags -- along with the reason it was still there.

- **The check named the failures instead of the successes, which is the mistake round 1 diagnosed.**
  `_check_values` covered `None` and an out-of-range `int`; `loads` also produces a `Decimal` (from
  `<bigdecimal>`, which is not in the XML-RPC specification at all and whose dispatch entry is
  registered unconditionally) and a non-string struct key (`end_struct` pairs the value stack blindly
  two at a time, so a `<member>` with one `<name>` and three `<value>`s yields `{'a': 1, 2: 3}`).
  Both are refused by `dumps`, so both reproduced the round-2 outcome exactly: `WorldBug`, a logged
  traceback, past `render_faults` and past the world's handler, from a document under 250 bytes.

  The comment written in round 1 about `except (ExpatError, ResponseError, ValueError)` -- "nowhere
  documented, and a future release may add a sixth" -- applies word for word to
  `Unmarshaller.dispatch`, which is exactly how `<bigdecimal>` arrived. So the walk is now an
  allowlist: what XML-RPC *carries* is a closed set the specification names, and what `xmlrpc.client`
  *emits* is neither closed nor documented. `_CARRIED` is `str`, `float`, `DateTime` and `Binary`;
  `None`, `bool` and `int` are decided by their own branches, `list` and `dict` are walked, and
  everything else is a `PARSE_ERROR` naming the set. A struct is walked as pairs so a key that is not
  a string is refused as `INVALID_REQUEST` -- the comment claiming a struct's names "are strings
  whatever they say" was true of a conforming document and this one is the caller's.

  Three cases join the parametrised list, and one test adds a tag to `Unmarshaller.dispatch` that
  unmarshals to a type `dumps` cannot write -- which is what `<bigdecimal>` was before anyone noticed
  -- so the next addition to the standard library fails here rather than in `dumps`. The round-trip
  test gained `<base64>` and `<dateTime.iso8601>` cases, which is the bound in the other direction:
  nothing a conforming client can send is refused.

- The range message named `<int>` even when the caller sent `<i8>` or `<biginteger>`; it now names
  the 32-bit range itself.

### Round 4

Clean. The verification read CPython 3.14's `xmlrpc/client.py`: it enumerated the ten types the stock
`Unmarshaller` can construct against `_CARRIED` and the branch types, checked every raise site inside
`dumps` is closed -- including that `dump_long`'s bounds are exactly complementary with no off-by-one
at the 32-bit edges, and that `dump_struct`'s key predicate is character-for-character the walk's --
exhaustively enumerated the struct-key path, ran about 60,000 randomised nested documents with no
`WorldBug` and no false refusal, and mutation-tested each `continue` to confirm it is load bearing.
The allowlist holds as a property rather than as a longer list.

