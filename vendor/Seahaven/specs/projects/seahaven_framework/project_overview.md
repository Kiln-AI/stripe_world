---
status: complete
---

# Seahaven: a framework for synthetic worlds

Seahaven is a Python framework for building **synthetic worlds**: faithful, stateful mocks of the
tool surface a real company's agent works against. An agent inside a Seahaven world reads and
writes through the same tools it would have in production, against state that responds the way the
real system does, without touching the real system. Worlds are cheap to stand up by the hundred,
reset to a known state in milliseconds, and are inspectable by the evaluation that runs them.

"Seahaven" is the framework, the Python package and the CLI. This overview states what it is for
and what it is; `functional_spec.md` states how it behaves.

## 1. Terminology

- **World:** the code for one synthetic world. A Python package. Mocks one company's tool surface.
  Shared by every fixture and instance of that world.
- **Fixture:** a named, immutable initial state a world instance is created from. A world has many
  fixtures (a fresh account, a three-person startup, a twelve-person agency).
- **Instance:** a running, independent copy of a world, created from a fixture. Agents write to it,
  it diverges from the fixture, and it is destroyed when the eval is done.
- **Tool:** one operation the agent can call, with a name, a JSON schema for its arguments and a
  JSON-serialisable result. A world's agent-facing surface is exactly its list of tools.
- **Extension:** an ordinary Python package that adds a capability to a world (a tool factory, a
  middleware, DDL) through the same seams every world uses.
- **Seahaven:** the framework itself. A world an agent lives in without knowing it is not real.

## 2. Why synthetic worlds

Optimising an agent harness, whether by hand or by an automated optimisation loop, needs a read-write
environment to run against: thousands of experiments, in parallel, each starting from a known state,
each inspectable afterwards. The real system cannot do this. It is expensive to set up, impossible to
reset, throttled in staging, and shared. A synthetic world can: it is a file copy to create, a file
delete to destroy, and a SQL query to inspect.

The agents in question are ordinary tool-calling LLM agents over a large but tractable surface: a
REST API, a database, a search tool. Not vision, not GUIs, not world models. What varies is the shape
of the tools: one tool per endpoint, a generic API-call tool, a SQL or query-language tool,
presentation tools that only need to appear in the trace. Seahaven exists so that reproducing such a
surface, quirks included, is a small amount of world code over a well-tested framework, not a bespoke
project each time.

**The mock seam is the tool layer.** A company has a fixed set of tools its agent can call. Whatever
sits between the agent and those tools (wrapper tools, tool search, a generic `call_api`, subagents)
is harness work and lives in the harness. A Seahaven world mocks the company's real tools and nothing
above them. This is also why Seahaven has no tool-projection layer: a world is its tool list, and
reshaping that list for an agent is the caller's job, done after the instance exists.

**Worlds mock existing systems.** An agent optimised in an invented world does not transfer back to
the real one, so a world reproduces the real API and its quirks. Fidelity work is where the per-world
effort goes; the framework's job is to make everything else free.

## 3. Open source, built to be extended

Seahaven is open source (MIT): REST-shaped tools over SQLite state, fixtures, instances, a
controllable clock, changesets, a sandboxed SQL helper, an OpenEnv environment, the CLI, a pytest
plugin, bundled docs and one reference world.

Extensibility is a first-class requirement, documented and stable from V1. A framework for hundreds
of worlds meets protocols and query languages it does not know, from an obscure internal RPC format
to a vendor's query dialect, and every one of those must be addable as an ordinary Python package,
by anyone, without forking the framework. Everyone builds on the same interface: the seams every
world uses (the instance context, middleware, instance startup, DDL as text, and the sandbox that
contains agent SQL). No privileged API exists. The repository ships an XML-RPC extension as the
worked example.

## 4. Goals

1. **Cheap to run.** Creating an instance is a file copy; destroying it is a delete. Hundreds of
   instances per process, minute-long lifetimes, tool calls in low milliseconds. About 3,000 one-row
   reads per second per process is the baseline, which is more than an LLM-driven agent can consume;
   the framework does not chase throughput beyond that.
