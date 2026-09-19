---
status: complete
---

# Component: Discovery

## Purpose and Scope

Discovery is the read-only layer that lets an agent find and learn about a Stripe operation without
that operation's full schema living in a tool docstring. It is one of the two halves of the agent
surface architecture.md §1 draws: the dispatcher *is* the world; discovery is how an agent finds its
way into the dispatcher without the dispatcher's 148 operations being enumerated up front.

This component owns:

- **`tools_dev/prune_spec.py`** — the build-time generator. Reads `research/stripe-openapi/spec3.json`
  (git-ignored, 8,028,700 bytes, 419 paths, 1454 schemas, version `2026-08-26.dahlia` per
  `research/MANIFEST.md`) and `src/stripeapi/dispatch/routes.py` (the 148-entry route table), and
  writes four committed, generated files under `src/stripeapi/spec/`.
- **`src/stripeapi/spec/spec3.min.json`**, **`expandable.py`**, **`enums.py`**, **`event_types.py`** —
  the generated artifacts themselves: their shape, and the algorithm that produces them.
- **`src/stripeapi/discovery/index.py`** — the runtime module that loads `spec3.min.json` once and
  answers the two discovery tools.
- **`stripe_api_search(query)`** and **`stripe_api_details(method, path)`** — the ranking algorithm,
  the rendering algorithm, and their error conditions.
- **The drift test** — how CI proves the four generated files still match what the pruner would
  produce, given that `spec3.json` itself is not present in CI.

**What is explicitly not this component's job:**

- **The dispatcher and router** — matching a real `stripe_api_read`/`stripe_api_write` call to a
  handler, the trie, `ResourceSpec`, and request-time `ParamSpec` *validation* (as opposed to
  *documentation*, which this component renders) all belong to `components/dispatcher.md`. Discovery
  reads `dispatch/routes.py` and `dispatch/params.py` as data; it does not modify or execute them.
- **The expansion resolver** — walking a requested `expand[]` path at serialize time, enforcing the
  depth limit, the `data.` prefix rule, and raising `"This property cannot be expanded"` belongs to
  `components/cross_cutting.md`. This component only generates `expandable.py`, the data that resolver
  reads.
- **Event emission and validation at call time** — `emit_event(ctx, type, obj)` belongs to
  `components/cross_cutting.md` / `billing/`. This component only generates `event_types.py`, the
  closed set that call validates `type` against.
- **Schema DDL and `CHECK` constraints** — `components/data_model.md` owns the SQL; it *consumes*
  `enums.py` as the source of truth for the six doc-only-enum columns' `CHECK` lists, exactly as
  architecture.md §4.1 requires, but does not otherwise belong here.
- **Conformance** — validating a served object against `spec3.min.json`'s schemas at test time is
  `components/conformance.md`'s schema-conformance harness. This component supplies the schema; it
  does not run the validator.
- **`stripe_api_read` / `stripe_api_write`** — the two mutating/reading tools are dispatcher-backed and
  not discovery's concern at all, beyond sharing the same 148-operation universe.

## Public Interface

### Agent-facing tools (`src/stripeapi/tools/api.py`)

```python
def stripe_api_search(ctx: seahaven.Ctx, query: str) -> list[SearchResult]:
    """Find Stripe API methods by keyword. Routed operations only."""

class SearchResult(TypedDict):
    method: str    # "GET" | "POST" | "DELETE"
    path: str      # e.g. "/v1/subscriptions/{subscription_exposed_id}"
    summary: str   # Stripe's one-line summary, HTML-stripped

def stripe_api_details(ctx: seahaven.Ctx, method: str, path: str) -> OperationDetails:
    """Parameter documentation for one routed operation."""

class OperationDetails(TypedDict):
    method: str
    path: str
    operation_id: str
    summary: str
    description: str            # first sentence of Stripe's prose, HTML-stripped
    parameters: list[ParamDoc]

class ParamDoc(TypedDict, total=False):
    name: str
    type: str                   # e.g. "string", "integer", "array<object>", "boolean | null"
    required: bool
    description: str            # first sentence only
    enum: list[str]             # present only if the field has a closed set
    fields: list[ParamDoc]      # one level of nested object fields, present only for
                                 # object-typed parameters (see rendering algorithm)
    item_fields: list[ParamDoc] # one level of nested fields for array<object> parameters
```

**Error conditions:**

| Condition | Raised by | Error |
|---|---|---|
| `query` is empty or all-whitespace after normalization | `stripe_api_search` | `errors.InvalidSearchQuery` (authoring error — a malformed call, not "no results") |
| `method` not one of `GET`, `POST`, `DELETE` | `stripe_api_details` | `errors.InvalidMethod` |
| `(method, path)` is not a routed operation — including a syntactically valid Stripe path that exists in `spec3.json` but was cut (e.g. `GET /v1/customers/search`, `GET /v1/customers/{customer}/sources`) | `stripe_api_details` | `errors.UnknownOperation` |

