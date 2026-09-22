---
status: complete
---

# Component: Conformance harness

Cassette format, the recorder, the replayer, the allow-list, and schema conformance —
`tests/conformance/`, `tests/schema_conformance/`, `tools_dev/record.py`. Built in Phases 4–5 of
`implementation_plan.md`, used continuously through every resource phase's step 3, and gating in CI
forever after.

## Purpose and Scope

This component answers one question, twice, at two different prices:

1. **Cheap, constant question: does every object this world ever returns match the shape Stripe's
   own spec says it should?** — schema conformance, run against every object any test produces.
2. **Expensive, curated question: for a specific, recorded scenario, does this world's *behavior*
   match what the real API actually did?** — cassette conformance, run against a committed, reviewed
   set of scenarios.

It owns:

- the cassette file format and the code that reads and writes it (`tests/conformance/cassette.py`);
- the recorder that drives `stripe-python` against real Stripe test mode and produces cassettes
  (`tools_dev/record.py`, `tools_dev/scenarios/`);
- the redaction pass every recording goes through before it can be written to disk
  (`tests/conformance/redact.py`, shared by the recorder and by a standalone hygiene test);
- the replayer that turns a cassette into calls against a live instance and diffs the result
  (`tests/conformance/replay.py`);
- the allow-list of declared, permitted differences (`tests/conformance/allowed_differences.py`);
- the generated schema validator (`tests/schema_conformance/`).

**What is not this component's responsibility:**

- **Correctness of the behavior itself.** This harness proves *divergence*, not designs the fix. A
  failing cassette replay says the world disagrees with a recorded fact; fixing the disagreement is
  the resource slice's code, in `resources/`, `billing/`, `dispatch/`.
- **The routing table's completeness.** The 148-operation count assertion lives with the router
  (`components/dispatcher.md`); this component only conforms the operations it is given scenarios
  for, and does not attempt exhaustive per-operation coverage — that is what the ordinary resource
  test suite is for.
- **Webhook delivery, or anything about `test_helpers/*`.** Neither is routed (functional spec §3.2,
  §5), so neither can appear on either side of a diff. Where a *recording* scenario needs
  `test_helpers/test_clocks` to construct a precondition on the real API, that call is explicitly
  out-of-band and never becomes a cassette interaction (see §"Time-dependent preconditions" below).
- **Determinism of ids and timestamps.** That is a property of `_ids.py` / `ctx.clock`, tested
  directly by the determinism suite (architecture §11). This component's allow-list simply declares
  that ids and timestamps are *never* compared, because comparing them would be comparing two
  different clocks and two different id streams by construction, not testing anything.
- **Discovery.** `stripe_api_search` / `stripe_api_details` are exercised by `components/discovery.md`.

## Correction to architecture.md §10

Architecture §10 says recording "[r]equires `api.stripe.com` egress, which the current environment
blocks — recording happens elsewhere and the cassettes are committed artifacts." That sentence is
stale. `implementation_plan.md`'s "Probing" section is explicit and more recent: **the coding
environment allows `api.stripe.com` egress**, and step 3 of every resource phase's recipe records
directly against real test mode as part of the normal work of that phase — there is no separate
"elsewhere." Only the environment the *specs* were written in blocks it, and that fact has no bearing
on implementation.

This component is designed for `implementation_plan.md`'s model: recording is a routine, in-loop
action available in the same environment as everything else, and the one thing that must hold
regardless of environment is narrower and still true — **CI never records.** It replays committed
cassettes and never imports the `stripe` PyPI package, let alone opens a socket (see Dependencies and
Test Plan). The distinction that matters is CI vs. interactive/agent use, not "here" vs. "elsewhere."

## Public Interface

### The cassette (`tests/conformance/cassette.py`)

```python
@dataclass(frozen=True)
class Ref:
    """A placeholder: "use the value this replay run actually produced," not the literal recorded
    value. Only meaningful inside a Step's `path` or `params`."""
    step: str          # the `binds_as` name of an earlier step in the same scenario
    field: str = "id"  # dotted path into that step's response body

@dataclass(frozen=True)
class Step:
    seq: int
    method: str                     # "GET" | "POST" | "DELETE"
    path: str                       # may contain Ref-substituted params in `params`, never in the
                                     # literal path string — path parameters travel as `path_refs`
    path_refs: dict[str, Ref]       # e.g. {"payment_method": Ref("payment_method")}, applied to
                                     # `path`'s `{placeholder}` segments before dispatch
    params: dict[str, Any]          # literal params as recorded; Ref-valued entries substituted at
                                     # replay time before dispatch
    idempotency_key: str | None
    binds_as: str | None            # name later steps may `Ref(...)` against; None if unused
    recorded_status: int
    recorded_body: dict[str, Any]
    recorded_stripe_version: str    # normally the pin; scenario 3 deliberately varies it

@dataclass(frozen=True)
class Cassette:
    scenario: str
    description: str
    recorded_at: str                # ISO 8601, informational only, never diffed
    stripe_version_pin: str
    steps: tuple[Step, ...]

def load(path: Path) -> Cassette: ...
def dump(cassette: Cassette, path: Path) -> None:
    """Canonical serialization: fixed key order per object type, 2-space indent, trailing newline,
    `ensure_ascii=False`. The only writer of cassette files. Re-running `dump` on a `load`ed cassette
    reproduces the file byte-for-byte — this is what `test_cassette_json_is_canonically_formatted`
    checks, and it is what keeps re-recordings a small, legible diff instead of key-order churn."""
```

