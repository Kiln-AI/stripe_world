---
status: complete
---

# Component: Cross-cutting behavior

Idempotency, pagination, expansion, events, and the error envelope — the five mechanisms that are
the same on every one of the 148 operations and belong to none of them.

Architecture reference: [`architecture.md`](../architecture.md) §6 and §7. Behavior reference:
[`functional_spec.md`](../functional_spec.md) §6. Research:
[`cross-cutting-semantics/`](../research/stripe-billing-and-payments/cross-cutting-semantics/),
whose [`gap-closure-2026-09-18.md`](../research/stripe-billing-and-payments/cross-cutting-semantics/gap-closure-2026-09-18.md)
corrects several earlier claims and is authoritative wherever it disagrees with the other files.

This document settles six things the architecture left to the component design, and **corrects four
statements in `architecture.md` / `functional_spec.md`**. The corrections are collected in
[§7](#7-corrections-to-architecturemd-and-functional_specmd); each is also argued where it arises.

---

## 1. Purpose and Scope

### 1.1 What this component is

Five modules and two middleware layers:

| Thing | Lives in | Runs |
|---|---|---|
| Idempotency | `middleware/idempotency.py` | middleware, third (innermost of the three) |
| The Stripe response boundary | `middleware/stripe_envelope.py` | middleware, second |
| The error envelope | `stripe_errors.py`, `dispatch/response.py` | everywhere; rendered at the boundary |
| Pagination | `dispatch/resource.py` (`page()`) | inside the tool, inside the transaction |
| Expansion | `serialize/expand.py` | inside the tool, after the handler's writes |
| Events | `resources/events.py` (`emit_event`) | inside the tool, inside the transaction |

### 1.2 What is not this component's responsibility

- **Route matching, `ParamSpec` and coercion** — `components/dispatcher.md`. This component consumes
  `Route` (for `response_object`, `envelope` and the concrete path) and is consumed by `ParamSpec`
  (which calls this component's `expand` path validator).
- **Per-table DDL and serializer field maps** — `components/data_model.md`, except the DDL for
  `idempotency_keys` and `events`, which is given here because no resource owns it.
- **Which event fires for which state transition** — `components/billing_engine.md` and the resource
  recipe. This document settles the *mechanism*, the signature, the snapshot rule, and the ordering.
- **The decline decision** — which card declines and with what `decline_code` is
  `components/billing_engine.md`. This document settles how a decline reaches the agent without
  losing the rows it wrote.
- **The allow-list file itself** — `components/conformance.md`. This document produces the entries;
  [§6](#6-declared-for-the-conformance-allow-list) is the list to transcribe.

---

## 2. Public Interface

### 2.1 `stripe_errors.py`

```python
StripeErrorType = Literal["api_error", "card_error", "idempotency_error", "invalid_request_error"]
#: The complete wire enum. `rate_limit_error`, `authentication_error` and `permission_error` are
#: stripe-python conveniences derived from the HTTP status and never appear on the wire
#: (errors.md, "The `type` enum — only 4 wire values").


class StripeApiError(Exception):
    """A Stripe error that abandons the call. Raising one is a rollback (§2.3)."""

    def __init__(
        self,
        status: int,
        type: StripeErrorType,
        message: str,
        *,
        code: str | None = None,
        decline_code: str | None = None,
        param: str | None = None,
        doc_url: str | None = None,
        charge: str | None = None,
        advice_code: str | None = None,
        network_advice_code: str | None = None,
        network_decline_code: str | None = None,
        payment_method_type: str | None = None,
        sub_objects: Mapping[str, dict] | None = None,   # payment_intent / payment_method / setup_intent
        pre_execution: bool = False,
    ) -> None: ...

    status: int
    pre_execution: bool          #: True => the endpoint never began; the idempotency layer must not cache it
    def envelope(self) -> dict:  #: {"error": {...}} — omits every field that is None
```

Constructors, which are what handler code actually uses — `StripeApiError(...)` is called directly
nowhere outside this module:

```python
def invalid_request(message: str, *, code: str | None = None, param: str | None = None,
                    status: int = 400, pre_execution: bool = False) -> StripeApiError
def resource_missing(object_name: str, id_: str, *, param: str | None = None) -> StripeApiError
    # 404, invalid_request_error, code="resource_missing", "No such {object_name}: '{id_}'"
    # (no trailing period — live probes return it verbatim without one; an earlier draft of this
    # line carried a period nothing corroborates)
def unknown_parameter(param: str) -> StripeApiError
    # 400, code="parameter_unknown", pre_execution=True, "Received unknown parameter: {param}"
def missing_parameter(param: str) -> StripeApiError
    # 400, code="parameter_missing", pre_execution=True, "Missing required param: {param}."
def cannot_expand(segment: str, *, exists: bool, hint: str | None = None) -> StripeApiError
    # 400, invalid_request_error, no code, no param, pre_execution=True — see §3.3.5
def idempotency_mismatch() -> StripeApiError                 # 400, idempotency_error
def idempotency_key_in_use(key: str) -> StripeApiError       # 409, idempotency_error
def internal(message: str) -> StripeApiError                 # 500, api_error — see §3.5.5
```

And the one that does **not** raise:

```python
def declined(
    *, decline_code: str, message: str | None = None, charge: str | None = None,
    param: str | None = "payment_method", sub_objects: Mapping[str, dict] | None = None,
) -> dict:
    """The body of a 402 card decline — an envelope, returned, never raised.

    A decline is an outcome, not an abandonment: the handler has already written the failed
    charge, the balance-transaction-free ledger effect and the event, and those rows must
    survive. The handler pairs this with `ApiResponse(402, declined(...))`.
    """
```

`decline_code` is validated against `spec/enums.py`'s `DECLINE_CODES` — **50 entries**, two of them
(`do_not_try_again`, `try_again_later`) marked deprecated, per
`gap-closure-2026-09-18.md` item 7. An unlisted decline code is a `WorldBug`, for the same reason an
unlisted event type is (§3.4.4). The seven codes the first research pass missed
(`authentication_required`, `authentication_not_handled`, `incorrect_address`,
`invalid_expiry_month`, `offline_pin_required`, `online_or_offline_pin_required`,
`mobile_device_authentication_required`) are in the set.

### 2.2 `dispatch/response.py`

```python
@dataclass(frozen=True)
class ApiResponse:
    """What a handler returns. `status` and `body` are named exactly as the wire keys are."""
    status: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def ok(cls, body: dict) -> ApiResponse: ...        # 200
    @classmethod
    def created(cls, body: dict) -> ApiResponse: ...   # 200 — Stripe returns 200 on create, not 201


def render(response: ApiResponse) -> dict[str, Any]:
    """The tool's return value. The only place the wire shape is written."""
    return {"status": response.status, "body": response.body, "headers": response.headers}
```

The field names matter: if `stripe_envelope` were ever unregistered, Seahaven's own serialiser would
render the dataclass to the same three keys, so the failure mode of a missing middleware is a
missing header rather than a wrong wire shape.

See [§7.4](#74-the-stripe-version-header-has-nowhere-to-go-functional_spec-26-vs-25) for why there
is a third key and what the fallback is if the owner refuses it.

### 2.3 `middleware/stripe_envelope.py`

```python
@world.middleware
def stripe_envelope(ctx: seahaven.Ctx, call: seahaven.Call, next_: Handler) -> Any:
    """Turn a handler's outcome into `{status, body, headers}`. Registered second."""
```

- An `ApiResponse` return → `render(...)`.
- A `StripeApiError` → `render(ApiResponse(err.status, err.envelope(), base_headers(ctx)))`.
- Anything else (a `ToolError`, a `WorldBug`, an unexpected exception) → untouched; it is the error
  handler's.
- Applies only to `stripe_api_read` / `stripe_api_write` / `call_stripe`. `stripe_api_search` and
  `stripe_api_details` are not HTTP faces and return their own shapes; the middleware passes them
  through on tool name, the way ProjectTracker's error handler names `SQL_DOOR_TOOLS`.

### 2.4 `middleware/idempotency.py`

```python
@world.middleware
def idempotency(ctx: seahaven.Ctx, call: seahaven.Call, next_: Handler) -> Any:
    """Replay a POST that has been made before. Registered third (innermost). See §3.1."""

def request_hash(method: str, path: str, params: Mapping[str, Any] | None) -> str:
    """sha256 hex of the canonical request. §3.1.2."""
```

### 2.5 `dispatch/resource.py` — pagination

```python
@dataclass(frozen=True)
class ListPage:
    rows: list[dict[str, Any]]
    has_more: bool

    def envelope(self, url: str, serialize: Callable[[dict], dict]) -> dict:
        return {"object": "list", "data": [serialize(r) for r in self.rows],
                "has_more": self.has_more, "url": url}


def page(
    ctx: seahaven.Ctx,
    *,
    table: str,
    object_name: str,
    where: Sequence[str] = (),
    params: Sequence[SqlValue] = (),
    limit: int | None,
    starting_after: str | None,
    ending_before: str | None,
    order: PageOrder = CREATED_DESC,
) -> ListPage:
    """One page, cut by at most one cursor. Raises `StripeApiError` for a bad cursor or a bad
    `limit`; never writes."""
```

`PageOrder` is a frozen dataclass holding the two column expressions and the direction, and it is
the **only** place the ordering is written — see [§3.2.2](#322-why-created-desc-id-desc-and-why-that-is-a-problem).

### 2.6 `serialize/expand.py`

```python
def validate_paths(paths: Sequence[str], *, object_name: str, is_list: bool) -> PathTrie:
    """Check every requested path against `spec/expandable.py`, with no database access at all.

    Raises `StripeApiError` (400, pre_execution=True) for a non-expandable field, a field that does
    not exist, a missing `data.` prefix on a list, or more than four segments.
    """

def apply(ctx: seahaven.Ctx, payload: dict, trie: PathTrie, *, is_list: bool) -> dict:
    """Inflate every reference the trie names, breadth-first and batched. Read-only. §3.3.4."""
```

`PathTrie` is `dict[str, PathTrie]`; `{"customer": {"default_source": {}}}` is `customer` and
`customer.default_source` sharing one fetch of the customer.

### 2.7 `resources/events.py` — emission

```python
def emit_event(
    ctx: seahaven.Ctx,
    *,
    type: str,
    obj: dict[str, Any],
    previous: Mapping[str, Any] | None = None,
) -> str:
    """Append one `event` row and return its id.

    `type` must be in `spec/event_types.py`'s 266-entry set — an unknown type is a `WorldBug`
    (§3.4.4). `obj` is the **already-serialised API object**, not a row (§3.4.3). `previous` is the
    changed keys' prior values, rendered as `data.previous_attributes`; omit it and the key is
    absent.
    """
```

---

## 3. Internal Design Approach

### 3.0 The one fact that organises everything: where the transaction is

Read directly from `seahaven/call.py:140-163` and `seahaven/instances.py:646-666`, because every
decision below turns on it:

```
Instance._dispatch
└── _recording(i)                      ← the apsw change-log session opens HERE
    └── error_handler                  ← middleware, outermost
        └── stripe_envelope            ← middleware
            └── idempotency            ← middleware
                └── invoke             ← the innermost handler
                    ├── tool.validate(arguments)
                    └── with ctx.db.transaction():     ← the per-call transaction opens HERE
                            tool.fn(ctx, **arguments)
```

Three consequences, each load-bearing:

1. **Every middleware runs outside the per-call transaction.** By the time `stripe_envelope` sees an
   exception, the rollback has already happened. By the time `idempotency` writes its key row, the
   call has already committed or rolled back. A middleware write is therefore in autocommit and is
   *not* atomic with the call's writes — see [§3.1.6](#316-why-the-key-row-is-not-atomic-with-the-call).
2. **Every middleware runs inside the change-log session.** A middleware write to a *tracked* table
   would appear in `inst.change_log()` under the call's ordinal. This — not tidiness — is the reason
   `idempotency_keys` must be untracked.
3. **A middleware short-circuit never opens a transaction and never touches a tracked table,** so the
   call's changeset is empty and `inst.change_log()` gains zero records for it. That is the headline
   eval's property, and it is structural rather than a thing we remember to do.

One caveat on (3), and it is the one thing an eval author must be told: `_dispatch` takes a call
ordinal for **every** call, "a `ToolError` and a middleware short-circuit included"
(`instances.py:659-661`). A replay therefore advances `call_count` and appears in
`seahaven.state+calls/1`'s `calls` list. **The reward function must count change-log records, not
calls.** `one object per idempotent retry` is expressed as "exactly one `insert` record on
`charges`", never as "exactly one call".

### 3.1 Idempotency

#### 3.1.1 When the layer engages

In order, on raw `call.arguments` (middleware arguments are unvalidated —
`capability-map.md`, `world.middleware`):

1. `call.name != "stripe_api_write"` → pass through. (`stripe_api_read` is GET; a key on GET "has no
   effect", confirmed verbatim in `gap-closure-2026-09-18.md` item 1.)
2. `arguments.get("method")` is not a `str` whose `.upper()` is `"POST"` → pass through. v1 DELETE
   does not honour keys; the key is ignored silently, with no error, exactly as Stripe ignores it.
3. `arguments.get("idempotency_key")` is not a non-empty `str` → pass through. A non-string is *not*
   an error here: validation has not run yet, and raising would steal the `ArgumentError` the
   framework is about to produce with every violation in one message.

Only a POST through `stripe_api_write` carrying a non-empty string key reaches the machinery below.

#### 3.1.2 The request hash: exact inputs and canonicalisation

```python
def request_hash(method, path, params):
    canonical = json.dumps(
        [method.upper(), path, params if params is not None else {}],
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

Settled, point by point:

- **Three inputs: method, path, params.** Nothing else. The key itself is the lookup key and is not
  hashed. The API version is fixed and constant. There is no account dimension.
- **`method` is upper-cased; `path` is taken exactly as given.** No trailing-slash normalisation, no
  case folding: `/v1/Customers` is a different request, and it is a 404 anyway.
- **`params` is the raw parameter object**, before coercion, before defaults, before `ParamSpec` has
  touched it. Stripe compares "incoming parameters to those of the original request"
  (`gap-closure-2026-09-18.md` item 1, verbatim); the incoming parameters are what arrived.
- **Object keys sorted recursively** (`sort_keys=True`), so `{"a":1,"b":2}` and `{"b":2,"a":1}` are
  one request. **Arrays are order-sensitive**, including `expand[]`. `items[]` order is meaningful
  and making one array order-insensitive and not the others is a special case nobody would predict.
  Declared for the allow-list.
- **`expand` is part of the hash.** It is a parameter, and Stripe compares parameters. Excluding it
  would require a claim no source supports.
- **Separators and `ensure_ascii=False` match `_json.py`'s single dump function**, so the canonical
  string is byte-reproducible across runs — the determinism test compares two rollouts and the hash
  column must not be a source of difference.
- **sha256, stored, params discarded.** A 64-hex column keeps the table tiny and comparison O(1).
  Nothing is lost: Stripe's mismatch message names no parameter either.

The endpoint is inside the hash rather than inside the primary key. See
[§7.2](#72-a-key-is-scoped-to-the-endpoint-functional_spec-61).

#### 3.1.3 The table

`schema/004_infra.sql`:

```sql
CREATE TABLE idempotency_keys (
  key          TEXT    NOT NULL PRIMARY KEY,
  method       TEXT    NOT NULL,
  path         TEXT    NOT NULL,
  request_hash TEXT    NOT NULL,
  state        TEXT    NOT NULL CHECK (state IN ('in_flight', 'complete')),
  status       INTEGER,
  body         TEXT    CHECK (body IS NULL OR json_valid(body)),
  created      TEXT    NOT NULL
) STRICT;
```

`status` and `body` are null while `state = 'in_flight'` and non-null once `complete`. `body` holds
the rendered envelope — the whole response body, success or error — dumped through `_json.py`.
Headers are not stored: they are regenerated on replay from the current call, which is right, since
`Idempotency-Key` echoes the key that was just sent and `Request-Id` is this request's.

#### 3.1.4 The four outcomes

The layer reads `SELECT ... FROM idempotency_keys WHERE key = ?` once, and then:

**(a) Miss.** Insert the reservation, run the call, then complete or retract it:

```
INSERT INTO idempotency_keys VALUES (key, METHOD, path, hash, 'in_flight', NULL, NULL, ctx.clock.iso())
try:
    result = next_(ctx, call)                       # an ApiResponse
except StripeApiError as e:
    if e.pre_execution:  DELETE the row;  raise     # nothing cached — see below
    UPDATE ... SET state='complete', status=e.status, body=dumps(e.envelope())
    raise
except BaseException:
    DELETE the row;  raise                          # ArgumentError, WorldBug, anything
UPDATE ... SET state='complete', status=result.status, body=dumps(result.body)
return result
```

The `pre_execution` branch is Stripe's carve-out, implemented literally: *"We save results only
after the execution of an endpoint begins. If incoming parameters fail validation … we don't save
the idempotent result because no API endpoint initiates the execution. You can retry these
requests."* (`gap-closure-2026-09-18.md` item 1, verbatim.) Retracting the reservation is what makes
"you can retry these requests" true — leaving it would poison the key with a permanent 409.

The `BaseException` branch retracts for the same reason and covers `ArgumentError` (Seahaven's, not
Stripe's) and `WorldBug`. An author's bug must not burn an agent's key.

**(b) Hit, `state = 'complete'`, hash matches — the short-circuit.**

```python
return ApiResponse(row["status"], json.loads(row["body"]), base_headers(ctx))
```

`next_` is not called. `invoke` never runs, so: no argument validation, no transaction, no handler,
no `INSERT` anywhere, no event. The change-log session is open but sees nothing, so the changeset is
empty and the call contributes **zero `LogRecord`s**. A retried `POST /v1/charges` leaves exactly one
`charges` insert in the log across both calls.

The replay writes nothing to `idempotency_keys` either — no `last_used_at`, no hit counter. There is
nothing to record, and a write on the replay path would make "a replay writes nothing" a claim with
an asterisk instead of a fact.

**(c) Hit, `state = 'complete'`, hash differs.** `raise idempotency_mismatch()` —

```json
{"error": {"type": "idempotency_error",
           "message": "Keys for idempotent requests can only be used with the same parameters they were first used with."}}
```

400, no `code`, no `param`. `next_` is not called and the stored row is not touched, so this outcome
also writes nothing. The message is the string reported verbatim across many client-library issues
(`idempotency.md`, "Identical key + different params").

**(d) Hit, `state = 'in_flight'`.** `raise idempotency_key_in_use(key)` — 409,
`type: "idempotency_error"`, `code: "idempotency_key_in_use"`, the message quoted in
`idempotency.md`. In-flight beats the hash: the check is on `state` first, and a matching hash does
not make an unfinished request replayable.

This world is synchronous and single-threaded, so (d) cannot arise from concurrency. It is reachable
exactly one way: a previous call died between the reservation and the completion without running its
`finally` — a hard interpreter failure. That is the honest answer to `functional_spec.md` §6.1's
"modelled so the error exists", and it is better than a dead branch, because the state machine is
real and a test can reach it by seeding the row through `inst.bulk()`.

#### 3.1.5 Why the reservation, given it is nearly always immediately overwritten

Two reasons, and the second is the one that decides it:

1. It gives (d) a genuine trigger instead of unreachable decoration.
2. It is the only design in which a key is *never* silently lost. Without a reservation, a call that
   dies mid-flight leaves no trace at all and the next retry re-executes — which, for a charge, is
   the exact double-charge the world exists to grade. With a reservation, the retry gets a 409 and
   the operator sees a stranded row in `idempotency_keys`.

The cost is one extra `INSERT` and one `UPDATE` per keyed POST, on an untracked table, in a world
where a fork of a 120K-row fixture costs 11–29 ms.

#### 3.1.6 Why the key row is not atomic with the call

The middleware is outside the per-call transaction (§3.0), so the reservation, the completion and
the call's own writes are three separate SQLite transactions.

The alternative was available and is rejected: the idempotency middleware could open
`ctx.db.transaction()` around `next_`, in which case `invoke`'s transaction nests as a `SAVEPOINT`
(`db.py:217-224`) and the key row would commit with the call. **Do not do this.** It inverts the
nesting the framework documents (*"the per-call transaction uses one layer up, so a tool's own
`db.transaction()` nests inside it"*) and puts the apsw change-log session, which is open around the
whole chain, into a savepoint-rollback configuration that nothing in Seahaven's own test suite pins
— `test_db.py::test_a_nested_transaction_is_a_savepoint` covers `Db`, not the session. Betting the
project's headline property on unpinned interaction between the session extension and
`ROLLBACK TO SAVEPOINT` is a bad trade for atomicity that is unobservable in a synchronous,
single-connection, no-crash-recovery world.

The non-atomicity is therefore declared, not fixed. It is listed as an open question
([§8.1](#81-atomicity-of-the-key-row)) for anyone who later needs it.

#### 3.1.7 Placement in the chain, and why

Registration order in `middleware/__init__.py` — Seahaven applies middleware outermost-first in
registration order (`capability-map.md`, `world.middleware`):

```python
from stripeapi.middleware import error_handler   # 1, outermost
from stripeapi.middleware import stripe_envelope # 2
from stripeapi.middleware import idempotency     # 3, innermost
```

**Outside the error handler is wrong.** The handler's contract is "nothing reaches the agent that
this world did not choose" and it is the scaffold's, registered first. A bug in the idempotency
layer — a `json.loads` on a corrupted `body`, a `KeyError` — must reach the agent as this world's
`INTERNAL`, not as a raw traceback. Idempotency is therefore inside it.

**Inside `stripe_envelope`, not outside it.** This is a correction to `architecture.md` §6.1, which
places idempotency "immediately inside the error handler" and so outside the envelope. The reason it
cannot sit there: outside the envelope, both a success and a 400 arrive as
`{"status": …, "body": …}` dicts, and `pre_execution` — the flag that decides whether Stripe caches
the result — has already been erased by rendering. The layer would then either cache validation
failures (wrong, and directly contradicted by the verbatim doc quote) or reconstruct the
distinction by sniffing status codes and error codes, which is guesswork over a fact it could have
been handed. Inside the envelope, the layer sees `ApiResponse` returns and `StripeApiError` raises
and reads the flag off the exception.

It also means the layer stores an `ApiResponse`'s `status` and `body` rather than a rendered dict,
so the wire shape stays written in exactly one place (`render`).

#### 3.1.8 `untracked_tables`, and what it means for testing

```python
world = seahaven.World(..., untracked_tables=("idempotency_keys",), state_format="seahaven.state/1")
```

Verified against `changes.py:169-183` and `docs/concepts.md:261`. What it does and does not do:

- **Does**: removes the table from every changeset, therefore from `inst.change_log()`, therefore
  from `inst.state()`'s `{"db": {"log": [...]}}` payload. A graded episode never sees a bookkeeping
  row. Also exempts it from the explicit-primary-key requirement (`changes.py:361-362` offers
  `untracked_tables` as the alternative to giving the table a key) — we give it one anyway.
- **Does not**: hide it. The table is ordinary. It is in the instance file, it contributes to the
  **schema hash** (so a column added to it regenerates all three fixtures — `architecture.md` §12),
  and it is fully visible to `inst.inspect()` and to the control `run_sql` door, which reads "every
  table on each node … including the ones the framework knows nothing about … a world's untracked
  tables" (`control.py:71-77`).

**Testing implication, stated plainly:** an idempotency test cannot assert on the key row through
`inst.state()` or `inst.change_log()`, because neither can see it. It asserts two different things
through two different doors:

- *the replay wrote nothing* → `inst.change_log()`, filtered to the ordinals of the two calls,
  contains exactly the first call's records;
- *the key was recorded correctly* → `inst.inspect().one("SELECT state, status, request_hash FROM
  idempotency_keys WHERE key = ?", key)`.

Conflating the two is the mistake to avoid: a test that checks only the change log would pass even
if the key row were never written, and a test that checks only the key row would pass even if the
replay had re-executed the whole call.

### 3.2 Pagination

#### 3.2.1 The SQL shape

One statement per page, for every list in the world. `<cols>` is `*`; the serializer takes rows.

Forward — the default page and `starting_after`:

```sql
SELECT * FROM <table>
 WHERE <filter> AND … 
   AND (created, id) < (?, ?)        -- only when starting_after was given
 ORDER BY created DESC, id DESC
 LIMIT ?                             -- limit + 1
```

Backward — `ending_before`:

```sql
SELECT * FROM <table>
 WHERE <filter> AND …
   AND (created, id) > (?, ?)
 ORDER BY created ASC, id ASC        -- note: the direction of travel, not of the result
 LIMIT ?                             -- limit + 1
```

…and the rows are **reversed in Python** before the envelope is built, so `data` is newest-first on
every page whichever cursor produced it. That is required: Stripe's guarantee is on the response
ordering, not on the scan.

`(created, id) < (?, ?)` is SQLite's row-value comparison (3.15+), the same construction
ProjectTracker's `_pagination.Order.after` uses. It is a single index seek against
`(created DESC, id DESC)`, which every listable table carries as a covering index — declared in
`components/data_model.md`, one per table, named `idx_<table>_created_id`.

Nothing a caller supplied is ever in the SQL text. `table`, `where` and the order expressions are
this world's own strings; cursor values, filter values and `limit` are `?` parameters.

`created` filters (`{"gt": …, "lte": …}` or a bare int) arrive as Unix seconds and are converted to
canonical ISO text by `_time.py` before binding, because the column is TEXT. That conversion happens
in the `ParamSpec` layer, not here; `page()` receives finished predicates.

#### 3.2.2 Why `(created DESC, id DESC)`, and why that is a problem

`created` alone is not a key. Under a frozen clock every object created during an episode carries
the **same** `created` to the millisecond, so `ORDER BY created DESC` alone is not a total order:
SQLite may return tied rows in any order, and the same cursor could skip a row on one page and
repeat it on another. The `id` tiebreak makes the order total and the cursor exact. This is a direct
consequence of the frozen clock and `architecture.md` §6.2 is right to design for it rather than
discover it.

**But the tiebreak is not creation order, and that is a genuine fidelity defect.** `id` is
`cus_` + a suffix drawn from `ctx.ids.random` (`_ids.stripe_id`), which is seeded-deterministic but
arbitrary with respect to insertion sequence. So:

> An agent that creates three customers in one episode and then lists customers gets them back in an
> order that is stable across runs but unrelated to the order it created them in. Stripe would return
> them newest-first.

Fixture rows are unaffected — the generator writes explicit historical timestamps from a simulated
timeline (`architecture.md` §9), so they have distinct `created` and sort correctly. The defect bites
exactly on objects an agent creates, which is exactly what an eval looks at.

**Recommendation, flagged rather than taken unilaterally** because it is a data-model change and
belongs to `components/data_model.md`: give every listable table

```sql
seq INTEGER NOT NULL           -- COALESCE(MAX(seq), 0) + 1 at insert, per table
```

and order on `(created DESC, seq DESC)`. Deterministic, monotonic, one connection, no clock.

Pending that decision, this component does two things:

1. Implements `(created DESC, id DESC)` as architected, but writes the ordering **once**, in
   `PageOrder`, so the change is one line in one file plus one index per table.
2. Takes the fix unilaterally for **one** table — `events` — because `events` is in `004_infra.sql`,
   no resource owns it, and the ordering of events within a single frozen instant is the one place
   where arbitrary order is not merely unfaithful but actively misleading (a `charge.succeeded`
   listed after the `invoice.payment_succeeded` it caused). See §3.4.5.

#### 3.2.3 Resolving a cursor to a row

A cursor is an object id, not an opaque token (`pagination.md`: `type: string`, `maxLength: 5000`,
no format constraint). Resolution is one statement, **before** the page query:

```sql
SELECT created, id FROM <table> WHERE id = ?
```

- **Found** → its `(created, id)` binds the row-value predicate. The cursor is **exclusive**: the
  named object never appears in the page. Confirmed indirectly but firmly by stripe-python's
  `_get_filters_for_next_page`, which passes the current page's *last* id as `starting_after`; any
  other semantics would return that row forever.
- **Not found** → 404, `invalid_request_error`, `code: "resource_missing"`,
  `param: "starting_after"` (or `"ending_before"`), message `No such <object>: '<id>'` (no
  trailing period; see §2.1). This is the
  reading the one real datapoint supports (`stripe-node#2368`, where a cursor that stopped matching
  threw `resource_missing`), and it is the safe failure: silently returning page one for a garbage
  cursor would make an auto-paginating agent loop forever.
- **Resolution is scoped to the table, not to the filter.** A cursor naming a real customer on
  `GET /v1/customers?email=x@y.z` resolves even if that customer's email is different; it is a
  coordinate in the ordering, and only the page query applies the filter. This keeps a filtered walk
  from breaking when an object stops matching mid-walk.

**Both cursors supplied** → 400, `invalid_request_error`, `code: "parameters_exclusive"` (a real
value in the ~215-entry `code` enumeration), message
`Received both starting_after and ending_before parameters. Please pass in only one.`, no `param`.

**`limit` out of range** (`< 1` or `> 100`) → 400, `code: "parameter_invalid_integer"`,
`param: "limit"`. Absent `limit` → 10. All three numbers are verbatim from the spec's own parameter
description, identical on every list endpoint.

All of these are raised with `pre_execution=True`: they are parameter faults, the endpoint never
began, and nothing about them should be cached under an idempotency key.

#### 3.2.4 The deleted-object cursor — a designed answer to a documented silence

`gap-closure-2026-09-18.md` item 3 is explicit: the full prose page was fetched and **Stripe's docs
simply do not cover this case.** It is not a gap in our reading. So we design it.

First, a fact about this world that narrows the question: Stripe's v1 DELETE on a deletable resource
returns `{id, object, deleted: true}` and the object stays *retrievable*. So `deletable=True` in a
`ResourceSpec` does not remove the row; it sets `deleted = 1`, and every list filters
`WHERE deleted = 0`. The row survives.

**Designed behavior:**

> A `starting_after` / `ending_before` naming a soft-deleted object resolves normally. Its
> `(created, id)` is used as the positional marker exactly as if it were live. The deleted object
> itself does not appear in the page — not because the cursor excluded it, but because the list's
> own `deleted = 0` filter did.

The argument: a cursor is a coordinate, not a membership test. Under any other reading, deleting one
customer breaks every auto-paginating walk that happens to be standing on it, which is both hostile
and — per `stripe-node#2368`, the one adjacent real observation — probably not what the real API
does either. And it is the only reading under which `has_more` stays coherent, because the marker
does not have to satisfy the filter for "rows after this coordinate" to be well defined.

The residual case is a resource that is *hard*-deleted (no row at all). Those exist in this world —
`payment_method` detach, for instance, is a state change rather than a delete, but a future resource
might genuinely remove a row. For those, resolution finds nothing and §3.2.3's `resource_missing`
applies.

**Declared for the conformance allow-list**, with a recorded scenario to settle it: create two
customers, delete the older, list with `starting_after=<deleted id>`. Until that cassette exists the
world's behavior is stated, not assumed correct.

#### 3.2.5 `has_more` without a second query

Ask for `limit + 1` rows. `has_more = len(found) > limit`. Trim to `limit`.

The extra row is the entirety of what `has_more` knows, and it costs one row where `SELECT count(*)`
costs a scan — and worse, a count answers a different question than the page it describes, because
the count has no cursor in it. The spec's own description settles the boundary semantics: `has_more`
is "True if this list has another page of items after this one that can be fetched", so a page that
is exactly `limit` long and happens to be the last one reports `has_more: false`, and the probe row
is what distinguishes the two.

**Direction.** `has_more` means *"there is at least one more object in the direction of travel."*
For a default or `starting_after` page that is older objects; for an `ending_before` page it is
newer objects, and the probe is the `limit + 1`-th row of the ascending scan. This is an inference,
not a quote: it is the only reading under which stripe-python's backwards auto-pagination
terminates, given that `next_page()` short-circuits on `not self.has_more`
(`pagination.md`, "Client behavior for the empty-page terminal case") and `previous_page()` is its
mirror. Declared for the allow-list.

#### 3.2.6 The envelope

```json
{"object": "list", "data": [...], "has_more": false, "url": "/v1/customers"}
```

Exactly four keys, all four required by the spec's list schema. **No `total_count`** — every one of
the seven occurrences of `total_count` in `spec3.json` is inside a search-result schema, and even
there it is opt-in via `expand[]=total_count` and not returned by default
(`gap-closure-2026-09-18.md` item 2, correcting `pagination.md`'s own earlier framing). Search
endpoints are not routed in this world in any case.

`url` is **the concrete request path with the query string stripped**, taken from the route match —
`/v1/customers`, or `/v1/customers/cus_123/balance_transactions` for a nested list. Not the route
pattern: the spec constrains it with `pattern: "^/v1/customers"` and Stripe returns the real path.

An empty page returns `{"object": "list", "data": [], "has_more": false, "url": …}` and is not an
error, whether the list is genuinely empty or the cursor sits past the end.

#### 3.2.7 Pagination is read-only

`page()` never writes. Stated because the change log makes it checkable: a test lists every list
endpoint against the `large` fixture and asserts `inst.change_log()` gained nothing.

### 3.3 Expansion

#### 3.3.1 Where it runs

Inside the handler, inside the per-call transaction, **after** the handler's writes and immediately
before the envelope is built. So `POST /v1/customers` with `expand[]=default_source` sees the
customer it just created. Expansion itself is strictly read-only.

Path *validation* runs much earlier — in the `ParamSpec` layer, before the handler, with no database
access at all (§3.3.2). That placement matters for two reasons beyond speed: a bad path is caught
before any row is written, so a `POST … expand[]=nonsense` creates nothing; and because it is a
parameter fault it is raised `pre_execution=True`, so the idempotency layer retracts the reservation
and the agent can retry the key with a corrected path. That chain only works because validation is
static.

Static validation is possible because each `Route` declares what it returns:

```python
Route(..., response_object="customer", envelope="object")   # or envelope="list"
```

#### 3.3.2 The walk

`spec/expandable.py` is generated from the spec's `x-expandableFields` into
`{object_name: {field: target_object_name}}`. `validate_paths` walks each requested path through it:

1. Split on `.`. **More than four segments → error** (§3.3.3).
2. If `is_list`, the first segment must be exactly `data`; the walk then continues from the route's
   `response_object`. A bare first segment that *is* expandable on the item is the special-cased
   hint case (§3.3.5). `data` with nothing after it is itself an error — the real API's response to
   `expand[]=data` is `This property cannot be expanded (data).`
3. For each remaining segment, look it up in `EXPANDABLE[current_object]`. Hit → `current_object`
   becomes the target and the walk continues. Miss → error, distinguishing "exists on the object but
   is not expandable" from "is not a property at all" by consulting the object's full property list
   from `spec3.min.json` (§3.3.5).
4. **Nested list envelopes are handled by the same rule, recursively.** If a segment's value is a
   list envelope — `invoice.lines`, whose value is `{"object": "list", "data": [...]}` — the next
   segment must be `data`, and the walk continues from the item's object type. `lines.data.price` is
   three segments and valid; `lines.price` is an error with the `data.` hint. This is the `data.`
   prefix rule applied wherever a list appears, not only at the top, which is what Stripe does and
   what `x-expandableFields: ["data"]` on every list envelope encodes.

The successful result is a `PathTrie`: `["customer", "customer.default_source", "payment_intent"]`
becomes `{"customer": {"default_source": {}}, "payment_intent": {}}`. Merging paths into a trie is
what makes the fetch count depend on the shape of the request rather than on how many paths were
written.

#### 3.3.3 The depth limit

**At most four dot-separated segments, and `data.` counts as one of them.**

The citable ground truth is Stripe's own example of the deepest allowed expansion when listing
charges: `data.payment_intent.customer.default_source` (`expand.md`, "Depth limit"). Four segments.
Taking the example literally rather than reasoning about what "four levels" means gives:

- on a list: `data.` plus three object hops;
- on a retrieve/create/update: four object hops.

The asymmetry is a consequence of the literal rule, not a separate decision. The retrieve-side count
is **not independently confirmed** — the only quoted example is the list one — so a five-segment
retrieve path is declared for the allow-list with a recorded scenario.

Exceeding the limit is an error. Stripe's message for this case is not captured anywhere in the
research; this world emits the non-expandable form naming the segment that crossed the line, and the
exact text is allow-listed.

#### 3.3.4 Expansion × pagination: a 100-row page

The naive implementation is one `SELECT` per row per path, which for a 100-row charges page with
`expand[]=data.customer&expand[]=data.payment_intent.customer` is 300 statements. The resolver does
it in four, and the count does not depend on the page size.

`apply()` is breadth-first over the trie, one level at a time:

```
frontier = [payload] if not is_list else payload["data"]
for each level of the trie:
    for each field at this level:
        collect {id for obj in frontier if isinstance(obj.get(field), str)}
    group the ids by target table
    for each table:  SELECT * FROM <table> WHERE id IN (?, …)    # chunked at 500
    serialise each row once, through that resource's to_api
    substitute: obj[field] = cache[(target_object, id)]
    frontier for the next level = the objects just substituted
```

Three properties that follow:

- **Query count is bounded by (levels × distinct target tables), never by row count.** The example
  above: level 1 is one query on `customers` and one on `payment_intents`; level 2 is one more on
  `customers`, mostly served from cache; plus the page query itself.
- **One resolution cache per request**, keyed `(object_name, id)`, holding the serialised object.
  Fifty charges sharing one customer expand to fifty references to the **same dict**, serialised
  once. That is safe because nothing mutates a serialised object after substitution except deeper
  expansion, which is exactly the sharing we want: expanding `data.customer.default_source` resolves
  each distinct customer's default source once, not once per charge.
- **The cache never crosses calls.** The clock is frozen but rows are not: a customer updated by call
  N must expand to its new shape in call N+1.

Edge cases, settled:

- **Field is null** → stays null. No fetch, no error. `charge.customer` being unset is not a bad
  path.
- **Field is already an object** (a nested JSON column the serializer inflated) → `x-expandableFields`
  does not list it, so `validate_paths` rejected it before we got here.
- **Id present, row missing** → `WorldBug`. Foreign keys are on and the serializer emitted that id
  from the row we just read; a dangling reference is a schema or write bug in this world, not
  something an agent did, and it must not be dressed up as a Stripe error.

#### 3.3.5 The exact error

A bad or non-expandable path is a **hard 400, never silently ignored** — resolved and corrected in
`gap-closure-2026-09-18.md` item 4, against `expand.md`'s earlier "not confirmed".

```json
{"error": {"type": "invalid_request_error",
           "message": "This property cannot be expanded (application)."}}
```

Three message forms, all wire-quoted in the gap-closure pass:

| Case | `message` |
|---|---|
| Exists on the object, not expandable | `This property cannot be expanded (<segment>).` |
| Not a property at all | `This property cannot be expanded because it doesn't exist: <segment>.` |
| List endpoint, missing `data.` prefix | `This property cannot be expanded (<segment>). You may want to try expanding 'data.<segment>' instead.` |

Settled details:

- **The parenthesised token is the offending *segment*, not the whole path.** Both real quotes show
  this (`(application)` from a path, `(data)` from a bare field).
- **No `code` and no `param`.** The one complete wire body in the research —
  `{"error": {"message": "This property cannot be expanded (data).", "type": "invalid_request_error"}}`
  — carries neither, and `type` is the only required field on the envelope. Emitting a plausible
  `code` would be inventing one.
- Status 400, `pre_execution=True`.

### 3.4 Events

#### 3.4.1 The table

`schema/004_infra.sql`:

```sql
CREATE TABLE events (
  id             TEXT    NOT NULL PRIMARY KEY,
  seq            INTEGER NOT NULL,
  type           TEXT    NOT NULL,
  data_object    TEXT    NOT NULL CHECK (json_valid(data_object)),
  previous       TEXT             CHECK (previous IS NULL OR json_valid(previous)),
  request_id     TEXT,
  idempotency_key TEXT,
  created        TEXT    NOT NULL
) STRICT;
CREATE UNIQUE INDEX idx_events_seq ON events (seq);
CREATE INDEX idx_events_created_seq ON events (created DESC, seq DESC);
```

`events` is **tracked**. Events are Stripe state, an eval may grade them, and unlike
`idempotency_keys` they are not bookkeeping.

#### 3.4.2 When it fires

The rule, not the catalogue (the catalogue is per-resource and lives with the handlers):

> An event is emitted by the handler that made the change, **after every row for that change is
> written**, and **inside the same call transaction**. A rolled-back call emits no event, because the
> insert rolls back with everything else.

That placement is the point. An event written before the rows would survive a later
`StripeApiError` only if it were outside the transaction, and an event describing a change that did
not happen is worse than no event. An event written after the rows and inside the transaction is
correct in both directions for free.

The families, from the routed resources: `customer.*`, `product.*`, `price.*`, `coupon.*`,
`payment_intent.*`, `charge.*`, `charge.refunded`, `charge.dispute.*`, `setup_intent.*`,
`payment_method.*`, `payout.*`, `customer.subscription.*`, `invoice.*`, `invoiceitem.*`,
`credit_note.*`, `customer.discount.*`. Only the subset belonging to routed resources is ever
emitted; the 266-entry file is the validation set, not the emission set.

#### 3.4.3 How `data.object` is snapshotted

**`obj` is the already-serialised API object, not a row.** `emit_event` receives what `to_api` just
produced for the object as it now stands, and stores it verbatim:

```python
json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
```

through `_json.py`'s single dump function, so two runs that make the same change write the same
bytes.

Storing a row id and re-serialising at read time would be smaller and is wrong: `/v1/events` would
then report the object's *current* shape, so an `invoice.created` event read after the invoice was
paid would show it paid. Avoiding exactly that is what events are for. The snapshot is the frozen
past; the resource endpoint is the present.

`data.previous_attributes` is rendered from the `previous` column when the handler supplied it —
the changed keys with their prior values, which is Stripe's shape. Handlers that can cheaply compute
it (every `ResourceSpec`-generated update can, since it has the before-row) pass it; handlers deep
in the billing engine that cannot, omit it, and the key is then absent rather than null. That
asymmetry is declared for the allow-list.

`event.request` renders as `{"id": request_id, "idempotency_key": idempotency_key}`. Both are taken
from the current call: `stripe_envelope` mints a `req_`-prefixed id per dispatched call and parks it,
with the call's idempotency key, in `ctx.state["_request"]` — a plain dict living as long as the
instance (`capability-map.md`, `ctx.state`), overwritten per call, which is safe because the world is
synchronous. `request.id` is therefore **never null in this world**, whereas the real API nulls it
for events it generated itself (subscription cycle billing). With a frozen clock nothing here happens
without a call, so there are no automatic events to null it for. Declared.

This also settles `functional_spec.md` §6.1's claim that "`request.idempotency_key` is echoed on
responses" — see [§7.3](#73-requestidempotency_key-is-echoed-on-responses-functional_spec-61).

#### 3.4.4 Why an unknown type is a `WorldBug`

`type` is checked against `spec/event_types.py`'s 266-entry closed set at call time. A miss raises
`seahaven.WorldBug`, and the error handler re-raises it unchanged.

Three reasons, in order:

1. **It is an authoring mistake, by construction.** `type` is never agent input. It is a literal in
   world code. A value not in the set means the author invented an event Stripe does not have.
2. **The agent can do nothing with it.** Anything agent-visible invites the agent to handle it, and a
   gym that teaches an agent to retry around our bugs is teaching the wrong thing.
3. **It would be graded as agent behavior.** Dressed as a Stripe `api_error`, a typo in a billing
   handler becomes a 500 in a transcript and an eval scores an agent down for our defect. A
   `WorldBug` fails the test suite loudly instead, which is where it belongs — this is exactly the
   rule `capability-map.md` states for the error handler ("hiding it lies to the agent and hides a
   bug from you").

The same reasoning, and the same treatment, applies to an unlisted `decline_code` (§2.1).

#### 3.4.5 Ordering, and the one unilateral fix

`seq` is `COALESCE(MAX(seq), 0) + 1`, assigned at insert. `/v1/events` lists on
`(created DESC, seq DESC)`, and `events` is the one table where this component departs from
`architecture.md` §6.2's `(created DESC, id DESC)`.

The justification is narrow and it does not generalise on its own authority: every event in an
episode shares one `created`, and `evt_` ids are seeded-random, so `(created DESC, id DESC)` would
list a call's events in arbitrary order. For customers that is untidy; for events it is *wrong*,
because the sequence is the semantics — `invoice.payment_succeeded` before the `charge.succeeded`
that caused it is a false statement about the world. `events` is in `004_infra.sql` and no resource
owns it, so the fix is taken here. The same fix for the other nineteen tables is recommended in
§3.2.2 and left to `components/data_model.md`.

Events are immutable. `/v1/events` is list and retrieve only; there is no update, no delete, no
route for either.

### 3.5 The error envelope

#### 3.5.1 The rendered shape

```json
{"error": {"type": "...", "code": "...", "decline_code": "...", "param": "...",
           "message": "...", "doc_url": "..."}}
```

`envelope()` omits every field that is `None`, because "the API will omit attributes in error objects
when they have a null value" (`errors.md`, quoting stripe-python's own defaults block). Only `type`
is required.

`doc_url` is emitted when `code` is set and the type is `invalid_request_error` or `card_error`:
`https://stripe.com/docs/error-codes/{code.replace('_', '-')}` — Stripe hyphenates. The exact URL
form is allow-listed. `request_log_url` is **never** emitted; it is already on the architecture's
allow-list.

For a card error raised from a confirm, `sub_objects` carries the full `payment_intent` — Stripe
returns the whole PaymentIntent on the error object, which is how a client learns the intent's new
status after a decline. `charge` is a plain id string and is not expandable (`api_errors`'s own
`x-expandableFields` lists only `payment_intent`, `payment_method`, `setup_intent`, `source`).

#### 3.5.2 Error → HTTP status

**The status is carried on the error, not derived from `type`.** It has to be: 400 and 404 share
`invalid_request_error`, and nothing in the four-value enum distinguishes them. The constructors in
§2.1 are where the pairing is fixed, so no handler ever chooses a status by hand.

| Constructor | Status | `type` | `code` |
|---|---|---|---|
| `unknown_parameter` / `missing_parameter` / `cannot_expand` / bad `limit` / both cursors | 400 | `invalid_request_error` | `parameter_unknown` / `parameter_missing` / — / `parameter_invalid_integer` / `parameters_exclusive` |
| `resource_missing` | 404 | `invalid_request_error` | `resource_missing` |
| `declined` (returned, not raised) | 402 | `card_error` | `card_declined` + `decline_code` |
| `idempotency_mismatch` | 400 | `idempotency_error` | — |
| `idempotency_key_in_use` | 409 | `idempotency_error` | `idempotency_key_in_use` |
| `internal` | 500 | `api_error` | — |

Two of these are inferences worth naming. The 409's `type` is not attested anywhere: stripe-python's
status switch has no 409 branch at all and falls through to the generic `APIError`, so the wire
`type` is unobserved. `idempotency_error` is chosen because the condition is an idempotency
condition and `idempotency_key_in_use` is a `code` in the enumeration; `api_error` is the
alternative. Allow-listed. The 402's `param` defaults to `"payment_method"`, which is the parameter
an agent would fix; also allow-listed.

Method-mismatch (a routed path reached with the wrong verb) is the dispatcher's, not this
component's — `components/dispatcher.md` §3.1 — but it renders through the same envelope.

#### 3.5.3 The boundary: where an exception stops being an exception

`middleware/stripe_envelope.py`, and **only** there. Not in `tools/api.py`, not in the dispatcher,
not in a handler's `try`. One place, so there is one answer to "did this call commit".

`StripeApiError` therefore propagates out of the tool function, through `invoke`, and the per-call
transaction — which is *inside* `invoke` (§3.0) — rolls back on the way. By the time the middleware
catches it, the rollback is already done and the middleware is only formatting.

This is a correction to `architecture.md` §7, which says the error is "caught at the dispatcher
boundary". The dispatcher runs inside the tool, inside the transaction. An error caught there means
the tool *returns normally*, and a tool that returns normally **commits**. Catching at the
dispatcher boundary would silently commit the partial writes of every failed call.

#### 3.5.4 The rule — raise loses, return keeps

This is the subtlest thing in the component, so it is stated as a rule a reviewer can apply
mechanically:

> **A handler that must keep its writes returns. A handler that must lose them raises.**
>
> `StripeApiError` is a raise and is therefore *always* a rollback. There is no such thing as a
> raised Stripe error that keeps its writes, and no flag that makes one.
>
> An HTTP error status that an outcome legitimately *earned* is not a raise. A declined card, a
> failed invoice payment attempt, a payout that bounced: those calls succeeded. They wrote a failed
> `charge` row, moved the `payment_intent` to `requires_payment_method`, appended a
> `payment_intent.payment_failed` event, incremented the dunning attempt count — and every one of
> those rows must survive. The handler builds the 402 body with `stripe_errors.declined(...)` and
> **returns** `ApiResponse(402, body)`. It commits like any other successful call.

Three consequences, each checkable:

1. **The supporting rule that makes "raise = rollback" safe: every handler finishes looking things
   up before it starts writing.** A handler that writes three rows and *then* discovers the coupon is
   expired is not a rollback problem, it is an ordering bug — and it would be, because the rollback
   is correct and the work is simply wasted. This generalises `architecture.md` §7's parent-lookup
   rule from foreign keys to everything: validate, resolve, decide, then write.
2. **`declined()` returns a `dict` and has no `raise` form.** It cannot be raised, because it is not
   an exception. The type system carries the rule.
3. **A grep is the review.** Every `raise StripeApiError` and every `raise <constructor>(...)` under
   `resources/` and `billing/` must be a condition under which no row should survive. A lint test
   (§4.5) asserts that `declined` never appears as the operand of a `raise` and that `stripe_errors`
   exports no constructor that both returns a body and raises.

The half-case worth naming explicitly, because it is where an author will be tempted: *a call that
writes rows and then hits a genuine error.* For example, an invoice `pay` that creates a
`payment_intent`, then finds the customer has no payment method. Rule 1 says look first — the
payment-method check precedes the write. If some future flow genuinely cannot check first, the
answer is still to raise and lose the writes, and to accept a 400 with nothing created, because that
is what an API client expects from a 400. The only outcomes that keep writes are the ones Stripe
itself models as having happened: declines, failures, disputes. Those have a `card_error` or a
status field, not a raised exception.

#### 3.5.5 Interaction with idempotency, and with `api_error`

The rule composes cleanly with §3.1.4:

- **Returned decline** → normal `ApiResponse` return → cached under the key with status 402 and its
  body. A retry with the same key and same params replays the decline, which is correct: the card is
  still declined and re-running it would produce a second failed charge row. This is the
  succeeded-then-declined case surviving end to end.
- **Raised error, `pre_execution=False`** (a `resource_missing` from inside a handler) → the call
  rolled back, and the *error* is cached. Stripe caches results from requests that reached endpoint
  execution "success or otherwise". A retry replays the 404 without re-running anything.
- **Raised error, `pre_execution=True`** (unknown parameter, bad expand path, bad cursor) → the call
  rolled back and the reservation is retracted. Nothing is cached; the retry is a fresh request.

`api_error` is, in practice, emitted by nothing. An unexpected exception in world code is caught by
the scaffolded error handler and reaches the agent as this world's Seahaven `INTERNAL` `ToolError`,
not as a Stripe `{"status": 500}` — which is what `functional_spec.md` §2.3 asks for ("Seahaven's
declared-error mechanism is reserved for failures that are *not* Stripe responses"). So
`architecture.md` §11's conformance line "the four error `type` values" can exercise only three of
them from live behavior. The `internal()` constructor and the fourth enum value stay, the test
asserts the enum has four members and that three are reachable, and the gap is recorded. A
fault-injection route would close it and is not worth a route.

---

## 4. Dependencies

### 4.1 What this component depends on

| Depends on | For |
|---|---|
| `seahaven` — `World.middleware`, `Ctx.db`, `Ctx.clock`, `Ctx.state`, `WorldBug` | the chain, the connection, the frozen clock, authoring errors |
| `World(untracked_tables=("idempotency_keys",))` | §3.1.8 — set in `world.py`, not here |
| `spec/event_types.py` (266) | `emit_event` validation |
| `spec/enums.py` — `DECLINE_CODES` (50), `ERROR_CODES` (~215) | `declined()` and constructor validation |
| `spec/expandable.py` | `validate_paths` |
| `spec3.min.json` property lists | distinguishing "not expandable" from "does not exist" (§3.3.5) |
| `_json.py`, `_time.py`, `_ids.py` | reproducible bytes, TEXT↔Unix, `evt_`/`req_` ids |
| `dispatch/router.py` — `Route.response_object`, `Route.envelope`, the concrete path | static expand validation, the list `url` |
| `components/data_model.md` — `idx_<table>_created_id`, the `deleted` column on deletable resources | the page query and the deleted-cursor rule |

### 4.2 What depends on this component

| Depends on it | For |
|---|---|
| `dispatch/params.py` | `validate_paths`, and every parameter-fault constructor |
| every `ResourceSpec` list operation | `page()` and the envelope |
| every resource handler and all of `billing/` | `emit_event`, `declined()`, `resource_missing()`, `ApiResponse` |
| `tools/api.py` | `ApiResponse` in, `{status, body, headers}` out |
| `components/conformance.md` | the allow-list entries in §6 |
| `components/evals.md` | the change-log property in §3.0, and the reward function caveat |

### 4.3 What this component must not depend on

Nothing in `resources/` or `billing/`. `emit_event` takes a serialised object rather than a row
partly for this reason: a cross-cutting module that imports a resource module to serialise for it
inverts the layering and produces an import cycle the first time a resource wants to emit an event
about another resource.

---

## 5. Test Plan

Every test goes through `instance.call(...)`, never a bare function, so validation, the middleware
chain and the transaction are all in play. Errors are asserted by `code` and `status`, never by
message text, except the four tests below that exist specifically to pin message text.

### 5.1 Idempotency — `tests/test_idempotency.py`

| Test | Verifies |
|---|---|
| `test_replay_returns_the_identical_body` | same key, same params → byte-identical `body`, same object id |
| `test_replay_writes_no_change_log_records` | **the headline.** Two `POST /v1/charges` with one key; `inst.change_log()` holds exactly one `insert` on `charges` across both ordinals |
| `test_replay_does_not_reach_the_tool` | monkeypatched handler asserts it is called once |
| `test_replay_does_not_write_the_key_row` | `idempotency_keys` row byte-identical before and after the replay, via `inst.inspect()` |
| `test_different_params_same_key_is_idempotency_error` | 400, `type == "idempotency_error"`, no `code` |
| `test_mismatch_does_not_touch_the_stored_row` | the stored `status`/`body`/`request_hash` are unchanged after a mismatch |
| `test_mismatch_writes_nothing` | change log gains nothing for the mismatching call |
| `test_in_flight_row_yields_409` | row seeded `in_flight` through `inst.bulk()` → 409, `code == "idempotency_key_in_use"` |
| `test_in_flight_beats_a_matching_hash` | seeded `in_flight` with the matching hash still 409 |
| `test_validation_failure_is_not_cached` | unknown parameter with a key → 400; retry with the same key and *valid* params succeeds and creates the object |
| `test_validation_failure_retracts_the_reservation` | no `idempotency_keys` row survives that call |
| `test_handler_error_is_cached` | `resource_missing` under a key → replay returns the same 404 without re-running |
| `test_decline_is_cached_and_replays` | a declined charge under a key replays 402 with the same body and creates **one** failed charge row |
| `test_argument_error_retracts_the_reservation` | a type violation leaves no key row |
| `test_key_ignored_on_get` | `stripe_api_read` with a key writes no key row |
| `test_key_ignored_on_delete` | `stripe_api_write` DELETE with a key writes no key row and does not replay |
| `test_no_key_is_never_cached` | two identical keyless POSTs create two objects |
| `test_same_key_different_endpoint_is_mismatch` | pins §7.2's reading |
| `test_hash_is_order_insensitive_for_objects` | `{"a":1,"b":2}` and `{"b":2,"a":1}` replay |
| `test_hash_is_order_sensitive_for_arrays` | reordered `items[]` is a mismatch |
| `test_hash_includes_expand` | same params plus `expand[]` is a mismatch |
| `test_key_table_is_absent_from_state` | `inst.state()`'s log mentions no `idempotency_keys` record, on a fixture built with keyed writes |

### 5.2 Pagination — `tests/test_pagination.py`

| Test | Verifies |
|---|---|
| `test_default_limit_is_ten` | absent `limit` → 10 |
| `test_limit_bounds` | `0` and `101` → 400 `parameter_invalid_integer` on `param == "limit"`; `1` and `100` pass |
| `test_reverse_chronological` | `large` fixture, distinct `created` → strictly descending |
| `test_frozen_clock_tie_is_total_and_stable` | ten objects created in one episode paginate at `limit=3` with no repeat and no omission |
| `test_starting_after_is_exclusive` | the named object never appears |
| `test_ending_before_returns_the_previous_page_newest_first` | reversal in §3.2.1 |
| `test_walk_forward_covers_every_row_exactly_once` | `limit=7` across a 100-row table; ids form the full set |
| `test_walk_backward_covers_every_row_exactly_once` | the mirror |
| `test_has_more_false_on_an_exactly_full_last_page` | the probe row earns its keep |
| `test_has_more_on_an_empty_page` | past the end → `[]`, `has_more: false`, not an error |
| `test_has_more_follows_direction_of_travel` | pins §3.2.5's inference |
| `test_unknown_cursor_is_404_resource_missing` | `param` names which cursor |
| `test_deleted_cursor_resolves_and_excludes_the_object` | **pins §3.2.4's designed behavior** |
| `test_cursor_resolution_ignores_list_filters` | cursor on a customer whose email does not match the filter still resolves |
| `test_both_cursors_is_parameters_exclusive` | 400, `code == "parameters_exclusive"` |
| `test_envelope_has_exactly_four_keys` | and `object == "list"` |
| `test_no_total_count_anywhere` | every list endpoint, `large` fixture |
| `test_url_is_the_concrete_path` | including a nested list |
| `test_one_statement_per_page` | exec trace on `ctx.db.conn` counts the page query |
| `test_listing_writes_nothing` | every list endpoint, change log unchanged |

### 5.3 Expansion — `tests/test_expand.py`

| Test | Verifies |
|---|---|
| `test_unexpanded_reference_is_a_bare_id` | the default |
| `test_single_hop` | `expand[]=customer` |
| `test_recursive_hop` | `payment_intent.customer` |
| `test_four_segments_allowed_on_a_list` | `data.payment_intent.customer.default_source` — the quoted deepest example |
| `test_five_segments_rejected` | the depth error |
| `test_bare_field_on_a_list_gets_the_data_hint` | message text pinned |
| `test_non_expandable_field_message` | `This property cannot be expanded (x).` pinned |
| `test_nonexistent_field_message` | `…because it doesn't exist: x.` pinned |
| `test_expand_data_alone_is_an_error` | `(data)` |
| `test_expand_error_has_no_code_and_no_param` | §3.3.5 |
| `test_nested_list_needs_data` | `lines.price` errors, `lines.data.price` works |
| `test_null_reference_stays_null` | no error |
| `test_dangling_reference_is_a_world_bug` | seeded through `inst.bulk()` |
| `test_expand_on_create` | `POST /v1/customers` with `expand[]` sees the new row |
| `test_bad_path_on_create_writes_nothing` | change log unchanged — static validation precedes the handler |
| `test_bad_path_on_create_is_not_cached` | with an idempotency key, the retry succeeds |
| `test_hundred_row_page_query_count` | exec trace: `data.customer` + `data.payment_intent.customer` on a 100-row page is ≤ 5 statements |
| `test_shared_target_is_fetched_once` | fifty charges, one customer, one `customers` statement |
| `test_expansion_writes_nothing` | change log unchanged |

### 5.4 Events — `tests/test_events.py`

| Test | Verifies |
|---|---|
| `test_unknown_type_is_a_world_bug` | `WorldBug`, not a `ToolError`, and it escapes the error handler |
| `test_unknown_decline_code_is_a_world_bug` | the same rule for `declined()` |
| `test_every_emitted_type_is_in_the_closed_set` | greps every `emit_event` literal in `resources/` and `billing/` against `event_types.py` |
| `test_data_object_is_the_snapshot` | create an invoice, emit, pay it; the event still shows `status: "draft"` |
| `test_snapshot_bytes_are_reproducible` | same fixture and seed, two runs, identical `data_object` |
| `test_previous_attributes_on_update` | changed keys only, prior values |
| `test_rolled_back_call_emits_no_event` | a handler that emits then raises leaves no `events` row |
| `test_events_ordered_by_seq_within_one_instant` | a call emitting three events lists them in emission order |
| `test_request_is_populated` | `request.id` non-null, `request.idempotency_key` echoes the call's key |
| `test_request_idempotency_key_null_without_one` | keyless POST |
| `test_events_are_listable_and_retrievable` | `/v1/events` and `/v1/events/{id}` |
| `test_no_event_update_or_delete_route` | the route table has neither |

### 5.5 The error envelope — `tests/test_stripe_errors.py`

| Test | Verifies |
|---|---|
| `test_type_enum_has_exactly_four_values` | and that they are the four wire values |
| `test_no_rate_limit_or_authentication_type_is_reachable` | greps the source for the three client-side names as `type` values |
| `test_null_fields_are_omitted` | not rendered as `null` |
| `test_status_is_carried_not_derived` | 400 and 404 both `invalid_request_error` |
| `test_doc_url_hyphenates_the_code` | and is absent when `code` is |
| `test_request_log_url_never_emitted` | every error path |
| `test_raised_error_rolls_the_call_back` | a handler that writes then raises leaves **no** change-log records |
| `test_returned_decline_keeps_its_writes` | **the subtle one.** A declined charge leaves the failed `charge`, the `payment_intent` status change and the `payment_intent.payment_failed` event in the change log, and returns 402 |
| `test_decline_is_never_raised` | AST lint: no `raise` whose operand calls `declined` |
| `test_handlers_look_up_before_they_write` | AST lint over `resources/` and `billing/`: no `raise` of a `StripeApiError` constructor lexically after an `INSERT`/`UPDATE` in the same function body — a heuristic, with an explicit `# checked-late:` opt-out comment that must carry a reason |
| `test_envelope_is_the_only_boundary` | AST lint: `except StripeApiError` appears only in `middleware/stripe_envelope.py` |
| `test_every_decline_code_is_in_the_fifty` | and that the seven late additions are present |
| `test_error_bodies_validate_against_the_spec` | schema conformance on `api_errors` for every error path |

### 5.6 Cross-cutting integration — `tests/test_cross_cutting.py`

| Test | Verifies |
|---|---|
| `test_middleware_order` | `world.middlewares` is exactly `[error_handler, stripe_envelope, idempotency]` |
| `test_short_circuit_consumes_an_ordinal` | pins §3.0's caveat so an eval author cannot be surprised |
| `test_response_has_three_keys` | `status`, `body`, `headers` |
| `test_stripe_version_header_on_every_response` | success and error |
| `test_idempotency_key_echoed_in_headers` | and absent when none was sent |
| `test_determinism` | same fixture and seed, two rollouts including keyed retries, declines and expansions → identical ids, timestamps, change log and `idempotency_keys` contents |

---

## 6. Declared for the conformance allow-list

Entries to transcribe into `tests/conformance/allowed_differences.py`. Each is a behavior this world
implements deliberately where the real API's behavior is unverified, plus the scenario that would
settle it.

| # | Difference | Why it is declared | Settling scenario |
|---|---|---|---|
| 1 | Idempotency keys never expire | frozen clock; the real rule is a ≥24h floor, not a TTL | none — structural |
| 2 | A key reused on a different endpoint is an `idempotency_error` | §7.2; "scoped to the endpoint" admits two readings | same key, two endpoints |
| 3 | 409 `idempotency_key_in_use` carries `type: "idempotency_error"` | the wire `type` for 409 is unobserved | not reachable against the real API |
| 4 | `expand[]` array order is part of the request hash | array order-sensitivity applied uniformly | same key, reordered `expand[]` |
| 5 | A cursor naming a soft-deleted object resolves as a positional marker | §3.2.4 — a genuine silence in Stripe's docs, re-checked against the full prose page | create two, delete the older, `starting_after=<deleted>` |
| 6 | An unresolvable cursor is 404 `resource_missing` | inferred from one adjacent observation | `starting_after=cus_doesnotexist` |
| 7 | `has_more` follows the direction of travel on an `ending_before` page | inferred from client termination behavior | walk backwards to the newest page |
| 8 | `parameters_exclusive` with no `param` on both-cursors | the code is real; its `param` is unobserved | send both |
| 9 | Exact `limit`-out-of-range message | not captured | `limit=0` |
| 10 | Five-segment expand on a **retrieve** is rejected | the quoted depth example is list-side only | `expand[]=a.b.c.d.e` on a retrieve |
| 11 | Exact depth-exceeded message | not captured anywhere | as above |
| 12 | Expand errors carry no `code` and no `param` | the one complete wire body carries neither | any bad path |
| 13 | `data.previous_attributes` present only where a handler could compute it | Stripe presumably always has it | any `*.updated` from the billing engine |
| 14 | `event.request.id` is never null | no automatic operations under a frozen clock | none — structural |
| 15 | `doc_url` form `https://stripe.com/docs/error-codes/<hyphenated-code>` | form corroborated, not exhaustively | any coded error |
| 16 | `request_log_url` never emitted | already on the architecture's list | — |
| 17 | 402 `param` defaults to `"payment_method"` | plausible, unverified | any decline |
| 18 | `api_error` is not reachable from live behavior | §3.5.5 | — |
| 19 | `Stripe-Version`, `Request-Id`, `Idempotency-Key` returned in a `headers` key rather than as HTTP headers | there is no HTTP layer | — |
| 20 | Recorded requests always carry an idempotency key, replayed ones may not | stripe-python auto-generates one on every POST (`idempotency.md`) — the recorder cannot produce a keyless POST | flagged to `components/conformance.md`: the replayer must not treat a missing key as a difference |
| 21 | Objects created within one frozen instant list in seeded-random id order | §3.2.2, until the `seq` decision is taken | create three, list |

---

## 7. Corrections to `architecture.md` and `functional_spec.md`

### 7.1 Idempotency belongs inside the envelope, not outside it (`architecture.md` §6.1)

§6.1 registers idempotency "immediately inside the error handler", which places it outside the
Stripe response boundary. It cannot sit there: outside the boundary every outcome is already a
`{status, body}` dict and the `pre_execution` flag that decides Stripe's cache carve-out has been
erased. Order is `error_handler → stripe_envelope → idempotency`. Argued in §3.1.7.

### 7.2 "A key is scoped to the endpoint" (`functional_spec.md` §6.1)

Read literally this gives `PRIMARY KEY (key, method, path)` and makes the same key on a different
endpoint a *fresh* key that silently executes. That is the more dangerous behavior to emulate
wrongly, and the only verbatim doc sentence we have points the other way: the layer "compares
incoming parameters to those of the original request and errors if they're not the same" — and
method and path are part of the request.

**Settled: `PRIMARY KEY (key)`, with method and path folded into the request hash.** A key reused on
another endpoint is an `idempotency_error`. Declared (allow-list #2) with a scenario to settle it.

### 7.3 "`request.idempotency_key` is echoed on responses" (`functional_spec.md` §6.1)

This overreads the research. `idempotency.md` is explicit under "where it actually lives": the field
is on the **Event** object (`event.request.idempotency_key`), populated for events on or after
23 May 2017, and it is *not* a field on the synchronous response body. What the real API and
stripe-mock both do is echo the key as a **response header**.

Both behaviors are implemented — `event.request.idempotency_key` in §3.4.3, the header echo in
§7.4's `headers` key — but the functional spec's sentence, as written, describes a response-body
field that does not exist and should be reworded.

### 7.4 The `stripe-version` header has nowhere to go (`functional_spec.md` §6.5 vs §2.3)

§6.5 says "The real API returns a `stripe-version` response header, so this world does too" — now
confirmed by a real header dump (`gap-closure-2026-09-18.md` item 5). §2.3 says "Every tool returns
`{"status": int, "body": {...}}`". Two keys. There is nowhere to put a header, and §6.5 is
unimplementable as the two documents stand.

**Recommended resolution, taken here: a third key.** `{"status", "body", "headers"}`, where
`headers` holds `Stripe-Version` always, `Request-Id` always, and `Idempotency-Key` when one was
sent. It costs one key, it makes §6.5 and §6.1's echo true, `request-id` is a real thing agents use,
and the conformance replayer diffs bodies so headers are additive there.

**The fallback, if the owner prefers the two-key shape:** drop `headers` from `render()` — one line —
and move `Stripe-Version` to the allow-list as unrepresentable. The `ApiResponse.headers` field stays
either way, because `event.request.idempotency_key` needs the same plumbing.

This crosses into the functional spec, so it is flagged rather than decided.

### 7.5 `StripeApiError` is not caught "at the dispatcher boundary" (`architecture.md` §7)

The dispatcher runs inside the tool, inside the per-call transaction. Catching there makes the tool
return normally, which **commits** — silently committing the partial writes of every failed call.
The catch is in `middleware/stripe_envelope.py`, outside the transaction. Argued in §3.5.3, and it
is the mechanism that makes the rule in §3.5.4 work.

---

## 8. Open questions

### 8.1 Atomicity of the key row

The reservation, the completion and the call's writes are three transactions (§3.1.6). Making them
one requires the middleware to open `ctx.db.transaction()` around `next_`, which nests the per-call
transaction as a `SAVEPOINT` and puts the change-log session into a rollback configuration nothing
in Seahaven's test suite pins. **Not closed.** Closing it means either a framework test proving the
apsw session handles `ROLLBACK TO SAVEPOINT` correctly, or a framework affordance for a middleware
that wants to enclose the call's transaction. Until then the non-atomicity is unobservable in this
world and declared.

### 8.2 The `seq` column for the other nineteen tables

§3.2.2. `events` gets it here because `004_infra.sql` is unowned; the general fix is a data-model
decision. Until it is taken, allow-list entry #21 stands and an eval that grades list order over
objects created in one episode will be grading seeded-random id order. **Recommend taking it** — it
is one column, one index and one line in `PageOrder`, and it gets cheaper before `large` is built,
because every schema change regenerates all three fixtures.

### 8.3 Whether `pre_execution` covers the right set

The carve-out is "results are saved only once the endpoint has begun execution". This design maps
that to: route matching, `ParamSpec` validation, expand-path validation and cursor/`limit`
validation are pre-execution; everything raised from inside a handler is not. That is a clean line
and it matches the doc's own examples, but the real boundary is Stripe's internal one and we cannot
see it. The uncomfortable case is a `resource_missing` on a *path* parameter
(`POST /v1/customers/cus_nope` → is that "validation" or "execution"?). This design calls it
execution, so it is cached. **Not settled**; the recording scenario is a keyed POST to a nonexistent
parent followed by a retry of the same key after creating the parent — if the retry succeeds, Stripe
calls it validation and this design is wrong.

### 8.4 `metadata` `param` naming

`gap-closure-2026-09-18.md` item 6 closed the bracket-notation grammar for arrays and nested objects
with real wire examples, but left one sub-question genuinely open: how a bad key *inside* a
`metadata` object is named in `param`. This world emits `metadata[<key>]`, by analogy with
`line_items[price]`. Unverified, low stakes, not worth a recorded scenario of its own — it rides
along on whatever metadata conformance scenario `components/data_model.md` records.