2. **Small cost to create a world.** A world is a schema, a list of tools, fixtures and tests. The
   framework owns state, clock, IDs, validation, errors, lifecycle, transport and tooling. The
   author, human or agent, makes no architectural decisions.
3. **Low risk of bugs in worlds.** The hard logic is in the framework and tested once. A world's own
   logic is about reproducing one product, and the framework's lints catch the classes of mistake an
   authoring agent makes silently (wall-clock reads, a forgotten module, a fixture out of step with
   the schema).
4. **Testable.** Worlds carry ordinary tests; Seahaven ships a pytest plugin. Conformance testing
   against a real staging system is not a framework feature; it needs a real target, and a real
   staging state differs anyway, so it belongs to whoever builds a world against one.
5. **Time is part of the world.** Every instance runs on a framework-controlled clock frozen at its
   fixture's `now`. Fixture data is relative to that moment, and timestamps the agent's writes
   produce come from it. Exactly two things read the wall clock: a blank instance's default `now`,
   and a fixture's `created_at` at freeze.
6. **Fixtures are immutable and enforced.** A fixture is minted only by freezing an instance, is
   never opened in place, and is hash-verified before use. Fork, never edit.
7. **Reproducible by construction, not by force.** A frozen clock, a seeded random source and id
   generator per instance, and deterministic ordering are provided; a world that uses them replays
   the same eval on the same fixture to the same result. The framework does not chase absolute
   determinism by restricting APIs: every eval drives a world differently, and that is a losing
   battle.
8. **Correct over fast.** Seahaven runs high-cost LLM experiments; its CPU is far cheaper than the
   data an error corrupts. Requests are never dropped or refused for capacity. Budgets and caps are
   unbounded unless a world sets them to mimic the real product. Callers and world code are trusted.
9. **Standard where a standard exists.** The remote lifecycle and transport are OpenEnv, so a
   Seahaven world publishes to open environment hubs and any OpenEnv client drives it. The
   in-process Python API is Seahaven's own and is what the OpenEnv environment wraps.
10. **Built for AI authors.** Version-matched docs ship inside the package, found with
    `seahaven docs`; each world's `AGENTS.md` points at them; `seahaven check` turns every framework
    rule into a lint with a named fix. Conventions are bought with docs, a reference world and machine validation, because a
    new framework has no training corpus.

## 5. Shape of the framework

- **Language and runtime:** Python 3.14+. APSW is the only SQLite binding. Tools are synchronous
  functions, run on the thread that brought the call; a concurrency gate bounds how many run at
  once, and queues the rest rather than refusing any. One connection per instance; many instances
  per process.
- **State:** one SQLite file per instance. Schema is hand-written SQLite DDL, linted (STRICT tables,
  explicit primary keys, no wall-clock defaults). Timestamps are ISO 8601 UTC text. Every connection
  overrides SQLite's time functions with the instance clock.
- **World definition:** Flask-style registration on one `World` object in `world.py`. Three verbs,
  each a decorator or a plain call: `world.tool`, `world.middleware`, `world.instance_startup`. A
  tool receives an instance context (`ctx`) carrying the database wrapper, clock, ID generator,
  declared errors and per-instance state.
- **Extension seams:** the instance context, middleware (a structurally typed callable shape,
  checked at registration), per-instance startup (which also receives the `reset()` keyword
  arguments beyond `fixture`, `seed` and `now`), DDL as text, and the sandbox that contains agent
  SQL. An extension is an ordinary Python package that depends on `seahaven` and uses its public
  API; the world imports the extension, and Seahaven never imports an extension.
- **Error wrapper:** an app-wide error translation middleware is the standard middleware in every
  world (`middleware/error_handler.py`). Engine and framework errors are mapped to the world's
  declared error shapes before anything reaches the agent.
- **Fixtures and instances:** a fixture is a directory holding `state.sqlite` and `fixture.yaml`.
  Instances are file copies. `freeze` mints fixtures. Read-only inspection and changesets (what the
  agent changed) are the eval's view of an instance.
