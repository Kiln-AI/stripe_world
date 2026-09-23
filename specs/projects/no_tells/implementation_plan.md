---
status: complete
---

# Implementation Plan: No Tells

Ten phases. The ordering is set by three dependencies and one long pole.

**The long pole is the catalogue enumeration** (architecture §4.2): several hundred to a thousand
live MCP calls, once, and both the refusal model and discovery read its output. Phase 1 builds it
and starts it; it runs in the background while Phases 2–6 proceed, and Phase 7 is the first that
needs it finished.

**The three dependencies:** everything reads `ctx.state["account"]`, so Phase 2 comes before the
tools; ids need the account fragment, so Phase 3 follows Phase 2; and the tools are written against
the new return contract, so Phase 4 precedes Phase 5.

## How a phase is done

1. **Write the test first, from captured real behavior** — the probe artifacts, not this spec. Where
   a recording and a spec disagree, the recording wins and the spec is corrected in the same phase
   (functional spec §15).
2. **Name each test for the register id it closes** — `test_ts_04_read_takes_operation_id`.
3. **Implement until green**, then `uv run ruff format --check && uv run ruff check`, `uv run ty
   check`, `uv run pytest`, `uv run seahaven check`.
4. **Update the register's `Disposition` column** for the rows the phase touched — `closed`,
   `declared` or `not-a-tell`. The register is
   [`research/mcp-fidelity-probe/tells.md`](research/mcp-fidelity-probe/tells.md) and it is the only
   copy; every row starts `open`. A row left `open` after a phase that touched it is unfinished work.

A phase is not done until its register rows have dispositions and the full check list is clean.

## Phases

### Capture

- [x] **Phase 1: Capture and enumerate.** `tools_dev/enumerate_catalogue.py` with resume and
      `--sample` (architecture §4.2); commit `spec3.json` in full; capture the ten real tool schemas
      and descriptions to `tests/surface/real_tool_schemas.json`; write the surface-conformance
      harness (§8.1), `xfail` until the tools land. **Start the enumeration run at the end of this
      phase.** No world code changes — this phase only produces artifacts everything else reads.

### Foundations

- [x] **Phase 2: Account, startup and the `livemode` sweep.** `startup.py` builds
      `ctx.state["account"]`; the fresh-sandbox account object (§6.3); remove `livemode` from ~15
      `ResourceSpec` constants and inline literals and add it centrally in `serialize/fields.py`
      behind the four-object carve-out; derive the embedded `livemode=` error string. **Write the
      source-grep guard test before the sweep, not after.** The widest edit in the project.
- [x] **Phase 3: Ids.** The two measured formats, the per-prefix table, the account fragment from
      Phase 2's state, the timestamp group taken from the stamped time rather than "now", the
      version digit as a defaulted argument. Replaces the blanket 24-char assertion.

### The edge

- [x] **Phase 4: The envelope transform.** Rewrite `middleware/stripe_envelope.py` to unwrap a 2xx
      to its bare body and raise on anything else (architecture §2.5), and adapt the existing suite,
      which asserts `{status, body}` throughout. Tool signatures are untouched here on purpose: this
      isolates the one change with transaction semantics behind it. `call_stripe` keeps
      `ApiResponse`, since it bypasses middleware.
- [x] **Phase 5: The four API tools.** New signatures with operation-id addressing and the context
      parameters, `_descriptions.py` verbatim, `_context.py` validation and its two refusals.
      Surface conformance goes green for these four.
- [x] **Phase 6: The account tools.** `list_available_accounts_or_orgs` as a projection of Phase 2's
      state, `get_stripe_account_info` retained, `manage_stripe_accounts`. Surface conformance green
      for all eight registered tools.

### Behavior

- [x] **Phase 7: The refusal model.** *Needs Phase 1's enumeration finished.* Catalogue gating ahead
      of route matching, buckets A and B, the product table, B1 and B2, and `stripe_analytics` as a
      B1 refusal. The generated refusal-conformance test (§8.2) lands here.
- [x] **Phase 8: Discovery.** The pruner's second output and `discovery_index.json`; `intent` +
      `resource` search with the scoring function; the twelve-key details document; `{id}`
      placeholder normalisation in discovery output only.
- [x] **Phase 9: Serialization completeness.** The 20 already-in-schema fields; the pruner fix that
      retains schemas reachable from tool outputs so the `account` object is covered; the three
      excluded product fields with a test asserting their absence; the generated check driven by the
      probe's captured objects.

### Completion

- [ ] **Phase 10: Documentation and closeout.** README and world `AGENTS.md` — including the
      composition requirement of functional spec §4.1.2, so nobody stands this up standalone and
      assumes the surface is complete. Audit every register row for a disposition, finalise §13, and
      apply the SH206 correction of architecture §1.1 to the functional spec.

## Notes

**Phase 1 gates more than it looks.** Nothing in Phases 7 or 8 can be written against a guess at the
catalogue, and a sampled run changes what those phases can assert. If the enumeration returns a
large `error` count rather than clean verdicts, stop and fix the script rather than proceeding on
partial data — a verdict of `error` filed as `absent` invents a refusal.

**Phases 2 and 4 are the risky ones.** Phase 2 touches ~15 modules and its failure mode is a missed
literal; Phase 4 changes commit semantics at the middleware boundary and its failure mode is a
rolled-back write that should have survived. Both deserve a careful review, and Phase 4 should keep
a test that a `402` decline leaves its charge row behind — the property the whole design exists to
preserve.

**No component designs.** Architecture is the whole design; the three documents proposed in
architecture §10 are not being written. Phase plans carry the per-phase detail instead, which means
Phases 5, 7 and 8 should expect a longer planning step than the rest.

**Nothing here needs a Stripe key except Phase 1.** Every later phase tests against committed
artifacts, and CI never opens a socket.