### The recorder (`tools_dev/record.py`)

```
python -m tools_dev.record --scenario 01_proration_half_cent_tie_break
python -m tools_dev.record --scenario 01_proration_half_cent_tie_break --scenario 03_malformed_stripe_version
python -m tools_dev.record --all
python -m tools_dev.record --list          # prints every registered scenario name, no network
```

Reads `STRIPE_CONFORMANCE_TEST_KEY` from the environment; refuses to run (before making any request)
if unset, or if the key does not start with `sk_test_` or `rk_test_`. There is no `--live` escape
hatch. Writes one cassette file per `--scenario`, after the redaction pass reports zero findings; a
non-zero finding aborts the write and prints what it found and where (never writes a partially-
redacted file). `--all` re-records every registered scenario, each independently, so a mid-run
failure on scenario 6 still leaves scenarios 1–5's freshly re-recorded files on disk and unaffected
by the failure — re-running `--all` (or just `--scenario 06_...`) picks up where it left off.

### Scenario declaration (`tools_dev/scenarios/*.py`)

```python
# tools_dev/scenarios/s01_proration_half_cent_tie_break.py
from tools_dev.scenarios._dsl import Recorder, ref

SCENARIO = "01_proration_half_cent_tie_break"
DESCRIPTION = "A mid-cycle plan change whose proration line lands on an exact x.xx5."

def record(r: Recorder) -> None:
    customer = r.step("POST", "/v1/customers", {"email": "s01@conformance.stripeapi.invalid"},
                       binds_as="customer")
    ... # further r.step(...) calls, using ref(customer) wherever a later param or path segment
        # needs *this run's* id rather than the literal one just recorded
```

`Recorder.step(...)` is the one primitive every scenario uses: it calls
`client.raw_request(method, path, **resolved_params, stripe_version=stripe_version_override or PIN)`
through `stripe-python`, captures `(status, body, headers)` for both the success path (the returned
`StripeResponse`) and the error path (`stripe.StripeError`'s `.http_status` / `.json_body` /
`.headers`, since `raw_request` raises on non-2xx — confirmed by reading
`stripe/_api_requestor.py::_interpret_response`), and appends a `Step`. A scenario module is
registered by filename convention (`sNN_slug.py` → cassette `NN_slug.json`); `record.py`'s registry
is built by globbing `tools_dev/scenarios/s*.py`, so declaring a scenario is "add a file," nothing
else to wire up.

### The replayer (`tests/conformance/replay.py`)

```python
def replay(cassette: Cassette, instance: seahaven.Instance) -> list[Violation]:
    """Walks `cassette.steps` in order. For each step: resolves `Ref`s against ids captured from
    *this run's* earlier responses, dispatches through the matching tool
    (`stripe_api_read` for GET, `stripe_api_write` for POST/DELETE — the same tools an agent uses,
    per architecture §11's rule that every call goes through `instance.call(...)`), and diffs the
    tool's `{"status", "body"}` against the step's `recorded_status` / `recorded_body` through the
    allow-list. Returns every violation found across every step — never stops at the first."""
```

### The allow-list (`tests/conformance/allowed_differences.py`)

```python
@dataclass(frozen=True)
class AllowedDifference:
    path: str                                  # see "Path matching" below
    reason: str                                # required, non-empty; this is the reviewable content
    scenario: str | None = None                # None = every scenario; else exact scenario name
    predicate: Callable[[Any, Any], bool] | None = None
    # predicate is None  -> path may differ arbitrarily (the common case: ids, timestamps)
    # predicate is given -> path may differ only if predicate(recorded, replayed) is True

ALLOWED_DIFFERENCES: list[AllowedDifference] = [ ... ]   # the reviewed content of this file
```

### Schema conformance (`tests/schema_conformance/`)

```python
def validate_object(obj: dict[str, Any], *, source: str) -> list[SchemaViolation]:
    """`obj["object"]` selects the schema. Validates required fields, types, nullability and
    additionalProperties against the structural rules generated from spec3.min.json, and separately
    validates every field in ENUM_OVERRIDES (generated into spec/enums.py, §5.2) against its
    extracted closed set instead of "any string". `source` is a human string identifying which test
    and which call produced `obj`, folded into the failure message."""
```

A pytest hook (`tests/schema_conformance/conftest.py`) wraps every `@pytest.mark.seahaven` test:
after the test body runs, it walks `instance.call_log` (Seahaven's own record of every call made
through that instance — `seahaven.instances.Instance.call_log`), pulls the `body` of every
`stripe_api_read` / `stripe_api_write` response in it, walks that body recursively (top-level object,
every nested object with its own `object` discriminator, every list item), and calls
`validate_object` on each one. A test does not opt in; it cannot opt out. This is what "runs against
every object a test produces rather than against a curated sample" means concretely: there is no
separate suite of "schema conformance tests" to keep in sync with the resource suite — the resource
suite *is* the corpus, automatically, because every resource phase's own tests (from step 3 of its
recipe) already call the tools and therefore already feed this hook.