- **Transport:** OpenEnv-native. An instance is an OpenEnv session; `reset(fixture=, seed=)` creates
  it; tools are called through the `CallToolAction` / `ListToolsAction` shape with Seahaven's own
  dispatch; the session close destroys it. Every Seahaven environment supports concurrent sessions.
  Eval-facing reads (inspection SQL, changesets) are two control tools on the same connection,
  served only when `serve` is told to include them. MCP access is post-V1.
- **Tooling:** `seahaven new`, `check`, `fixture` (`freeze`, `fork`, `list`), `serve`; a pytest
  plugin with `world` and `instance` fixtures; uv, ruff, ty, pytest.
- **Fully typed.** The framework, the helpers and the reference world are fully typed Python with
  `ty` in CI as the guard. Seahaven does not impose typing on the worlds built
  with it. Its public API is typed primitives, not strings, because type errors are the fastest,
  most local signal an authoring agent acts on.
- **Reference world:** ProjectTracker, a fictional Linear/Jira-shaped issue tracker with three
  fixtures (`empty`, `small_startup`, `agency`). One world, deep enough for state-based grading.

The functional spec (`functional_spec.md`) states each of these precisely.

## 6. What is in V1

| Capability | Release |
|---|---|
| REST-shaped tools (one tool per operation, hand-written over SQLite) | V1 |
| Relational state on SQLite, DDL contract and lint, clock UDFs | V1 |
| Fixtures (freeze, fork, hash verification, sidecar) and instances | V1 |
| Static instance clock, seeded IDs and randomness | V1 |
| Sandboxed `run_sql` (SQLite dialect) and `describe_schema` helpers | V1 |
| Basic full-text search over FTS5 | V1 |
| App-wide error wrapper (standard middleware) | V1 |
| Extension seams: instance context, middleware, instance startup | V1 |
| Read-only inspection and changesets | V1 |
| OpenEnv environment, `serve`, control tools | V1 |
| CLI, pytest plugin, bundled docs | V1 (docs a later phase) |
| ProjectTracker reference world | V1 |
| XML-RPC example extension (the extensibility demonstration) | V1 |
| MCP access to a world | post-V1 |

## 7. Non-goals

- Hosting and isolation. Seahaven is a library that runs equally well locally and in a container
  someone else provisions. Runaway world code is not contained in-process; workers run trusted code.
- Conformance against a real staging system.
- A tool-projection layer, tool groups or reset-time tool filtering.
- A mutation-overlay feature. Per-instance setup is the eval's job through the world's tools, or
  post-V1 through `reset(startup_sql=...)`.
- A progressing clock, per-instance clock override, mocked tool responses: post-V1.
- Live external tools (web search, LLM-backed tools). The run configuration supplies them.
- Subagents, streaming, GUI replicas, actually delivering anything (webhooks, email, payments).
- Benchmarks to choose between designs. Choose on knowledge; benchmark to validate.
- A second reference world.

## 8. Constraints

- No copyleft in anything shipped: the runtime closure and every declared extra are checked in CI.
  Permissive, MPL-2.0 (copyleft per file) and CC0-1.0 pass; GPL, AGPL and LGPL never do, and an
  unrecognised licence fails rather than being assumed.

  *Corrected 2026-09-13 — this constraint read "Permissive-only runtime dependencies (MIT,
  Apache-2.0, BSD-class), checked in CI", which was both the wrong bar and the wrong scope: the
  `serve` extra is a runtime one and the gate did not look at it. Closes `BACKLOG.md` B15.*
- No real customer data in the repository, ever. The reference world is fictional; no real product's
  names, schema or error text.
- The framework never depends on a world or on where it runs.
- Nothing may depend on process-wide mutable state, so running one instance per process and
  free-threaded CPython stay open.

## 9. Consumers

- **World authors**, human or agent. Everything about authoring ergonomics is designed for an agent
  reading the bundled docs and writing a world.
- **Eval harnesses** drive worlds through the OpenEnv client and read state through the control
  tools on the same connection. A harness designs its hook against Seahaven's lifecycle API;
  Seahaven does not design the harness's hook.
- **Hub users and extension authors** run worlds from open environment hubs, drive them with any
  OpenEnv client, and build extensions on the documented seams.
- **Python clients** use the in-process API and the pytest plugin.
