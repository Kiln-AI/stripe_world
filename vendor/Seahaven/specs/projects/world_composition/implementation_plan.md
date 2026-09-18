---
status: complete
---

# Implementation Plan: World Composition

Composition is built in the phases below, each one reviewable unit referencing `architecture.md`
(§ numbers) and `functional_spec.md` (FS §). Nothing here restates them.

The framework is implemented: all thirteen phases of `../seahaven_framework/implementation_plan.md`
are complete on `main`, and the architecture was verified against that code. Every phase below
lands on it and passes the checks `AGENTS.md` lists (`ruff format`, `ruff check`, `ty check`, the
three pytest suites, the licence check) before it is committed.

## Phases

- [x] **Phase 1: the composition model and the seal.** `composition.py`: `AddedWorld`, `NodeKey`,
      `Node`, `Contributed`, `Composition` (§2); `World.add_world` with the five call-site checks
      (§3); resolution with scope propagation, BFS canonical paths, alias edges, key-wise bound
      startup and per-node-key contribution (§4.1 steps 1–5); the registration epoch and
      `World.composition()` (§4.2); the six seal checks and `attached_limit()` (§4.4); the flat
      tool surface and `Instance.tools()` (§5). No chains, no instances yet.
      The composite test world under `tests/worlds/` (§17) with `test_composition.py` and
      `test_add_world.py`.
- [x] **Phase 2: contexts, handles, composite instances and dispatch.** `handles.py` (`Frame`,
      `Worlds`, `WorldHandle`); `Ctx` generic with `worlds` and `with_call(worlds=)` (§6.3);
      `Call.node`; `build_route_chain` and per-node chains on `Node` (§4.1 step 6, §7.6);
      `NodeRuntime`, N files named by path, N connections and sessions, `node_seed`, the pinned
      node set (§6.1–6.2); tree startup hooks with merged bound kwargs and N transactions (§6.4);
      `_held()` with the depth counter, the epoch and the `Frame`; the in-call thread-local; node
      dispatch, nested `handle.call`, `bulk` over N transactions (§7); the node path and `internal`
      marker in the log line. Blank composite instances only; fixtures are phase 4.
      `test_composite_instance.py`, `test_composite_dispatch.py`. Needs phase 1.
- [x] **Phase 3: typed access.** `Tool[**P, R]`; the `call` overloads on `Instance` and
      `WorldHandle`; `by_fn` resolution at the root and within a handle's subtree, with the
      ambiguity error (§8.1–8.2); `Ctx[X]` accepted by the registration check and the `Worlds`
      typing base (§8.3); `invoke` returns the tool's original object after proving it serialises,
      and `control.dispatch` serialises its own (§8.4). The `ty` gate on `Concatenate` +
      `ParamSpec` overloads in CI (§16, last row). `test_typed_call.py`. Needs phase 2.
- [x] **Phase 4: fixtures, inspection, changes, the report.** `NodeMeta`, `FixtureMeta` version 2,
      per-node freeze, verify and `check_composition` (§11); `open_inspection(attachments=)` with
      the attach-before-authorizer order and `Instance.inspect()` over every node (§9);
      `Change.world` and per-node rendering (§10); `Instance.composition()` (§12); control tools
      over the composition. `test_composite_fixtures.py`, `test_composite_inspection.py`,
      `test_composite_changes.py`. Needs phase 2.
- [x] **Phase 5: OpenEnv and `seahaven check`.** `SeahavenState.composition` (§12; `openenv/env.py`
      serialising the observation's result landed in phase 3, with the `invoke` change that made it
      necessary); `check` seals first and reports seal
      errors as SH504 (§4.3); SH206–SH209, SH406, SH502–SH504 and per-node SH401–SH405 (§14),
      `lint/world.py` new. Lint fixture pairs under `tests/worlds/`; the composite OpenEnv
      end-to-end test (§17). Needs phases 3 and 4.
- [x] **Phase 6: docs.** A `composition.md` page under `src/seahaven/docs/` and the FS §14 README
      section, with examples that run under `tests/test_docs_examples.py`; the authoring-docs
      requirements of FS §13 in `authoring.md` (prefer an added world's tools over direct SQL;
      prefixes and lists match the client's real surface; never assume sole writership; return
      models, not dicts); `reference/api.md` for `add_world`, `Worlds`, `WorldHandle`, `Ctx[X]` and
      the `call` overloads; `Change.world` in `reference/api.md`'s `## Change` block and in
      `concepts.md`'s description of a change's fields; the new codes in `reference/lints.md`; the
      scaffold's `AGENTS.md` template. Needs phase 5.
- [x] **Phase 7: benchmark.** A composite workload in `bench/` over the test composite world —
      one-row read and write mix against a three-node tree — run beside the existing ProjectTracker
      workloads, with results in `bench/results/latest.md` so the per-node floor §15 assumes is a
      number. Never a gate. Needs phase 4.
- [x] **Phase 8: Backlog.** Review open backlog items with the user, then close or dismiss each
      through the standard phase flow.

## Order

1 → 2 is strict, and 2 → 3 and 2 → 4 are strict; 3 and 4 can proceed in parallel; 5 waits for
both; 6 and 7 are last and independent of each other. The single-node path is already proven by
ProjectTracker and the executed docs, which is what makes it safe for phase 2 to turn it into the
degenerate case of one code path (§1): every existing suite runs unchanged after every phase.