## Internal Design Approach

### The cassette format

**One JSON file per scenario**, not per-request and not one giant file. A scenario is a short,
ordered script — "create a customer, attach a card, charge it, retry the charge with the same
idempotency key" — and the unit a human reviews, and the unit a re-recording touches, is that script,
not an individual HTTP exchange. `tests/conformance/cassettes/NN_slug.json`, numbered by the priority
order in functional spec §12 (`01`–`09`, with lettered siblings for a scenario's declared variants,
e.g. `01b_proration_flexible_billing_mode.json` for the `billing_mode=flexible` half of scenario 1).

**Ordering and repeated identical requests are handled by construction, not by matching heuristics.**
A cassette's `steps` are a fixed, ordered sequence; the replayer executes them in that order and
nothing else. There is no lookup table matching an incoming request to "the recording that looks like
it" (the VCR/`vcrpy` model), because there is no independent caller making unpredictable requests to
match — the scenario script *is* the sequence, both at record time and at replay time. This sidesteps
the entire class of problem "the same GET was recorded twice, which recording does a replayed GET
match": two identical requests at positions 3 and 7 are simply steps 3 and 7, played in that order,
compared to responses 3 and 7. An idempotent-retry scenario (§12 point 4) is the clearest case this
buys: two POSTs, same idempotency key, same params, **different** expected bodies is not even the
right framing — they must have the **same** body (that is what the scenario is proving), and
positional replay makes that a direct two-step comparison rather than something a matcher has to be
taught not to collapse into one.