Both are Seahaven authoring errors (architecture.md §7) — not Stripe wire errors. An agent that calls
`stripe_api_details("GET", "/v1/customers/search")` is not told "Stripe says no such endpoint"; it is
told its own call is invalid, the same way an unusable `method` on `stripe_api_read` would be. `errors.py`
is shared infrastructure (`components/dispatcher.md`'s namespace); this component only names the two
error conditions it needs there.

A well-formed `query` that matches nothing is **not** an error: `stripe_api_search` returns `[]`.

### Internal module (`src/stripeapi/discovery/index.py`)

Framework-agnostic — no `seahaven` import, no `ctx` parameter, unit-testable as plain Python.

```python
@dataclass(frozen=True, slots=True)
class Operation:
    method: str
    path: str
    operation_id: str
    summary: str
    description: str
    parameters: tuple[ParamDoc, ...]
    # lower-cased copies of method/path/operation_id/summary/description are
    # precomputed at load time and cached as private fields, so search() never
    # calls str.lower() per query.

INDEX: tuple[Operation, ...]                      # built once, at import (see "When loading happens")
BY_KEY: dict[tuple[str, str], Operation]          # (method, path) -> Operation, O(1)

def search(query: str) -> list[SearchResult]:
    """Raises ValueError if query has no tokens after normalization."""

def details(method: str, path: str) -> OperationDetails | None:
    """Returns None — not an exception — for an unrouted or malformed
    (method, path). tools/api.py is responsible for turning that None and any
    ValueError from search() into the Seahaven ToolError subclasses above;
    this module deliberately doesn't know Seahaven exists."""
```

### Generator (`tools_dev/prune_spec.py`)

```python
def build_artifacts(full_spec: dict, routes: Sequence[Route], params: Mapping[str, ParamSpec]) -> Artifacts:
    """Pure function: (full spec3.json, routes.py's table, params.py's ParamSpecs) -> the four
    generated files' in-memory contents. No filesystem I/O — kept pure so --check can diff
    without writing."""

class Artifacts(NamedTuple):
    spec3_min: dict
    expandable: dict[str, tuple[str, ...]]
    enums: dict[str, dict[str, tuple[str, ...]]]
    event_types: frozenset[str]

def main(argv: Sequence[str]) -> int:
    """CLI. `python -m tools_dev.prune_spec` regenerates and writes the four files under
    src/stripeapi/spec/. `python -m tools_dev.prune_spec --check` regenerates in memory and exits
    non-zero if any committed file would change — see "The drift test" below. Both modes require
    research/stripe-openapi/spec3.json on disk and fail immediately, with that path named in the
    error, if it is absent."""
```

## Internal Design Approach

### 1. Scoping the operations: read `routes.py`, don't re-derive it

The pruner's first input is not a resource-root list re-typed from the functional spec — it is
`dispatch/routes.py`'s actual 148 `Route` entries, imported directly. This is the mechanism behind
architecture.md §5.1's claim ("the filter is derived from the route table rather than maintained beside
it"): there is no second list of routed paths anywhere in this component. If a route is added, removed,
or its path string typo'd, the very next `prune_spec.py` run reflects it, and a route whose
`(method, path)` has no match in `spec3.json` — a typo, or a path that doesn't exist at the pinned
`2026-08-26.dahlia` version — is a **hard generation failure**, not a silently-skipped entry
(`test_prune_spec_fails_on_unmatched_route`, below). This is stronger than architecture.md's own
phrasing suggests: it doesn't just derive discovery's scope from the route table, it validates the
route table's paths against ground truth in the same pass.

I verified independently (querying `spec3.json` directly, not trusting the research doc's arithmetic)
that this table is really achievable: `spec3.json` has exactly **419** paths and **1454** schemas at
this version; the 22 resource-root prefixes architecture.md §3.1 lists cover exactly **187** operations;
excluding the 39 legacy/search/history operations functional_spec.md §3.2 and
`minimum-closed-set-and-tool-budget.md` name (Sources/Cards/BankAccounts sub-resources,
Cash Balance, Tax IDs, funding instructions, `products/features`, the 7 `search` endpoints held for the
gated phase, `balance/history` ×2) leaves exactly **148**. Both numbers match the functional spec and
architecture exactly; this is not a new scope decision, it's confirmation the declared one is real and
reproducible from the artifact, not asserted from memory.

### 2. The schema closure walk — and the problem with doing it naively

For each kept operation, the pruner collects every `$ref` reachable from its (trimmed) `summary`,
`description`, `operationId`, `parameters`, `requestBody` and `responses` — that's the **frontier**.
From there it's a breadth-first walk over `components.schemas`: for each schema name in the frontier,
pull the full schema body, harvest every `$ref` anywhere inside it (a single recursive walk over the
whole JSON tree, not just `properties` — this is what makes `allOf` composition, `anyOf`/`oneOf`
polymorphism, `items` on arrays, and `additionalProperties` on maps like `metadata` all "just work"
without special-casing each construct), and add anything not already visited to the next frontier. A
`closure: set[str]` of already-visited names is the cycle guard — Stripe's object graph is
**genuinely cyclic** (`customer` → `subscriptions` → `subscription` → `latest_invoice` → `invoice` →
`customer`, for one concrete loop that exists in this exact data), and a walker that doesn't check
membership before recursing does not terminate.

**I ran this literally — a naive version of exactly this algorithm, no boundary awareness — against the
real 148-operation frontier, and it is a genuine problem, not a hypothetical one.** The result: **1036 of
1454 schemas** reachable, **2,066,991 bytes** compact JSON (1.97 MB). That is a 71%-of-the-spec closure
from a scope this project has deliberately kept to roughly a third of the spec's operations. The cause
is concrete and traceable: fields like `payment_intent.on_behalf_of`
(`anyOf[string, $ref:account]`), `customer_balance_transaction.checkout_session`
(`anyOf[string, $ref:checkout.session]`), and `balance_transaction.source`'s 16-way union all still
point at **full out-of-scope object schemas** in the raw spec, even though
`scope-boundary-edges.md` — already written, already the authority functional_spec.md §4 cites — rules
every one of them **null** or **stub as id-only string**. `account` alone pulls in
`account_capabilities` (15,097 B), both `issuing_*_authorization_controls` schemas (~20 KB each),
`checkout.session` (19,325 B) and `payment_link` (10,143 B) — none of which this world can ever
produce, because Connect and Checkout Sessions are out of scope by the functional spec's own §14. A
closure walk that follows every `$ref` the raw OpenAPI graph offers is not "only referenced schemas" in
the sense architecture.md §5.2 means it — it's "everything Stripe's spec *could* nest here across every
product", most of which contradicts fidelity rules this project has already settled.

**This is a real gap in architecture.md §5.2 as written**, and it needs a second mechanism the
architecture doesn't currently name: the closure walker must stop at the same scope boundary
`scope-boundary-edges.md` already drew, not just at the route table's edge.

**The fix — a stoplist derived from `scope-boundary-edges.md`'s own ruling column**, applied *before*
`$ref` harvesting on every `anyOf`/`oneOf` member:

| Ruling in `scope-boundary-edges.md` | Representative schema names stoplisted |
|---|---|
| Connect | `account`, `application`, `transfer`, `transfer_reversal`, `connect_collection_transfer`, `reserve_transaction`, `application_fee`, `application_fee_refund`, `person` |
| Stripe Tax | `tax.*` (7 schemas), `tax_product_*`, `customer_tax`, `tax_code` |
| Radar | `review`, `radar_radar_options` |
| Checkout Sessions / Payment Links | `checkout.session`, `payment_link` |
| Financial Connections / Issuing / Terminal / Treasury / Capital / Climate | `issuing.*`, `issuing_*`, `treasury.*`, `terminal.*`, `capital.*`, `climate.*`, `financial_connections*` |
| Legacy Sources/Cards/BankAccounts | `source`, `card` *(the legacy top-level schema — distinct from `payment_method_card`, which is kept)*, `bank_account`, `deleted_source`, `deleted_card`, `deleted_bank_account` |
| `mandate` / `setup_attempt` | `mandate`, `setup_attempt` |
| Cash Balance | `cash_balance`, `customer_cash_balance_transaction` |
| `smor_resource_managed_payments` | itself |

When the walker meets a `$ref` to one of these inside an `anyOf`/`oneOf`, it drops that member from the
union entirely rather than following it — the property keeps whatever sibling member is left (almost
always a bare `string`, matching how a stubbed/nulled reference actually serializes per
`serialize/expand.py`'s rule: "unexpanded references serialize as the bare id string"). A property whose
*only* member is stoplisted becomes `{"type": "string"}`. **This is not lossy relative to what the
world will ever emit** — it makes the documented and validated shape match the fidelity rules the
functional spec already committed to, rather than contradict them.

Re-running with the stoplist: **641 schemas, 1,547,709 bytes (1.48 MB)** — a 38% cut, and, more
important than the byte count, the closure no longer contains a single Issuing, Treasury, Terminal,
Capital, Climate, or Checkout-Session schema this world could ever be asked to validate an object
against.

**The list above is representative, not exhaustive** — I found two stragglers after building it
(`fee_refund`, `tax_deducted_at_source`, both small, both Connect/Tax-ledger-adjacent members of
`balance_transaction.source`'s union that weren't in my first pass) by inspecting the closure after the
fact, not by re-reading `scope-boundary-edges.md` exhaustively line by line. The implementation must
derive the final stoplist mechanically from that document's full summary table, not retype my table
above by hand into `prune_spec.py` — and `test_pruned_spec_no_residual_out_of_scope_schemas`, below, is
the backstop for whatever the hand transcription still misses at that point.

### 3. The second inflation: payment-rail fan-out, and why it needs its own pass

Even after the boundary stoplist, one more shape dominates the closure: four schemas —
`payment_method`, `payment_method_details` (on `charge`), `payment_intent_payment_method_options`, and
`refund_destination_details` — each carry **one property per value of the 57-member
`payment_method.type` enum** (`acss_debit`, `affirm`, … `zip`), each `$ref`-ing its own rail-specific
schema (`payment_method_acss_debit`, `payment_method_details_klarna`,
`payment_intent_payment_method_options_sepa_debit`, …). `scope-boundary-edges.md` already rules on this
exactly: model `card` (and `us_bank_account`) fully, **stub every other rail as `{type: "<rail>"}`
only** — and functional_spec.md §4 repeats the same ruling. But a `$ref`-closure walk has no way to
honor "stub this property, don't walk into it" on its own; it only knows how to follow or not follow a
reference.

The fix generalizes the property name itself, not the schema graph: `RAIL_NAMES` is read straight off
`payment_method.type`'s `enum` array (57 values, not hand-copied), `KEEP_RAILS = {"card",
"us_bank_account", "link"}`, and any property in the closure whose **name** is a rail not in
`KEEP_RAILS` is dropped from its parent schema's `properties` (and, if present, `required`) before that
parent's own `$ref`s are harvested. I applied this by property name rather than by naming the four hub
schemas explicitly, and it's the right call: a fifth hub — `payments_primitives_payment_records_resource_payment_method_details`,
part of a newer "Payment Records" API surface that also fans out one property per rail — showed up in
the closure and would have been missed by a hard-coded four-schema list. Property-name matching against
the rail enum catches every wrapper, present or future, without maintaining a parallel list of "which
objects happen to carry rail properties this version."

Re-running with both passes: **355 schemas, 1,304,066 bytes (1.24 MB)** — **16.2% of the original
8,028,700-byte spec, 24.4% of its 1454 schemas.** Parsing that file (measured, 20-run average) costs
**~11 ms**.

| Stage | Schemas | Compact bytes |
|---|---|---|
| Full spec | 1454 | 8,028,700 |
| Naive `$ref` closure over 148 routed ops | 1036 | 2,066,991 |
| + scope-boundary stoplist | 641 | 1,547,709 |
| + rail-fan-out trim (final) | **355** | **1,304,066** |

**Resolving the "still too large" question the task asks me to answer directly: it is not too large.**
Architecture.md §5.2's "8 MB — too large to load per instance" concern was real for the *raw* spec, but
the committed artifact is under a fortieth of that, parses in single-digit-to-low-double-digit
milliseconds, and — per "When loading happens" below — is loaded exactly once per process, never per
instance, so its cost doesn't compound with instance count at all. **No fallback or lazy-loading design
is needed.** What I'd add instead is a cheap regression guard, since the two inflation mechanisms above
are exactly the kind of thing a routine Stripe spec bump could silently reintroduce: a committed-size
assertion in the drift test (§4 below) — fail if `spec3.min.json` exceeds roughly 2.5 MB or 500 schemas,
forcing a human decision (extend the stoplist, or accept the growth) rather than a silent multi-hundred-
schema regrowth landing unnoticed in a diff.

### 4. The drift test — and the tension the task asks me to resolve explicitly

Architecture.md §5.2 says: "A test asserts the generated files match what the pruner produces from the
full spec, so a stale artifact fails CI rather than drifting. The full `spec3.json` stays in
git-ignored `research/`." **Those two sentences are in direct tension**: `spec3.json` is not present in
CI (it's git-ignored, 8 MB, and this session's own environment can't even fetch it fresh — the same
egress restriction functional_spec.md §12 documents for the conformance recorder). A test that
regenerates from the full spec cannot run where architecture.md says it must catch drift.

The resolution is two-tier, and it deliberately mirrors the shape functional_spec.md §12 already uses
for conformance cassettes (record-elsewhere, replay-only in CI, one manually-triggered re-record job) —
this is the same constraint appearing a second time, not a new one:

**Tier 1 — runs in every CI build, needs no `spec3.json`:**
- `spec3.min.json` (or a one-line sidecar next to it) records the sha256 of the exact `spec3.json`
  blob it was generated from — the same hash already committed in `research/MANIFEST.md`. A test
  compares the two committed values against each other. This can't prove the pruner's *logic* was
  applied correctly, but it proves the artifact claims to descend from the exact pinned spec blob, and
  it catches the common failure — someone hand-edits `spec3.min.json`, or regenerates against a
  different `spec3.json` than the one `MANIFEST.md` documents — without ever opening the 8 MB file.
- `discovery.INDEX`'s `(method, path)` key set is compared against `routes.py`'s, both directions —
  this is the "index ≡ route table" test architecture.md §10 already names, and it runs entirely off
  committed files.
- `enums.py` and `event_types.py` are checked against their own sources, which **are** committed:
  `event_types.py`'s 266 entries against `event-types-closed-set.txt` (also committed, so this hash
  check is real, not a proxy), `enums.py`'s six keys against a value pinned directly in the test.
- The size/schema-count regression guard from §3 above.

**Tier 2 — regeneration-and-diff, not part of default CI, run locally before any commit that touches
`prune_spec.py` or bumps the pinned spec, and by a manually-triggered CI job on a host with the spec
available (exactly parallel to the conformance re-record job):** `python -m tools_dev.prune_spec --check`
regenerates all four artifacts in memory from the real `spec3.json` and diffs them byte-for-byte (using
the project's one canonical JSON dump convention — `sort_keys=True, separators=(",", ":"),
ensure_ascii=False`, the same as `_json.py` — so the diff is meaningful and not a key-ordering false
positive) against what's committed. Non-zero exit, with a readable diff, on any mismatch.

This is a declared limitation, not a silent gap: default CI proves internal consistency (routes ↔
discovery ↔ enums ↔ event types ↔ the hash `MANIFEST.md` already commits to); only the tier-2 job, which
needs the same environment the conformance recorder needs, proves the pruner's *output* is still what
the pruner's *logic* would produce today.

### 5. The generated artifacts' shapes

**`spec3.min.json`** — `{openapi, info, paths, components: {schemas}}`, `paths` holding only the 148
routed operations (trimmed to `summary`/`description`/`operationId`/`parameters`/`requestBody`/
`responses`; HTML tags stripped from every `summary` and `description` at generation time — Stripe's
raw prose is `<p>`/`<code>`-wrapped HTML, and both discovery tools below consume this file's text
directly, so stripping once here means neither tool touches HTML at request time), `components.schemas`
holding the 355-schema closure. Dumped with the project's canonical JSON convention for byte-stable,
diffable output.

**`expandable.py`** — `EXPANDABLE_FIELDS: dict[str, tuple[str, ...]]`, one entry per schema in the
355-schema closure that carries `x-expandableFields` in the raw spec (measured: **188 of 355**),
mapping schema name straight to that array, e.g. `{"customer": ("address", "cash_balance",
"default_source", "discount", "invoice_settings", "shipping", "sources", "subscriptions", "tax",
"tax_ids", "test_clock"), ...}`. **One deliberate simplification versus architecture.md §5.1's shorthand
`{object: {field: target_resource}}`:** this file does not also resolve each field's *target* resource
type. Doing that correctly means inspecting each field's own `anyOf` member for its `$ref`'d schema's
`object` discriminator — real work, and work the expansion resolver
(`components/cross_cutting.md`) needs to do at *request* time anyway, against the very same
`spec3.min.json` this component publishes, to decide whether a requested path is even expandable at all
(a plain nested object like `automatic_tax` is in `x-expandableFields` for path-traversal purposes but
has no "target resource" to expand into). Pre-computing it here would duplicate that logic in two
places with two chances to disagree. I'm flagging this explicitly as a scope decision at the
discovery/cross-cutting boundary, not silently narrowing what architecture.md promised.

**`enums.py`** — `DOC_ONLY_ENUMS: dict[str, dict[str, tuple[str, ...]]]`, one entry per (object, field)
pair whose value set exists only in `description` prose (`spec3.json` has no `enum` key for it).
**Exactly six**, hand-transcribed from `resource-inventory.md`'s citations with a comment pointing back
at it (these cannot be regex-extracted reliably from free-text descriptions — Stripe's prose format for
this isn't consistent enough to parse, and a wrong silent extraction here is worse than a cited
hand-transcription):

```python
DOC_ONLY_ENUMS = {
    "dispute": {"reason": ("bank_cannot_process", "check_returned", "credit_not_processed",
        "customer_initiated", "debit_not_authorized", "duplicate", "fraudulent", "general",
        "incorrect_account_details", "insufficient_funds", "noncompliant", "product_not_received",
        "product_unacceptable", "subscription_canceled", "unrecognized")},                  # 15
    "payout": {
        "status": ("paid", "pending", "in_transit", "canceled", "failed"),                   # 5
        "method": ("standard", "instant"),                                                   # 2
        "source_type": ("card", "fpx", "bank_account"),                                      # 3
    },
    "refund": {"status": ("pending", "requires_action", "succeeded", "failed", "canceled")},  # 5
    "balance_transaction": {"status": ("available", "pending")},                              # 2
}
```

**A resolved discrepancy worth stating explicitly:** functional_spec.md §4 lists this set in prose as
"`dispute.reason`, `payout.status`/`method`/`source_type`, `refund.status`,
`balance_transaction.status` **and `setup_intent.usage`**" — seven fields by that sentence's own count
— while both functional_spec.md §11 and architecture.md §5.2 call it "**the six** bare-string-but-
actually-enum fields." I went to the primary source (`resource-inventory.md`'s own entry for
`setup_intent`) rather than guess which count is the typo: it says `usage` is typed as a bare `string`
with only a **doc-only default** (`off_session`), and explicitly notes "the description states the
allowed intent is `on_session` or `off_session` but does not enumerate it as a closed set
(forward-compatible)" — i.e. `setup_intent.usage` was never actually a closed doc-only enum the way the
other six are; it's an open, forward-compatible string with a documented default. **`enums.py` holds
six entries, not seven, and `setup_intent.usage` stays an unconstrained `TEXT` column with no `CHECK`.**
This is the "six" framing settled correctly, not the "seven" one — worth a one-line correction in
functional_spec.md §4's prose when this doc is reviewed, since as written it contradicts itself.

**`event_types.py`** — `EVENT_TYPES: frozenset[str]`, all **266** lines of the committed
`event-types-closed-set.txt`, verbatim, no filtering. Filtering to "only types routed resources can
actually emit" happens naturally because only `resources/`/`billing/` code calls `emit_event`, and that
code only exists for routed resources — per functional_spec.md §6.6, "the file is the validation set"
for whatever `type` a caller passes, not a pre-filtered subset. Narrowing it here would be a second,
possibly-wrong guess at which of the 266 a routed resource might emit; letting every real call site
validate against the whole authoritative set is simpler and cannot be wrong in the "resource X's event
type isn't in here" direction.

### 6. `discovery/index.py` — data structures and load

```python
def _load_index() -> tuple[tuple[Operation, ...], dict[tuple[str, str], Operation]]:
    raw = json.loads(_SPEC_PATH.read_text())          # ~11 ms, see measurement above
    ops = tuple(_build_operation(method, path, item) for path, item in raw["paths"].items()
                for method, item in item.items() if method in ("get", "post", "delete"))
    return ops, {(op.method, op.path): op for op in ops}

INDEX, BY_KEY = _load_index()
```

`_build_operation` renders `parameters` (see §7) once at load time and caches it on the frozen
`Operation`, so neither `search()` nor `details()` re-parses JSON schema shapes per call — a search or
details call is pure list/dict work over already-built Python objects.

### 7. `stripe_api_search` — ranking algorithm

Deterministic, no embeddings, no external index, exactly as required. Four fields, weighted, exact-token
match scored above substring match so that `"cancel a subscription"` doesn't get outscored by
`/v1/subscription_schedules/{schedule}/cancel` purely because "schedules" happens to contain
"schedule" as a substring of a different word:

```python
EXACT_WEIGHT   = {"path": 6, "operation_id": 5, "summary": 4, "description": 2}
SUBSTR_WEIGHT  = {"path": 3, "operation_id": 2, "summary": 2, "description": 1}

def search(query: str) -> list[SearchResult]:
    terms = [t for t in re.split(r"\W+", query.lower()) if t]
    if not terms:
        raise ValueError("empty search query")
    scored = []
    for op in INDEX:
        score = 0
        for field in ("path", "operation_id", "summary", "description"):
            tokens = op.tokens[field]          # precomputed: set(re.split(r"[^a-z0-9]+", text.lower()))
            text_l = op.lower[field]           # precomputed: text.lower()
            for t in terms:
                if t in tokens:
                    score += EXACT_WEIGHT[field]
                elif t in text_l:
                    score += SUBSTR_WEIGHT[field]
        if score > 0:
            scored.append((score, op))
    scored.sort(key=lambda pair: (-pair[0], pair[1].method, pair[1].path))
    return [{"method": op.method, "path": op.path, "summary": op.summary}
            for _, op in scored[:LIMIT]]
```

- **Fields searched**: `path`, `operation_id`, `summary`, `description` — exactly the four
  functional_spec.md §2.7 names.
- **Tie-break, made explicit as required**: sort key is `(-score, method, path)`. `(method, path)` is
  unique across all 148 routed operations (the router requires this — two routes can't share it), so
  this is a total order: equal-scoring results always come back in the same sequence, run to run,
  process to process. No result ever depends on dict-insertion order or hash seed.
- **Truncation**: `LIMIT = 10`, fixed, not agent-configurable — the public tool signature is
  `stripe_api_search(query)`, matching functional_spec.md's table exactly; there is no `limit`
  parameter to expose. 10 is generous enough that a reasonably-specific query's intended result is
  essentially always in the first page (validated by hand against `cancel a subscription`, `void
  invoice`, `refund a charge`, `list customers`, `create a coupon` — all land their best match in the
  top 3), while keeping a worst-case response small (10 × roughly `{method, path, summary}` ≈
  60–100 bytes each ⇒ under 1 KB).
- **Zero-result query**: a well-formed, non-empty query that matches nothing returns `[]`. This is the
  normal "try a different query" signal, not an error — validated directly (`"xyzzy_no_match"` → `[]`
  in the reference implementation above).
- **Empty query**: raises before scoring anything (`errors.InvalidSearchQuery` at the tool boundary) —
  a blank string is a malformed call, not a legitimate "match everything" request; returning all 148
  operations for `query=""` would defeat the entire point of a filtered discovery surface.
- **Known, accepted limitation**: no stemming. `"subscription"` (singular) exact-matches the first
  segment of `subscription_schedules` but not the plural `subscriptions` path segment, so a plural/
  singular mismatch between the query and Stripe's own path vocabulary costs the exact-match bonus.
  This is a deliberate simplicity trade-off, not an oversight — Stripe's own path vocabulary is
  consistent enough (`customers`, `subscriptions`, `invoices` throughout) that an agent doing a second
  search after a miss is cheap, and the alternative (a stemmer) is exactly the kind of "small extra
  library" the "no embeddings, no external index" instruction is steering away from.

### 8. `stripe_api_details` — rendering algorithm and measured response size

The naive approach — return `spec3.json`'s `requestBody` schema for the operation, verbatim — has two
real problems I measured directly, not estimated:

1. **Size.** `POST /v1/subscriptions`'s raw request-body schema is **23,129 bytes** (36 top-level
   parameters); `POST /v1/invoices`'s is **17,354 bytes** (33 parameters) — both fully inlined in the
   spec, no further `$ref` resolution even needed to get these numbers. That's a large response for a
   single mid-task tool call an agent is meant to read economically before making the real call.
2. **Correctness.** Architecture.md §3.3 is explicit that request parameters are validated against
   **this world's own `ParamSpec`**, not against `spec3.json` — "the spec's request bodies are far
   larger than the subset this world implements." I confirmed this concretely: `POST /v1/subscriptions`'s
   raw body includes `application_fee_percent`, `on_behalf_of`, `transfer_data` (Connect, nulled per
   `scope-boundary-edges.md`), `default_source` (legacy, nulled), and `customer_account` (not part of
   this world's object model at all). **If `stripe_api_details` simply mirrored `spec3.json`'s request
   body, it would document parameters `stripe_api_write` then rejects as `"Received unknown
   parameter"`** — actively misleading, and a direct contradiction of the same "the filter is derived
   from the routing table... so an operation cannot be advertised and unimplemented" principle
   functional_spec.md §2.7 already states for *paths*. That principle needs to extend one level deeper,
   to *parameters*, or it's false in practice for every write operation with a Connect/Tax/legacy field.

**Resolution — two changes, both measured:**

**(a) Filter parameters to `ParamSpec`'s accepted set.** The pruner reads `dispatch/params.py`'s
per-operation `ParamSpec` (the same object `components/dispatcher.md` defines and the dispatcher
validates against at call time) and keeps only request-body properties whose name is in that
operation's `creatable`/`updatable` allowlist, plus the centrally-handled `expand` and `metadata`
(architecture.md §3.3). This is the same "derive, don't duplicate" principle as the route filter: there
is exactly one place `ParamSpec` is declared, and discovery reads it rather than re-stating an
allowlist. *(`ParamSpec` itself doesn't exist yet — it's `components/dispatcher.md`'s deliverable — so
the numbers below use an illustrative drop set standing in for it: `application_fee_percent`,
`on_behalf_of`, `transfer_data`, `default_source`, `customer_account`, and, per-operation, one or two
similarly out-of-scope fields. The real `ParamSpec` will very likely drop more — Stripe's raw parameter
lists run 30+ deep and this world's actual fidelity target is narrower — so these are upper-bound
measurements, not the final numbers.)*

**(b) Flatten, don't dump raw JSON Schema.** `render_props` (already shown in the Public Interface's
`ParamDoc`) walks the (filtered) schema and emits one entry per parameter: `name`, a compact `type`
string (`array<object>`, `string | integer`, etc. — built by `type_of()`, collapsing `anyOf` into a
`|`-joined list rather than nesting it), `required`, the description's **first sentence only**, and
`enum` when present. Nested object and array-of-object parameters get **exactly one level** of their
own `fields`/`item_fields` rendered the same way, then stop — a `payment_settings` parameter shows its
own keys, but a key three levels down inside `payment_settings.payment_method_options.card` collapses to
`"type": "object"` with no further expansion. This mirrors the JSON-Schema boilerplate cost, which is
concentrated in repeated `type`/`description`/`nullable` keys at every nesting level — flattening to
depth 1 removes most of it, and description-truncation removes the rest.

**Measured result**, `ParamSpec`-filtered and depth-1-flattened, for the two largest routed request
bodies:

| Operation | Raw `spec3.json` body | Rendered `stripe_api_details` response |
|---|---|---|
| `POST /v1/subscriptions` | 23,129 B / 36 params | **7,226 B** / 30 params |
| `POST /v1/invoices` | 17,354 B / 33 params | **4,878 B** / 26 params |

Both are comfortably small for a single tool response (well under 2K tokens at a rough 4 bytes/token),
and both are almost certainly smaller still once `components/dispatcher.md` settles the real
`ParamSpec`, which will drop more than the five illustrative fields used here. A `GET`/list operation's
`details()` response is smaller again — a handful of list filters plus `expand`/`limit`/
`starting_after`/`ending_before`, no nested object parameters at all.

**Response envelope**: `{method, path, operation_id, summary, description, parameters}` — no response
*schema* is echoed. An agent doesn't need the response shape ahead of the call: it gets the real object
back from `stripe_api_read`/`stripe_api_write` directly, and echoing the response schema too would
roughly double every number in the table above for no benefit a running agent can use before it has
already made the call.

## When loading happens

**At process import, exactly once — not at instance startup, not on first call.** Discovery has no
`ctx`, no database, and no per-instance state (architecture.md §1's diagram marks it explicitly
"read-only, no state"), so there is nothing instance-specific for it to load. `discovery/index.py`'s
module-level `INDEX = _load_index()` (or lazily, `functools.cache`d on first access, if `seahaven check`
prefers no import-time I/O — a `components/dispatcher.md`-and-Seahaven-convention question, not this
component's call, but either way the parse happens once per **process**) runs before any instance
exists, and every instance created afterward — a fork is a SQLite file copy, not a fresh Python
interpreter — shares the same `tuple[Operation, ...]` by reference. The measured cost (~11 ms to parse
1.24 MB) is paid once per process, amortized across every rollout that process serves; it does not
appear in per-instance fork-cost measurements at all, and it's roughly a thousandth of the `large`
fixture's own fork cost (11–29 ms, per architecture.md §9) even before amortization.

## Dependencies

**Depends on (upstream):**

- `research/stripe-openapi/spec3.json` — build-time only, read by `tools_dev/prune_spec.py`; never
  imported or read at runtime. Git-ignored; pinned by `research/MANIFEST.md`'s sha256 and `info.version`.
- `dispatch/routes.py` (`components/dispatcher.md`) — the 148-entry route table; the sole source of
  which operations discovery may ever mention. A pruner run against a route with no matching
  `(method, path)` in `spec3.json` fails generation.
- `dispatch/params.py`'s `ParamSpec` (`components/dispatcher.md`) — filters each operation's request
  body down to the parameters `stripe_api_write` actually accepts, so `stripe_api_details` cannot
  document a parameter the dispatcher will reject.
- `research/.../api-surface-and-object-graph/scope-boundary-edges.md` — source of the closure-walker's
  stoplist (§2 above).
- `research/.../api-surface-and-object-graph/resource-inventory.md` — source of `enums.py`'s six
  hand-transcribed doc-only enum sets.
- `research/.../api-surface-and-object-graph/event-types-closed-set.txt` — the committed 266-entry
  source `event_types.py` is a verbatim transcription of.
- `research/MANIFEST.md` — the committed sha256 the tier-1 drift test checks `spec3.min.json`'s
  provenance claim against.
- Seahaven's world-import mechanics (`vendor/Seahaven/src/seahaven/docs/authoring.md`) — module-level
  code in a `tools/`-imported module runs once at world import; this component relies on exactly that
  behavior for "loaded once, shared across every instance."

**Depended on by (downstream):**

- `tools/api.py` — registers `stripe_api_search` and `stripe_api_details` as thin wrappers over
  `discovery/index.py`, translating its `None`/`ValueError` into `errors.UnknownOperation` /
  `errors.InvalidSearchQuery`.
- `serialize/expand.py` (`components/cross_cutting.md`) — the expansion resolver reads
  `expandable.py`'s `EXPANDABLE_FIELDS` to know which dotted paths are legal to request, and reads
  `spec3.min.json` directly (not a value this component pre-computes, per §5's `expandable.py` note) to
  resolve a given field's target object type.
- `billing/`, `resources/` (`emit_event`, per architecture.md §6.3) — validate every emitted `type`
  against `event_types.py`'s `EVENT_TYPES` frozenset at call time.
- `schema/*.sql` and the table `CHECK` constraints (`components/data_model.md`) — the six doc-only-enum
  columns (`disputes.reason`, `payouts.status`/`method`/`source_type`, `refunds.status`,
  `balance_transactions.status`) source their `CHECK (... IN (...))` lists from `enums.py`'s
  `DOC_ONLY_ENUMS`, per architecture.md §4.1's "taken from `spec/enums.py` so the schema and the
  conformance validator cannot disagree."
- `tests/conformance/` schema-conformance harness (`components/conformance.md`) — validates every
  served object against `spec3.min.json`'s schemas, and separately against `enums.py`'s six doc-only
  sets (since those fields carry no machine-readable `enum` for a generic validator to check).

## Test Plan

**Pruner correctness**

- `test_prune_spec_operation_set_matches_routes` — the set of `(method, path)` pairs in generated
  `spec3.min.json["paths"]` equals `routes.py`'s route set exactly, both directions.
- `test_prune_spec_fails_on_unmatched_route` — a `Route` pointing at a `(method, path)` absent from
  `spec3.json` makes `build_artifacts` raise, rather than silently omitting it.
- `test_prune_spec_excludes_the_39_cut_operations` — spot-checks that `GET /v1/customers/search`,
  `GET /v1/customers/{customer}/sources`, `GET /v1/balance/history`, `GET /v1/products/{product}/features`
  and the other six `search` endpoints are absent from `spec3.min.json`.
- `test_pruned_spec_no_out_of_scope_schema_names` — no schema name (or nested `$ref`) anywhere in the
  generated `components.schemas` matches the stoplist prefixes/names from §2 (`account`, `checkout.*`,
  `issuing.*`, `treasury.*`, `terminal.*`, `capital.*`, `climate.*`, `tax.*`, `mandate`,
  `setup_attempt`, `source`, `card`, `bank_account`, …) — the completeness backstop for the
  hand-transcribed stoplist noted as non-exhaustive in §2.
- `test_pruned_spec_rail_stubs` — for each of `payment_method`, `payment_method_details`,
  `payment_intent_payment_method_options`, `setup_intent_payment_method_options`,
  `refund_destination_details`, and any other schema carrying one property per `payment_method.type`
  value, only `{card, us_bank_account, link}` remain.
- `test_pruned_spec_html_stripped` — no `summary` or `description` string anywhere in
  `spec3.min.json` contains `<` or `>`.
- `test_pruned_spec_size_budget` — `spec3.min.json` is under 2.5 MB and its schema count is under 500;
  regression guard against a future Stripe spec bump silently reintroducing the inflation from §2/§3.
- `test_prune_spec_is_deterministic` — running `build_artifacts` twice on the same inputs produces
  byte-identical output (canonical JSON dump, sorted keys).

**Drift (the two-tier design in §4)**

- `test_committed_artifacts_declare_matching_source_hash` — the sha256 recorded alongside
  `spec3.min.json` equals the one committed in `research/MANIFEST.md`. Runs in default CI, no
  `spec3.json` needed.
- `test_event_types_matches_committed_txt` — `event_types.EVENT_TYPES` as a set equals the 266 lines
  of `event-types-closed-set.txt`; `len(EVENT_TYPES) == 266`.
- `test_enums_py_has_exactly_six_entries` — `DOC_ONLY_ENUMS`'s flattened `(object, field)` key set
  equals the six named in §5, with their exact value tuples pinned in the test (a value silently
  changing is exactly the kind of drift this test exists to catch).
- `test_expandable_py_spot_checks` — `EXPANDABLE_FIELDS["customer"]`,
  `["subscription"]`, `["invoice"]` match fixed, hand-verified tuples.
- `test_prune_spec_check_mode_matches_committed` — **only runs when `research/stripe-openapi/spec3.json`
  is present** (skipped otherwise, with a clear skip reason, in default CI); regenerates all four
  artifacts and diffs byte-for-byte against what's committed. This is the tier-2 check from §4, run
  locally and by the manually-triggered spec-drift job.

**`discovery/index.py`**

- `test_discovery_index_matches_route_table` — `INDEX`'s `(method, path)` keys equal `routes.py`'s,
  both directions (architecture.md §10's "index ≡ route table" test).
- `test_unrouted_operation_is_invisible` — `details("GET", "/v1/customers/search")` returns `None`
  even though that path exists in the full `spec3.json` (confirms filtering, not just absence).
- `test_discovery_loaded_once_per_process` — two separate `instance()` creations from the same world
  observe `discovery.index.INDEX is discovery.index.INDEX` (module-level identity), confirming it is
  not rebuilt per instance.

**`stripe_api_search`**

- `test_search_is_deterministic` — the same query run twice (including across a fresh process) returns
  byte-identical ordering.
- `test_search_tie_break_orders_by_method_then_path` — two synthetic equal-scoring operations come
  back ordered by `(method, path)`.
- `test_search_empty_query_raises` — `""`, `"   "` each raise `errors.InvalidSearchQuery`.
- `test_search_zero_result_returns_empty_list` — a nonsense query (`"xyzzy_no_match"`) returns `[]`,
  not an error.
- `test_search_results_are_routed_operations` — every `(method, path)` returned by `search()` for a
  battery of realistic queries is a key in `routes.py`.
- `test_search_result_count_never_exceeds_limit` — no query returns more than 10 results.
- `test_search_quality_spot_checks` — for `"cancel a subscription"`, `"void invoice"`,
  `"refund a charge"`, `"list customers"`, `"create a coupon"`, the intuitively-correct operation
  appears in the top 3 results (documents the known singular/plural limitation from §7 rather than
  silently accepting a regression in it).

**`stripe_api_details`**

- `test_details_unknown_operation_raises` — `details("GET", "/v1/customers/search")` (real Stripe path,
  not routed) and a fully invented path both raise `errors.UnknownOperation` at the tool boundary.
- `test_details_invalid_method_raises` — `method="PATCH"` raises `errors.InvalidMethod` before any
  lookup.
- `test_details_parameters_match_paramspec` — for every routed write operation, the parameter names in
  `stripe_api_details`'s response equal `ParamSpec`'s `creatable`/`updatable` keys plus
  `expand`/`metadata` — the cross-check that closes the "documents what's rejected" problem from §8.
- `test_details_response_size_budget` — `stripe_api_details("POST", "/v1/subscriptions")` and
  `("POST", "/v1/invoices")` — the two largest routed operations — each serialize under 10 KB;
  regression guard against the size problem from §8 reappearing.
- `test_details_nesting_depth_is_one_level` — a nested-object parameter's `fields` entries never
  themselves carry a `fields`/`item_fields` key (enforces the depth-1 flattening design in §8).
- `test_details_description_is_one_sentence` — no `description` field, at any nesting level, contains
  more than one `.`-terminated sentence (loosely — first-sentence truncation is doing its job).
