---
status: complete
---

# Implementation Plan: Seahaven

Seahaven is built in the phases below. Each phase is one reviewable unit and references the
specs; nothing here restates them.

## Phases

- [x] **Phase 1: skeleton and the runtime database layer.** Package layout (`architecture.md` §1),
      `pyproject.toml` with the `serve` extra, uv, ruff, ty, pytest on 3.14 in CI, the licence check
      for dependencies. `db.py`, `clock.py`, `ids.py`, `sandbox.py`, `errors.py` per
      `components/runtime_db.md` and `world_and_dispatch.md` §5, with the sandbox attack suite of
      `architecture.md` §10. No `LICENSE` file (sign-off gated).
- [x] **Phase 2: world definition and dispatch.** `world.py`, `tool.py`, `call.py`, `ctx.py` per
      `components/world_and_dispatch.md`: registration verbs, pydantic argument models (strict),
      the middleware chain, `invoke`, serialisation, the framework errors. Tests as listed there.
- [x] **Phase 3: fixtures, instances, changesets.** `fixtures.py`, `instances.py` (creation, startup
      hooks, the lock discipline and the concurrency gate, working directory and sweep, `bulk`,
      destroy), `changes.py`, `conformance.py` per `components/fixtures_instances.md`. In-process API
      complete after this phase: `world.instance(...)`, `call`, `inspect`, `changes`, `freeze`.
- [x] **Phase 4: helpers and control tools.** `helpers/run_sql.py`, `helpers/describe_schema.py`,
      `control.py`, FTS5 awareness per `components/helpers_and_control.md`.
- [x] **Phase 5: ProjectTracker placeholder.** `worlds/projecttracker/` in the section 2.1 layout
      with the schema's `users` table only, one `ping` tool, `errors.py`, the error handler and an
      `empty` fixture: just enough to be a real package that later phases build and serve. The full
      world is phase 10.
- [x] **Phase 6: OpenEnv.** `openenv/env.py`, `openenv/client.py`, `app`, `serve.py` per
      `components/openenv.md`: environment class, `SeahavenClient`, end-to-end tests with the stock
      and typed clients against the placeholder, the 500-session smoke test. The placeholder needs
      only its `openenv_app.py`; the hub files are Phase 7's, as template output.
- [x] **Phase 7: CLI and `seahaven check`.** `cli/` and `lint/` per `components/cli_and_check.md`:
      `new` with templates, `--hub` and the hub files it writes, `check` with every lint code,
      `docs`, `fixture`, `serve`, world discovery. A scaffolded world passes `check` and its own
      tests.
- [x] **Phase 8: pytest plugin and the docs skeleton.** `pytest_plugin.py` and the `docs/` layout
      with stub pages, per `components/pytest_and_docs.md`. ProjectTracker's tests move to the
      plugin.
- [x] **Phase 9: the XML-RPC example extension.** A separate package in the repository proving the
      extension contract (`functional_spec.md` §21): tool factory, fault-mapping middleware, tests
      on a copy of ProjectTracker.
- [x] **Phase 10: ProjectTracker, in full.** `components/projecttracker.md`: the schema, errors,
      the 25 world tools and the two helpers, the fixture generator and the three fixtures, tests on
      the plugin. The first real consumer of everything before it; API friction found here is fixed
      in the framework, not worked around in the world.
- [x] **Phase 11: benchmark and tuning.** `bench/` with the two workloads over `agency`; a tuning
      sweep over the concurrency gate's default (cold and warm cache) which confirms or adjusts it;
      results committed as `bench/results/latest.md`.
- [x] **Phase 12: docs.** The prose and reference pages, hand-written, including the ProjectTracker
      walkthrough and every lint code, plus the README. The authoring-experience phase
      (`functional_spec.md` §20).
- [x] **Phase 13: OSS readiness (sign-off gated).** Contribution guide, licence file, package
      publication, hub publication of ProjectTracker. Nothing in this phase starts without explicit
      maintainer sign-off. **Closed with the contribution guide only.** Publication is not part of
      this project: neither the package nor the hub is published here, and the `seahaven` name on
      PyPI keeps the placeholder it already holds (`BACKLOG.md` B22 records what that costs a
      reader). The licence file was not written and is still gated by `AGENTS.md`; it is a
      maintainer decision, not a phase deliverable, and nothing downstream waits on it.

## Order and dependencies

Phases are sequential; the placeholder world (5) exists so OpenEnv (6), the CLI (7) and the
plugin (8) have a real package to serve, check and test, and the full ProjectTracker (10) comes
after the framework is whole. Phase 11 waits for phases 3, 6 and 10 (gate, server, world). Phases 12
and 13 are last.