**Requests are keyed for replay by position (`seq`), with method+path recorded alongside as an
integrity check**, not as a lookup key. Before dispatching step N, the replayer's own bookkeeping
(built from the steps it has already executed against the live instance, not from the cassette) has
already produced concrete ids for every earlier `binds_as`. It resolves step N's `Ref`s against those,
dispatches, and only *then* is method+path from the cassette meaningful — as an assertion that the
scenario script hasn't silently drifted from the cassette it claims to replay (e.g., someone edited
`tools_dev/scenarios/sNN_*.py` without re-recording). A mismatch here is a distinct, loud failure
("step 3 recorded POST /v1/refunds but the scenario would now dispatch POST /v1/charges/.../refund —
re-record this scenario"), never a silent fall-through to "closest match."

**Data flow across steps (ids) is explicit, not inferred by scanning for Stripe-id-shaped strings.**
The literal request Stripe actually received is what is stored — real (redacted) values, exactly as
sent, so a reviewer reading the file sees the actual wire content. Where a value in that request
*came from* an earlier step's response, `path_refs` / `Ref`-valued `params` entries say so
structurally, recorded at the same time as the literal:

```json
{
  "seq": 2,
  "method": "POST",
  "path": "/v1/payment_methods/{payment_method}/attach",
  "path_refs": {"payment_method": {"step": "payment_method", "field": "id"}},
  "params": {"customer": {"$ref": {"step": "customer", "field": "id"}}},
  "idempotency_key": null,
  "binds_as": null,
  "recorded_status": 200,
  "recorded_stripe_version": "2026-08-26.dahlia",
  "recorded_body": { "id": "pm_1SxA0f...", "object": "payment_method", "customer": "cus_1SxA0e...", "..." }
}
```

`recorded_body` keeps the literal ids Stripe actually returned — those are the oracle the diff
compares against, and the allow-list is what says "don't compare `id` and `customer` here." The
`$ref`/`path_refs` machinery is only about what the *replayer sends*, never about what is stored as
the recorded truth to diff against. This also means the file reads naturally in a diff: a reviewer
sees `"customer": {"$ref": ...}` and immediately knows "this is wired to step 0's output," without
needing the diff tool to explain it.

**Readability in a diff** is a deliberate, checked property, not a hope: canonical `dump()` (fixed key
order, 2-space indent, one cassette = one file) means a re-recording that changes nothing material
produces a near-empty diff, and a re-recording that changes one field produces a one-line diff. Two
things actively work against this and are both designed out: (1) volatile-but-worthless header data
(`Request-Id`, and anything server-timing-shaped) is dropped at capture, never stored-then-ignored —
storing it would make every re-recording touch every line for no reason; (2) `recorded_at` is the
only genuinely record-time-varying field in the file and is explicitly excluded from the diff engine
(and from `test_cassette_json_is_canonically_formatted`'s byte-equality check target — it round-trips,
it just isn't compared for conformance purposes).

### Redaction

Designed as a **mandatory pipeline stage between capture and disk**, not a filter run against
already-committed files. `Recorder.step()` never returns a `Step` to the caller without it having
passed through `redact.scrub(step)` first; `record.py` cannot construct a `Cassette` from unscrubbed
steps because `Recorder.step()` is the only constructor of a `Step` outside `cassette.load()`.

Four categories, four different postures, because they are not the same kind of risk:

1. **API keys.** Never enter a cassette in the first place: `STRIPE_CONFORMANCE_TEST_KEY` is read once
   into `stripe.StripeClient(api_key=...)` and Stripe's own request/response cycle does not echo the
   key back in a body or a header under normal use. The defense-in-depth layer is a regex scrub over
   every string leaf of the assembled step (path, params, recorded body, serialized to text) for
   `sk_(test|live)_`, `rk_(test|live)_`, `pk_(test|live)_` followed by 10+ alphanumerics, replacing a
   match with `"<redacted:api_key>"`. This catches the case a human never intends — pasting a key into
   a metadata value while constructing a scenario by hand.
2. **Real emails.** Not scrubbed silently — **guarded**. Every scenario is required to use the
   reserved synthetic domain `@conformance.stripeapi.invalid` for every email-shaped field; `redact`
   scans for any RFC-5322-ish local@domain substring in the assembled step and, if the domain is not
   the reserved one, **raises**, aborting the recording with the offending value's location (which
   scenario, which field). A real email silently rewritten to `<redacted>` would hide that a real
   address had been handed to Stripe test mode in the first place, which is the actual problem;
   failing loudly is the correct response, not laundering it after the fact.
3. **Account identifiers.** No connected-account id (`acct_...`) can legitimately appear anywhere in
   this project's scope — there is no Connect surface routed. A regex scrub for `acct_[A-Za-z0-9]{10,}`
   replaces any hit with `"<redacted:account_id>"`, and separately raises a *test* failure (not just a
   scrub) if this scrubber's canary fixture (see Test Plan) fails to catch a planted one, so the
   scrubber itself is provably not a no-op.
4. **Request ids.** Dropped, not redacted — `Request-Id` and `Idempotency-Key` response headers are
   never copied into the `Step` at all. They compare to nothing, are needed by nothing (the
   `request_log_url` *body* field they're related to is a declared allow-list entry, see below, but
   the header itself has no field to occupy), and keeping them would only add re-recording diff noise
   for zero value.

**How a test proves no secret ever reaches git:** two independent tests, deliberately not the same
mechanism.

- `test_redaction_catches_planted_secrets` — a fixture `Step` (not a real recording) containing a
  fake `sk_test_...` value, a non-synthetic email, and a fake `acct_...`, run through `redact.scrub`.
  Asserts the key and account id are replaced and that the non-synthetic email raises. This proves
  the scrubber *works*, independent of what happens to be committed right now.
- `test_no_secret_reaches_committed_cassettes` (`tests/conformance/test_cassette_hygiene.py`) — walks
  every file actually committed under `tests/conformance/cassettes/`, applying the same three regexes
  plus the email-domain check, and fails if any hit. This is a hygiene test with no network access, so
  it runs in ordinary CI on every PR — including a PR that only touches source, in case a rebase or a
  manual edit reintroduced something the recorder's own gate would have blocked.

Both must pass; the second existing does not make the first redundant, because the first is what
proves the mechanism the second is trusting.

### The recorder

Talks to real Stripe **exclusively through `stripe-python`**'s `raw_request(method, path, **params,
stripe_version=...)`, never through the SDK's generated per-resource classes (`stripe.Customer.create`
and friends). This is a direct consequence of this world's own agent surface already being
method+path+JSON-params (functional spec §2.2, §2.5): the recorder's one primitive maps 1:1 onto the
dispatcher's one primitive, so a single `Recorder.step()` covers all 148 operations without 148
per-resource recording functions, mirroring exactly why the dispatcher itself needed no per-operation
handler for the ~95 generated-CRUD operations. `idempotency_key`, when a step declares one, is passed
the same way `stripe_api_write` exposes it — a request option `stripe-python` folds into the
`Idempotency-Key` header, not a body param.

`Stripe-Version: 2026-08-26.dahlia` is passed as `stripe_version=` on **every** `raw_request` call by
default, sourced from one constant (`tools_dev/scenarios/_dsl.py::PINNED_VERSION`, re-exported from
`src/seahaven_stripe_world/spec/` so the pin cannot drift between the recorder and the served version — see
Dependencies). Scenario 3 is the sole declared exception: it exists specifically to observe a
malformed version, so `Recorder.step(..., stripe_version_override="not-a-real-version")` is a named,
visible parameter used in exactly one scenario file, with a comment at that call site naming the
scenario so nobody mistakes it for an accidental un-pinned call.

**Naming and re-recording a single slice without disturbing others:** cassette filename = scenario
name = registered scenario module's `SCENARIO` constant. `record.py --scenario 06_invoice_paid_refund`
touches exactly `cassettes/06_invoice_paid_refund.json` and nothing else — no shared index file, no
cross-scenario state, because `Recorder` constructs one fresh `stripe.StripeClient` and one fresh set
of `binds_as` bindings per scenario run. Two scenarios never share a real Stripe object: scenario 6
creates its own customer rather than reusing scenario 4's, even though that means near-duplicate setup
steps across files. That duplication is the price of independence, and it is the right trade for a
committed artifact that must survive one scenario being edited or dropped without a ripple through
every other cassette.

**Per-slice recording (implementation_plan.md's daily loop) uses the same primitive, at lower
ceremony.** Step 3 of every resource phase's recipe ("probe the real API... turn what actually came
back into tests") is not required to produce a permanently committed, allow-listed cassette — it needs
the implementer to *see* real Stripe's actual response once and write an ordinary example-based test
from it. The recorder supports this directly: a throwaway scenario file under
`tools_dev/scenarios/probe_<slice>.py` (e.g. `probe_customers.py` while building Phase 6), recorded
with the identical `record.py --scenario probe_customers` command, producing a cassette under the same
`tests/conformance/cassettes/` directory by the same mechanism. The difference between a "probe"
cassette and a "conformance" cassette is purely a naming and curation convention, not a code path:

- A **conformance** cassette (files matching `^\d\d[a-z]?_`) is enumerated by
  `test_replay_conformance.py`, is subject to the allow-list, and is reviewed like a design document
  (architecture §10) — it is the permanent, curated fidelity suite.
- A **probe** cassette (files prefixed `probe_`) is *not* enumerated by that test at all (the
  collection glob explicitly excludes the `probe_` prefix) and carries no conformance obligation. It
  exists to be read while writing `tests/resources/test_customers.py`'s ordinary, hand-written
  assertions, and is free to be deleted once that slice's tests are green, or left committed as a
  breadcrumb — the implementer's call, same as any other scratch fixture.
- If a step-3 probe turns up exactly one of the §12 fidelity questions (most notably, the proration
  half-cent tie-break is expected to surface during Phase 14's own step 3, not only from a dedicated
  conformance run), it graduates: rename the file to its `NN_slug` conformance name, add it to the
  allow-list review, and it is now scenario 1. Nothing is recorded twice; the loop and the permanent
  suite draw from the same well.

This is the resolution to a gap the architecture leaves implicit: §10 and §12 describe the *curated*
nine-scenario suite in detail but say nothing about how the per-slice loop in
`implementation_plan.md`'s recipe actually uses recording day to day. Designing two consumption modes
over one mechanism — rather than either forcing every probe through allow-list review (too heavy for
step 3) or inventing a second, undocumented recording tool (drift risk) — keeps the loop cheap without
making the permanent suite's provenance murky.

### Time-dependent preconditions (a gap the spec does not address)

Scenario 5 ("a subscription recovering from `unpaid`") and dunning fidelity generally need Stripe's
real Smart Retries schedule to actually exhaust — which, on the real API, takes real elapsed time
unless the recording uses `test_helpers/test_clocks` to fast-forward it. `test_helpers/*` is
explicitly not routed (functional spec §3.2, §5), and neither architecture.md nor the functional spec
says how a scenario that needs elapsed time gets recorded at all. This is a genuine hole, not a detail
I'm filling in quietly:

**Resolution:** a scenario's `record()` function may call `test_helpers/test_clocks` (or any other
unrouted, real-Stripe-only endpoint) directly through `stripe-python` purely to advance *real* Stripe's
clock between steps — those calls are made with the plain SDK, outside `Recorder.step()`, and
**never become a `Step`**, because a `Step` only exists for operations this world could ever be asked
to reproduce. Every step that *is* recorded (each retry checkpoint's invoice `GET`, each `pay` attempt)
is a normal, routed, capturable interaction. On replay, the world is never asked to fast-forward
anything — it is asked to execute the same number of explicit, routed retry calls the cassette
captured, once per checkpoint, exactly as an agent driving retries manually would. This only works if
the billing engine's dunning model treats an attempt as something an explicit call can force,
independent of wall-clock time — which it must anyway, since no eval could otherwise reach an `unpaid`
state on a frozen clock either. That requirement is `billing_engine.md`'s to satisfy, not this
component's; this component's dependency on it is stated explicitly in Dependencies below, and
scenario 5 is the concrete conformance proof that the requirement was actually met, not just assumed.

### The replayer

Runs every conformance scenario against a **fresh `empty`-fixture instance**, never `small` or
`large`: every scenario is required to be fully self-contained (create whatever objects it needs as
its own first steps), which the recorder's `binds_as`/`Ref` machinery both enables and enforces — a
scenario cannot reference an object it did not itself create in an earlier step, because there is
nothing else to `Ref` against. This keeps a scenario's result independent of fixture content and
fixture generation order, and means a fixture regeneration (Phase 19, and every later schema change
per architecture §12's hazard about fixture invalidation) never touches conformance results.

Dispatch happens through the same tool functions an agent calls — `stripe_api_read(path, params)` for
a `GET` step, `stripe_api_write(method, path, params, idempotency_key)` for `POST`/`DELETE` — via
`instance.call(...)`, never the bare handler function, per the project-wide rule in architecture §11.
This means idempotency middleware, parameter validation and the error envelope are all genuinely in
play during a conformance replay, not bypassed for convenience.

**Comparison** is a structural tree diff over `{"status": ..., "body": ...}` versus
`{"status": recorded_status, "body": recorded_body}`: same JSON type at every path, same value unless
an `AllowedDifference` says otherwise (below), same set of keys unless a key's presence/absence is
itself allow-listed. Every violation at every path is collected before returning — a replay never
stops at the first mismatch, because a single broken handler frequently produces several related
diffs (a wrong `status` field cascades into a wrong `amount_captured`, a wrong `livemode` on a nested
object, etc.) and an author fixing it wants the whole picture in one run, not one-at-a-time discovery.

### The allow-list

**Path matching** operates on the dotted/bracketed path the tree-diff walker produces at each leaf
(`status`, `body.id`, `body.customer.id`, `body.data[3].id`, ...), against patterns using three
matching modes, all resolved by one small matcher (no third-party pattern library — the vocabulary is
deliberately tiny):

- **Exact** — `"body.livemode"` matches only that one path.
- **Single-segment wildcard `*`** — `"body.*.id"` matches `body.customer.id` and `body.default_source.id`
  but not `body.customer.address.id` (two segments deep).
- **Any-depth wildcard `**`** — `"**.id"` matches `body.id`, `body.customer.id`,
  `body.data[0].lines.data[2].id`, at any depth, including inside list items (`[N]` is treated as an
  ordinary segment for matching purposes, so `**.id` also covers `body.data[].id`-shaped paths without
  a separate array syntax).

Global entries (`scenario=None`) apply everywhere; scenario-scoped entries apply only when
`replay()`'s current scenario name equals `AllowedDifference.scenario`, so a genuinely scenario-local
oddity (the deliberately malformed `Stripe-Version` in scenario 3, say) cannot silently mask an
unrelated regression in every other scenario. The representative global entries:

| Path | Reason |
|---|---|
| `**.id` | Ids are freshly minted per world instance (architecture §4.4); never equal the recorded real-Stripe id by construction. |
| `**.created`, `**.*_at` (timestamp fields) | The fixture clock and the record-time wall clock are different instants; equality was never meaningful. |
| `body.request_log_url` | Stripe-dashboard-specific, tied to the recording account; this world does not model a dashboard. |
| `body.customer`, `body.payment_method`, `body.*` (any field holding a cross-reference by id) | Covered by `**.id` already where the value *is* an id string; listed here only where the field name itself differs in shape from a plain nested `id` (documented per-field as each is added, not a blanket rule). |

Two behavioral differences do **not** fit the per-field model and are declared in a second, smaller
section of the same file (`STRUCTURAL_DIFFERENCES: list[str]`, prose entries, reviewed the same way):
idempotency-key retention (real Stripe evicts a key after a ≥24h floor; this world's keys never expire
because the clock never advances — functional spec §6.1) and search result freshness (real Stripe
search lags writes; this world's is exact — functional spec §3.3, not yet exercised pre-Phase 22).
Neither can be expressed as "path X differs" because neither ever produces an observable diff *within
a single instance's frozen lifetime* — they are differences in a dimension (elapsed real time) that
this harness's replay never has, and the file says so explicitly rather than pretending the field-level
table covers everything.

**Failure output — the primary interface.** A failing replay raises `ConformanceFailure`, whose
message is built to be read directly, without opening a debugger:

```
Conformance FAILED: scenario '06_invoice_paid_refund', step 3 (POST /v1/refunds)

  2 undeclared differences:

    body.status
      recorded (real Stripe):  "succeeded"
      replayed (this world):   "pending"

    body.balance_transaction
      recorded (real Stripe):  "txn_1SxB2f..."
      replayed (this world):   null

  Neither path is in tests/conformance/allowed_differences.py.
  Fix the handler, or add an AllowedDifference with a reason — never leave this failing.

  Full recorded response:  tests/conformance/cassettes/06_invoice_paid_refund.json (step 3)
  Full replayed response:  tests/conformance/.replay-out/06_invoice_paid_refund-step3.json
```

Every undeclared violation across the whole scenario is listed in one message (not one exception per
violation, not fail-fast), each with the exact path, both values side by side, and an explicit
instruction that the fix is either the handler or a reviewed allow-list entry — never a silent pass.
The full bodies are always written to a scratch file (`tests/conformance/.replay-out/`, gitignored)
because an inline diff of a large nested invoice is not legible; the message names exactly where to
look. A step whose method+path has drifted from the cassette (see "requests are keyed... by position"
above) fails with its own distinct message naming the scenario file to re-record, so that failure mode
is never confused with a genuine behavioral divergence.

### Schema conformance

Deliberately cheaper and separate from cassette replay, because it answers a narrower question
("does the shape match spec3.min.json") against a much larger corpus (every object any test produces,
not nine curated scenarios) and needs no allow-list at all — a schema violation has no legitimate
excuse the way a `created` timestamp mismatch does.

**Generated, not hand-written**, from the same pipeline that already produces `enums.py`,
`expandable.py` and `event_types.py` (architecture §5.2, Phase 2). `tools_dev/prune_spec.py` walks
`spec3.min.json`'s component schemas for every `object` discriminator this world routes and emits, per
object type, the structural rule set `validate_object` reads: required fields, JSON type per field,
nullability, and `additionalProperties: false` enforcement. The **six (per functional spec §4 and
architecture §5.2's prose) bare-`string`-but-closed-set fields** are layered on top of that structural
pass from the *same generation step* that already extracts them into `enums.py` — `validate_object`
does not special-case them by name in hand-written code; it consults `ENUM_OVERRIDES`, a generated
`{object_type: {field_path: frozenset[str]}}` table, and validates against that set wherever it has an
entry instead of accepting "any string." Since Phase 4's review round the table also carries the
pass-2 enrollment `data_model.md` §12 prescribes — every closure field whose description genuinely
closes its value set, 36 `(schema, field)` pairs at the pinned version — because the same enum
appears under several schema names on the wire (`brand`/`funding` exist on the payment-method
storage shape, the charge's `payment_method_details.card`, and the dispute's copy, and each needs
its own entry); every value token is asserted at generation time to still appear in the live
description, and the deliberately excluded fields are recorded with reasons in
`phase_plans/phase_4.md`.

**A nullability-annotation gap, declared rather than papered over.** The spec's `nullable`
annotations are incomplete in at least one place the live API contradicts: at the pinned version a
freshly created customer carries `default_source: null` and
`invoice_settings.default_payment_method: null`, while `spec3.json` gives both an expandable union
with no `nullable` key (probed on the sandbox account, Phase 4, 2026-09-19). Strict
no-declared-differences conformance is therefore unimplementable against this spec, so the validator
carries exactly one declared exception table, `NULLABLE_DESPITE_SPEC` in
`tests/schema_conformance/validate.py`: `(schema, field)` pairs the pinned spec types as
non-nullable that the live API emits `null` for anyway. An entry is added only with probe evidence,
cited beside it, and the general declaration — Stripe's `nullable` annotations are not authoritative
where a recording contradicts them — belongs to Phase 5's `allowed_differences.py` review, which
this handoff names explicitly. This table is the one place schema conformance permits a declared
difference; every other violation remains excuseless.

**A discrepancy worth flagging while it's in front of me, not silently resolved:** the functional
spec's own list in §4 —
"`dispute.reason`, `payout.status`/`method`/`source_type`, `refund.status`, `balance_transaction.status`
and `setup_intent.usage`" — names **seven** field paths (`payout` alone contributes three), while both
§11's summary and architecture §5.2 call this "the six ... fields" twice each. Rather than guessing
which number is the typo and hard-coding six or seven anywhere in this harness, `ENUM_OVERRIDES` is
sized by whatever `prune_spec.py` actually extracts from the spec's description prose — the count is
never asserted as a literal in the validator, only enumerated. I'd flag the "six" wording in §4/§11/
architecture §5.2 for a one-line correction in a later spec pass; it does not block this design, since
the harness was built not to depend on the number being right in prose.

**Runs against every object a test produces, not a curated sample**, via the pytest hook described in
Public Interface: it reads `instance.call_log` after each `@pytest.mark.seahaven` test, so every
resource phase's own ordinary tests (built from step 3's recordings, per the daily loop above) are
schema-validated for free, the moment that phase's tests exist — there is no separate "now write the
schema conformance tests for this resource" step in the recipe, and none is needed.

## Dependencies

**Depends on:**

- `dispatch/` (the four tools, `stripe_api_read`/`stripe_api_write`) — the replayer's and the schema
  hook's only way into the world, per architecture §11's "every tool exercised through
  `instance.call(...)`" rule.
- `src/seahaven_stripe_world/spec/spec3.min.json`, `enums.py`, `expandable.py` — generated in Phase 2; schema
  conformance's structural rules and enum overrides are read from these, never re-derived.
- `fixtures/empty` — every conformance scenario's starting state (Phase 1).
- `stripe-python` (PyPI `stripe`, MIT) — **recording-only.** It is a dev/optional dependency
  (`pyproject.toml`'s `[project.optional-dependencies].dev` or an equivalent extra), never a runtime
  dependency of `seahaven-stripe-world`, and `import stripe` appears **only** inside `tools_dev/` —
  enforced by a static test (`test_conformance_code_never_imports_stripe_python`, an AST/grep check
  over `tests/conformance/`, `tests/schema_conformance/` and `src/seahaven_stripe_world/`) so that CI's dependency
  set for running the test suite doesn't need `stripe` installed at all, and there is no code path in
  replay or schema validation that could accidentally attempt a network call.
- `seahaven.instances.Instance.call_log` — schema conformance's hook into what a test actually did.

**Depended on by:**

- Every resource phase's step 3 (`implementation_plan.md`) — the recorder is how each slice's tests
  get written.
- CI's merge gate — `test_replay_conformance.py` (parametrized over every `NN_slug.json` cassette) and
  the whole schema-conformance hook run on every PR with no network access.
- `components/billing_engine.md` — indirectly: scenario 1 (the proration tie-break) and scenario 5
  (dunning exhaustion without wall-clock time) are this component's way of proving billing engine
  decisions that phase's own unit tests assume; billing engine in turn must expose dunning retries as
  explicitly callable, independent of elapsed time, for scenario 5 to be replayable at all (see "Time-
  dependent preconditions" above) — a requirement this document states but does not itself satisfy.
- Nothing in `src/seahaven_stripe_world/` imports this component; it is test-only and dev-tool-only, by design —
  the shipped package never needs `stripe-python` or a Stripe key to run.

## Test Plan

**Redaction / hygiene**

- `test_redaction_catches_planted_secrets` — a fake `sk_test_...`, a fake `acct_...`, and a
  non-synthetic email through `redact.scrub`; keys/account ids are replaced, the email raises.
- `test_no_secret_reaches_committed_cassettes` — regex/domain scan over every committed cassette file;
  no network, runs on every PR.
- `test_request_id_and_idempotency_key_headers_are_never_stored` — asserts no `Step` in any committed
  cassette carries those header names anywhere.

**Cassette format**

- `test_cassette_json_is_canonically_formatted` — `dump(load(path))` byte-equals `path`'s contents
  (excluding `recorded_at`), for every committed cassette.
- `test_step_ref_resolves_against_this_runs_ids_not_recorded_ids` — a synthetic two-step cassette
  where step 1's `Ref` is deliberately given a *different* recorded id than what a stub "replay run"
  produces; asserts the dispatched request used the stub's id, not the recorded one.
- `test_drifted_scenario_fails_loudly_not_silently` — a scenario module edited to call a different
  path than its cassette's step 0 records; replay raises the position-mismatch failure, not a
  body-diff failure.

**Replayer / diff / allow-list**

- `test_replayer_reports_every_undeclared_difference_at_once` — a stubbed handler returns two wrong
  fields; the resulting `ConformanceFailure` message names both.
- `test_replayer_passes_when_only_allow_listed_fields_differ` — recorded and replayed bodies differ
  only in `id` and `created`; replay is green.
- `test_allowlist_wildcard_matching` — unit tests on the matcher directly: exact, `*`, `**`, including
  a `**` match inside a list index.
- `test_allowlist_scenario_scoping_does_not_leak` — a scenario-scoped entry for scenario 3 does not
  suppress the same path's difference in scenario 4.
- `test_allowlist_predicate_mode` — a predicate-mode entry (e.g. "must both be `false`") rejects a
  case where the predicate returns `False`, even though the path is listed.
- `test_undeclared_missing_key_is_a_violation` — recorded body has a key the replayed body omits
  entirely; treated as a violation like any value mismatch, not silently skipped.

**Recorder**

- `test_recorder_refuses_without_test_mode_key` — no `STRIPE_CONFORMANCE_TEST_KEY`, or a `sk_live_`-
  prefixed one, aborts before any request is attempted (mockable — does not require real egress).
- `test_recorder_pins_stripe_version_by_default` — every `Step` in a recorded fixture (using a stubbed
  `stripe-python` transport) carries the pinned version, except when `stripe_version_override` is
  explicitly passed.
- `test_scenario_registry_has_no_orphans` — every `sNN_*.py` under `tools_dev/scenarios/` has a
  matching `NN_*.json` under `cassettes/`, and vice versa (excluding `probe_*` files, which are
  exempt by design).
- `test_probe_cassettes_are_excluded_from_conformance_collection` — a `probe_*.json` file dropped into
  `cassettes/` is not picked up by `test_replay_conformance.py`'s parametrization.

**Schema conformance**

- `test_schema_conformance_hook_runs_on_every_seahaven_test` — a throwaway test that calls
  `stripe_api_write` is itself validated by the hook without declaring anything extra.
- `test_schema_conformance_catches_a_planted_bad_enum_value` — a stubbed object with
  `dispute.reason = "not_a_real_reason"` fails validation; proves the enum-override layer is not a
  no-op.
- `test_schema_conformance_uses_generated_enum_overrides_not_hardcoded_ones` — asserts
  `ENUM_OVERRIDES`'s key set is exactly what `prune_spec.py` currently extracts, so a spec update that
  adds or removes one of the bare-string-enum fields changes this test's fixture and not a hand-edited
  constant.
- `test_additional_properties_are_flagged` — a stubbed object with an extra, undeclared field fails
  validation (catches a serializer that leaks an internal column name).

**End-to-end / CI shape**

- `test_conformance_code_never_imports_stripe_python` — static check over `tests/conformance/`,
  `tests/schema_conformance/`, `src/seahaven_stripe_world/`.
- `test_replay_conformance` (parametrized over every conformance cassette) — the actual merge gate;
  green with no network access, using only `fixtures/empty` and the world's own tools.
- Scenario-specific conformance tests, one assertion group per §12 scenario once recorded:
  `test_proration_half_cent_tie_break_classic`, `test_proration_half_cent_tie_break_flexible`,
  `test_pagination_cursor_against_deleted_id`, `test_malformed_stripe_version_response`,
  `test_customer_charge_decline_and_idempotent_retry`, `test_subscription_upgrade_cancel_and_unpaid_recovery`,
  `test_invoice_paid_then_partial_refund_then_over_refund_rejected`, `test_dispute_lifecycle_fees_and_ledger`,
  `test_pagination_boundary_and_expand_depth_and_bad_path`, `test_error_envelope_nested_param_name`.
