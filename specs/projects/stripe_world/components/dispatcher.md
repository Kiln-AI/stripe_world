---
status: draft
---

# Component: Dispatcher

The thing behind the four tools. A call arrives as `(method, path, params)`; this component decides
which of the 148 operations that is, decides whether the parameters are legal, runs the operation,
and turns whatever comes back into `{"status": int, "body": {...}}`. Everything else in the world —
the resources, the billing engine, the serializer, discovery — hangs off it.

Files: `dispatch/router.py`, `dispatch/routes.py`, `dispatch/resource.py`, `dispatch/params.py`,
`dispatch/response.py`, and `tools/api.py` for the four tool faces
([`architecture.md`](../architecture.md) §2).

> **Read §1.3 and §5 first if you are reviewing.** Two numbers in `architecture.md` §3.2 are wrong,
> and one rule in it cannot be honoured as written. All three are corrected here.

## 1. Purpose and Scope

### 1.1 What this component does

1. **Holds the route table.** `routes.py` is 148 `Route` values — the single source of scope for the
   whole project. Discovery filters `spec3.json` through it; the conformance harness enumerates it.
2. **Matches a path to a route.** A compiled trie over `/`-split segments, with exact segments
   beating placeholders, and a joint `(method, path)` match that distinguishes "no such URL" from
   "wrong verb for this URL".
3. **Validates parameters.** Against *our* `ParamSpec` allowlists, not against `spec3.json`, because
   Stripe rejects unknown parameters and that needs a closed set we control
   ([`architecture.md`](../architecture.md) §3.3).
4. **Serves the generated CRUD operations.** 77 of the 148 have no handler function at all: the
   `ResourceSpec` engine answers them.
5. **Invokes the 71 hand-written handlers** with one uniform signature.
6. **Builds the response.** Expansion, the list envelope, the status code, and the conversion of a
   raised `StripeApiError` into Stripe's error envelope — including the rollback that must go with
   it.

### 1.2 What this component does **not** do

| Not ours | Whose |
|---|---|
| Row → API object field mapping, Unix-second conversion, nested JSON inflation | `serialize/`, designed in [`data_model.md`](data_model.md) |
| The `x-expandableFields` map and the path resolver it drives | [`cross_cutting.md`](cross_cutting.md); we call it |
| Idempotency | `middleware/idempotency.py`, [`cross_cutting.md`](cross_cutting.md). It sits *outside* us (§3.8.3) |
| `emit_event` and the event-type closed set | [`cross_cutting.md`](cross_cutting.md); `ResourceSpec` names event types, it does not implement them |
| Every status machine, proration, dunning, the ledger | [`billing_engine.md`](billing_engine.md) |
| The `stripe_api_search` / `stripe_api_details` *bodies* | [`discovery.md`](discovery.md). Their **signatures** are settled here (§2.1) because all four tools must agree |
| DDL, columns, id prefixes, enum sources | [`data_model.md`](data_model.md) |
| The seven `/v1/*/search` endpoints | Out until the gated final phase (functional spec §3.3). Adding them is +7 `Route` entries and a `search` action; nothing here changes |

We also do **not** own the *design* of cursor pagination even though its code lives in our file:
`architecture.md` §6.2 puts the implementation in `dispatch/resource.py` and §13 puts the design in
`cross_cutting.md`. That split stands — we specify the call (§3.5.1) and `cross_cutting.md` specifies
`has_more`, the envelope and the deleted-cursor question.

### 1.3 The numbers, verified against `spec3.json`

Everything below was counted from `/home/user/stripe_world/research/stripe-openapi/spec3.json`
(`info.version` = `2026-08-26.dahlia`), not from memory.

**The 148 holds.** 187 `get`/`post`/`delete` operations under the 22 resource-root prefixes, minus
39 cut operations, is exactly 148. The 39 are `bank_accounts` (6), `cards` (5), `sources` (6),
`cash_balance*` (4), `tax_ids` (4), `funding_instructions` (1), `products/features` (4), `search`
(7), `balance/history` (2). One footnote for the record: `minimum-closed-set-and-tool-budget.md`
writes that parenthetical as "all four `search` endpoints", but there are seven and the total only
reaches 39 with all seven. The count is right; that phrase is not.

**The 148 is 94 distinct paths**, maximum depth 6 segments, with this method spread:

| Methods on a path | Paths |
|---|---|
| `GET` + `POST` | 36 |
| `POST` only | 34 |
| `GET` only | 13 |
| `DELETE` + `GET` + `POST` | 8 |
| `DELETE` + `GET` | 2 |
| `DELETE` only | 1 |

**The generated/hand-written split in `architecture.md` §3.2 is wrong.** It estimates ~95 generated
and ~53 hand-written. Applying the rule in §3.5 — *an operation is generated iff its whole behavior
is validate → touch one row (or one page of rows) of one table → serialize* — the real split is:

| Resource | Ops | Generated | Hand-written |
|---|---|---|---|
| `balance` | 1 | 0 | 1 |
| `balance_transactions` | 2 | 2 | 0 |
| `charges` | 13 | 6 | 7 |
| `coupons` | 5 | 5 | 0 |
| `credit_notes` | 8 | 3 | 5 |
| `customers` | 20 | 12 | 8 |
| `disputes` | 4 | 2 | 2 |
| `events` | 2 | 2 | 0 |
| `invoiceitems` | 5 | 5 | 0 |
| `invoices` | 17 | 3 | 14 |
| `payment_intents` | 11 | 2 | 9 |
| `payment_methods` | 6 | 4 | 2 |
| `payouts` | 6 | 3 | 3 |
| `prices` | 4 | 4 | 0 |
| `products` | 5 | 5 | 0 |
| `promotion_codes` | 4 | 4 | 0 |
| `refunds` | 5 | 3 | 2 |
| `setup_intents` | 7 | 2 | 5 |
| `subscription_items` | 5 | 2 | 3 |
| `subscription_schedules` | 6 | 2 | 4 |
| `subscriptions` | 8 | 2 | 6 |
| `tax_rates` | 4 | 4 | 0 |
| **Total** | **148** | **77** | **71** |

**77 generated, 71 hand-written**, not 95/53. The estimate went wrong by assuming every
`POST /v1/<collection>` is a generated create. It is not: `POST /v1/charges`, `/v1/refunds`,
`/v1/payouts`, `/v1/payment_intents`, `/v1/setup_intents`, `/v1/subscriptions`,
`/v1/subscription_items`, `/v1/subscription_schedules`, `/v1/invoices` and `/v1/credit_notes` each
move money, start a status machine, or build lines across two tables. Ten of the twenty-two
collections have a hand-written create, and `invoices` alone contributes 14 hand-written operations.

71 routes do not mean 71 functions: eight routes are aliases sharing a handler with a canonical route
(§3.1.3), so **63 distinct hand-written functions**. That is the number the implementation plan
should budget against.

This correction matters beyond bookkeeping. 71 hand-written operations at roughly four to six of them
per resource module is the real shape of phases 2–6, and a plan built on "~53, mostly small state
transitions" would under-budget `invoices` by a factor of three.

## 2. Public Interface

### 2.1 The four tools, as Seahaven will accept them

`tools/api.py`. Every constraint from `world.tool`'s registration checks
([`capability-map.md`](../research/stripe-billing-and-payments/seahaven-capabilities/capability-map.md),
"Registration verbs") is honoured: first parameter `ctx`, every argument annotated, no `Enum` or
`datetime`/`date`/`time` annotation anywhere, no mutable default, no positional-only parameter, no
`*args`/`**kwargs`, and a return type that is plain JSON (`bytes` and `set` are refused).

```python
@world.tool
def stripe_api_read(
    ctx: seahaven.Ctx,
    path: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read data from the Stripe API with a GET method. ..."""
    return dispatch(ctx, "GET", path, params, idempotency_key=None)


@world.tool
def stripe_api_write(
    ctx: seahaven.Ctx,
    method: Literal["POST", "DELETE"],
    path: str,
    params: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Write data to the Stripe API with a POST or DELETE method. ..."""
    return dispatch(ctx, method, path, params, idempotency_key=idempotency_key)


@world.tool
def stripe_api_search(ctx: seahaven.Ctx, query: str, limit: int = 10) -> dict[str, Any]:
    """Find Stripe API methods by keyword. ..."""       # body: discovery.md


@world.tool
def stripe_api_details(
    ctx: seahaven.Ctx,
    method: Literal["GET", "POST", "DELETE"],
    path: str,
) -> dict[str, Any]:
    """Parameter detail for one Stripe API method. ..."""   # body: discovery.md
```

Six decisions in those four signatures:

- **`method` is a `Literal`, not a `str`.** The closed set then appears in the tool's JSON schema, so
  an agent reads the legal verbs rather than discovering them by being refused, and `Enum` — which
  registration refuses outright — never comes up. A `GET` sent to the write tool is an
  `ArgumentError` from Seahaven, which `middleware/error_handler.py` restates as the world's
  `INVALID_INPUT`, **not** a Stripe envelope. That is deliberate and is what functional spec §2.3
  asks for: an unusable `method` is an authoring error, not a Stripe response.
- **`params` is `dict[str, Any] | None`, defaulting to `None`.** `{}` would be a mutable default and
  registration refuses it. `Any` is what lets Stripe's naturally nested JSON —
  `{"items": [{"price": "price_123"}]}` — arrive intact; pydantic gives `dict[str, Any]` the schema
  `{"type": "object"}`, which registration accepts. Every value inside is then our business, checked
  by `ParamSpec` (§3.3) rather than by pydantic, because the legal shape differs per operation.
- **`params` is validated as a whole dict before we see it.** A non-object `params` is an
  `ArgumentError` from strict validation, so §3.3 never has to handle "params is a list".
- **`idempotency_key` is on the write tool only**, because `GET` requests take no key (functional
  spec §2.2), and it is a named parameter rather than a key inside `params` so it shows in the tool
  schema — the headline eval grades an agent on using it.
- **`stripe_api_details` accepts either a pattern or a concrete path.** `/v1/customers/{customer}`
  and `/v1/customers/cus_123` both resolve, because the matcher (§3.2) already turns a concrete path
  into its `Route`. An agent that just made a call and wants the parameter list for it should not
  have to re-abstract the URL it used.
- **No docstring names another tool.** Lint `SH206`: a prefixing host renames tools without
  rewriting descriptions ([`architecture.md`](../architecture.md) §12).

All four keep `transaction=True` (the default). `stripe_api_read` opts in too even though it writes
nothing: the read path can still raise mid-way, and a uniform transaction is one fewer special case
in §3.8.

**The escape hatch.** Functional spec §2.5 records that a generic `call_stripe(method, path, params)`
is a few lines over the same dispatcher. It is:

```python
def call_stripe(
    ctx: seahaven.Ctx,
    method: Literal["GET", "POST", "DELETE"],
    path: str,
    params: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    return dispatch(ctx, method, path, params, idempotency_key=idempotency_key)
```

It lives in `tools/api.py` **undecorated**, with a one-line comment saying why, and a test calls it
directly so it cannot rot. Registering it is one decorator for a harness that wants the raw-HTTP
surface.

### 2.2 `dispatch` — the entry point

```python
def dispatch(
    ctx: seahaven.Ctx,
    method: str,
    path: str,
    params: Mapping[str, Any] | None,
    *,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """One Stripe API call. Always returns {"status": int, "body": {...}}; never raises
    StripeApiError."""
```

Raises only `WorldBug` (our own bug) and `seahaven.DbError` (which the error-handler middleware turns
into the world's `INTERNAL`). Every agent-visible failure is a return value.

### 2.3 `Router`

`dispatch/router.py`:

```python
class Router:
    def __init__(self, routes: Sequence[Route]) -> None:
        """Compile the trie. Raises WorldBug on a duplicate (method, pattern), a pattern that
        is not absolute, a malformed placeholder, or a ParamSpec whose `path` names do not
        match the pattern's placeholders in order."""

    def match(self, method: str, path: str) -> Match:
        """The route for this call.

        Raises StripeApiError(404, "invalid_request_error", message="Unrecognized request URL
        (<METHOD>: <path>).") when no pattern matches the path at all, and
        StripeApiError(405, "invalid_request_error", message="Not allowed: <METHOD> <path>")
        when a pattern matches but carries no route for this method."""

    def resolve(self, method: str, path: str) -> Route | None:
        """`match` without the raising, for discovery.md's use of a concrete path."""


@dataclass(frozen=True, slots=True)
class Match:
    route: Route
    path_values: tuple[str, ...]   # captured segments, in pattern order


ROUTER: Final = Router(routes.ALL)    # module-level, compiled once at import
```

`ROUTER` is built at import, not per call. It is immutable and holds no `ctx`, so one instance serves
every instance of the world.

### 2.4 The dataclasses

All frozen, all `slots=True`, all in `dispatch/`. Frozen because they are module-level constants read
by every call in every instance; a mutable one would let a call leave a footprint in the next.

#### `Route` — `dispatch/routes.py`

```python
Method = Literal["GET", "POST", "DELETE"]
Action = Literal["list", "create", "retrieve", "update", "delete"]

@dataclass(frozen=True, slots=True)
class Route:
    method: Method
    pattern: str                      # "/v1/customers/{customer}/balance_transactions"
    op_id: str                        # spec3.json operationId, exactly
    params: ParamSpec
    resource: ResourceSpec | None = None
    action: Action | None = None
    handler: Handler | None = None
    scope: Scope | None = None
    alias_of: str | None = None       # the op_id this one delegates to
```

**`handler is None` is the definition of "generated".** That is the rule the count in §1.3 is built
on, it is one attribute to read, and `test_router.py::test_generated_count_is_77` asserts it. Exactly
one of `handler` and `(resource, action)` is set; the other combination is a `WorldBug` at import.

`op_id` is the spec's `operationId` verbatim (`PostCustomersCustomerBalanceTransactions`), which is
what makes discovery's "index ≡ route table" test a set comparison rather than a path-normalisation
exercise.

A real entry, generated:

```python
Route(
    method="GET",
    pattern="/v1/charges/{charge}/refunds",
    op_id="GetChargesChargeRefunds",
    params=ParamSpec(op_id="GetChargesChargeRefunds", path=("charge",), paginated=True),
    resource=refunds.SPEC,
    action="list",
    scope=Scope(path_param="charge", column="charge_id", parent=charges.SPEC),
)
```

and hand-written:

```python
Route(
    method="POST",
    pattern="/v1/invoices/{invoice}/finalize",
    op_id="PostInvoicesInvoiceFinalize",
    params=INVOICE_FINALIZE,
    handler=invoices.finalize,
)
```

#### `Scope` — how a nested collection is constrained

```python
@dataclass(frozen=True, slots=True)
class Scope:
    path_param: str            # "charge"
    column: str                # "charge_id" on the child table
    parent: ResourceSpec       # looked up first, so a missing parent is a 404 not a DbError
```

`Scope` is what keeps `GET /v1/charges/{charge}/refunds`,
`GET /v1/customers/{customer}/balance_transactions`, `GET /v1/customers/{customer}/payment_methods`
and `GET /v1/customers/{customer}/subscriptions` generated instead of four hand-written lists. It
also carries the parent lookup that `architecture.md` §7 requires: the engine `SELECT`s the parent
through `resources/_lookup.py` before it queries the child, so a bad `{charge}` is Stripe's
`resource_missing` rather than an empty page.

#### `ParamSpec` — one operation's allowlist

```python
Kind = Literal[
    "string", "integer", "boolean", "number",
    "id", "literal", "object", "array", "range", "timestamp",
]

@dataclass(frozen=True, slots=True)
class Param:
    name: str
    kind: Kind
    required: bool = False
    choices: tuple[str, ...] = ()          # kind="literal"
    id_prefixes: tuple[str, ...] = ()      # kind="id": ("ch",) or ("pi", "ch")
    minimum: int | None = None
    maximum: int | None = None
    max_length: int | None = None
    shape: tuple["Param", ...] = ()        # kind="object"
    item: "Param | None" = None            # kind="array"
    unset_with_empty_string: bool = False  # Stripe's "" means "clear this field"
    column: str | None = None              # None ⇒ same name as the column

@dataclass(frozen=True, slots=True)
class ParamSpec:
    op_id: str
    path: tuple[str, ...] = ()             # placeholder names, in pattern order
    body: tuple[Param, ...] = ()
    expand: bool = True
    paginated: bool = False
    metadata: bool = False
    required_one_of: tuple[tuple[str, ...], ...] = ()
    mutually_exclusive: tuple[tuple[str, ...], ...] = ()
```

`ParamSpec` is the **per-operation** object, holding a tuple of per-parameter `Param`s.
`architecture.md` §3.3's prose says "A `ParamSpec` declares per-parameter: name, type, required,
allowed values, and nested shape", which describes `Param`; its own code sample in §3.1
(`params=CUSTOMER_BALANCE_TXN_CREATE, # a ParamSpec`) describes the collection. The code sample wins,
and the two names are split here so nothing has to be guessed.

`expand` defaults to `True` because 139 of the 148 operations accept `expand[]`, counted from
`spec3.json`. The nine exceptions are all `DELETE`s whose response is the `{deleted: true}` stub.
Note which `DELETE`s are *not* exceptions: `DELETE /v1/subscriptions/{subscription_exposed_id}` and
its customer-scoped alias do accept `expand`, because cancelling a subscription answers with the full
Subscription object rather than a stub. Defaulting the common case means a `ParamSpec` that forgets
`expand=True` still behaves, and the nine that must refuse it say `expand=False` out loud.

#### `ResourceSpec` — `dispatch/resource.py`, one per resource module

```python
Serializer = Callable[[seahaven.Ctx, Mapping[str, Any]], dict[str, Any]]
Normalizer = Callable[[seahaven.Ctx, "Request", dict[str, Any]], dict[str, Any]]

@dataclass(frozen=True, slots=True)
class ResourceSpec:
    object: str                            # "customer" — the API discriminator
    table: str                             # "customers"
    id_prefix: str                         # "cus"
    collection_url: str                    # "/v1/customers" — the list envelope's `url`
    serializer: Serializer
    columns: tuple[str, ...]               # the SELECT list; data_model.md owns it
    list_filters: tuple[ListFilter, ...] = ()
    creatable: ParamSpec | None = None
    updatable: ParamSpec | None = None
    delete: DeleteSpec | None = None
    metadata: bool = True
    sort: tuple[str, str] = ("created", "id")
    before_create: Normalizer | None = None
    before_update: Normalizer | None = None
    created_event: str | None = None       # "customer.created"
    updated_event: str | None = None
    deleted_event: str | None = None
```

The three `*_event` fields are three explicit strings rather than a mapping, because a `dict` default
on a frozen dataclass needs a `field(default_factory=...)` and the values are a closed set of three.
`cross_cutting.md`'s `emit_event` checks each against `spec/event_types.py` at call time.

`before_create` / `before_update` are the **one** escape the engine offers, and their contract is
narrow on purpose: given the validated `Request` and the column dict the engine is about to write,
return the column dict it should write instead. A normalizer may derive columns (a card number into
`brand`/`last4`/`exp_month`, a `price` id into a resolved `unit_amount`) and may `SELECT` to validate
a reference. It may **not** write another table, emit an event, or change the response. Anything that
needs to is a hand-written handler, and the review question for a pull request is exactly that
sentence. Five resources use one: `payment_methods` (magic-card interpretation),
`promotion_codes` (coupon resolution), `invoiceitems` (price resolution), `prices` (product
resolution), `subscription_items` (list-only, none) — and they stay generated because of it.

#### `ListFilter` and `DeleteSpec`

```python
@dataclass(frozen=True, slots=True)
class ListFilter:
    name: str                              # the query parameter: "email", "created", "status"
    column: str
    kind: Literal["exact", "range", "literal"]
    choices: tuple[str, ...] = ()
    id_prefixes: tuple[str, ...] = ()
    required: bool = False                 # `subscription` on GET /v1/subscription_items

@dataclass(frozen=True, slots=True)
class DeleteSpec:
    mode: Literal["soft", "hard"]
    requires_status: tuple[str, ...] = ()  # ("draft",) for invoices
    status_column: str = "status"
```

`mode` exists because Stripe has both. A deleted customer, coupon, product or invoiceitem stays
retrievable and answers `{"id": ..., "object": ..., "deleted": true}` forever — that is `soft`, and
`data_model.md` owns the `deleted_at` column it sets. A deleted draft invoice is gone and a later
retrieve is a 404 — that is `hard`. Without the distinction one of the two would have to be
hand-written for no reason other than the engine having picked a side.

`requires_status` is what keeps `DELETE /v1/invoices/{invoice}` generated: the only invoice-specific
thing about it is that a non-draft invoice must be refused with
`invalid_request_error` / `invoice_not_editable`, and that is a declaration, not a function.

#### `Request`, `Page`, `Result`, `Handler` — `dispatch/response.py`

```python
@dataclass(frozen=True, slots=True)
class Page:
    limit: int                             # already clamped to 1..100, default 10
    starting_after: str | None
    ending_before: str | None

@dataclass(frozen=True, slots=True)
class Request:
    method: Method
    path: str                              # the concrete path as called
    route: Route
    path_params: Mapping[str, str]         # {"charge": "ch_1"} — by the pattern's own names
    params: Mapping[str, Any]              # validated, coerced, allowlisted; the five removed
    expand: tuple[str, ...]
    metadata: MetadataUpdate | None
    page: Page | None
    idempotency_key: str | None

@dataclass(frozen=True, slots=True)
class Result:
    status: int
    body: dict[str, Any]

Handler = Callable[[seahaven.Ctx, Request], dict[str, Any] | Result]
```

`Request.params` never contains `expand`, `limit`, `starting_after`, `ending_before` or `metadata` —
they are lifted into their own fields (§3.4). A handler that reaches for `req.params["expand"]` has a
bug, and it will be a `KeyError`, which is the right kind of loud.

### 2.5 Errors this component raises

`StripeApiError` (`stripe_errors.py`, `architecture.md` §7) is the only agent-visible one, and it is
always caught before it leaves `dispatch`. The dispatcher itself raises exactly these:

| Condition | status | type | code | `param` |
|---|---|---|---|---|
| No pattern matches the path | 404 | `invalid_request_error` | — | — |
| Path matches, method does not | 405 | `invalid_request_error` | — | — |
| Unknown parameter | 400 | `invalid_request_error` | `parameter_unknown` | the key |
| Required parameter absent | 400 | `invalid_request_error` | `parameter_missing` | the name |
| Wrong type / not in `choices` / out of range | 400 | `invalid_request_error` | `parameter_invalid_*` | bracket path |
| Empty string where one is not allowed | 400 | `invalid_request_error` | `parameter_invalid_empty` | bracket path |
| Two mutually exclusive parameters together | 400 | `invalid_request_error` | — | the second one |
| Path id, scope parent, or looked-up row missing | 404 | `invalid_request_error` | `resource_missing` | the id's parameter |
| `expand` path bad or non-expandable | 400 | `invalid_request_error` | — | `expand` |

The three uncoded rows are uncoded because Stripe's `code` is optional and the ~215-value enumeration
in [`errors.md`](../research/stripe-billing-and-payments/cross-cutting-semantics/errors.md) has no
member for them. Inventing one would be inventing behavior.

**Two of these are not settled by research and are declared, not assumed.** Neither the 405 shape nor
the method-mismatch status appears in any source the research phase reached. We serve 405 with
`"Not allowed: <METHOD> <path>"` and 404 with
`"Unrecognized request URL (<METHOD>: <path>)."` — the latter is a real wire-quoted Stripe string
([`gap-closure-2026-09-18.md`](../research/stripe-billing-and-payments/cross-cutting-semantics/gap-closure-2026-09-18.md)
item 5), the former is our choice. Both go into
`tests/conformance/allowed_differences.py` as declared items, and a tenth conformance scenario is
added: *a known path with an unsupported verb*.

## 3. Internal Design Approach

### 3.1 The route table

#### 3.1.1 Shape

`routes.py` is 148 `Route(...)` literals grouped by resource in path order, and `ALL: Final[tuple[Route, ...]]`
at the bottom. It imports from `resources/` and `dispatch/params.py`; nothing imports from it except
`router.py`, `discovery/index.py` and the tests. It contains no logic — no loops building routes, no
comprehensions — because the one property that matters about it is that a human can read the 148
lines and see the scope of the project.

#### 3.1.2 Import-time checks

`Router.__init__` refuses, with `WorldBug` naming the offending `op_id`:

- a duplicate `(method, pattern)`;
- a pattern that does not start with `/v1/`, or has an empty or malformed segment;
- a `Route` with both `handler` and `action`, or neither;
- `action` set without `resource`, or `scope` set without `resource`;
- `ParamSpec.path` that is not exactly the pattern's placeholder names, in order;
- a `ParamSpec.body` entry named `expand`, `limit`, `starting_after`, `ending_before` or `metadata`
  (§3.4);
- an `alias_of` naming an `op_id` not in the table.

These run when `dispatch/router.py` is imported, which `__init__.py` does, so a malformed table is a
world that will not construct — the same failure mode Seahaven gives a malformed tool signature.

#### 3.1.3 Aliases

Eight of the 71 hand-written routes are legacy aliases that Stripe still serves and that the 148
therefore includes. They set `alias_of` and share their canonical route's `handler`; they do not get
a second implementation.

| Alias route | `alias_of` |
|---|---|
| `POST /v1/charges/{charge}/refund` | `PostRefunds` |
| `POST /v1/charges/{charge}/refunds` | `PostRefunds` |
| `POST /v1/customers/{customer}/subscriptions` | `PostSubscriptions` |
| `POST /v1/customers/{customer}/subscriptions/{subscription_exposed_id}` | `PostSubscriptionsSubscriptionExposedId` |
| `DELETE /v1/customers/{customer}/subscriptions/{subscription_exposed_id}` | `DeleteSubscriptionsSubscriptionExposedId` |
| `DELETE /v1/customers/{customer}/subscriptions/{subscription_exposed_id}/discount` | `DeleteSubscriptionsSubscriptionExposedIdDiscount` |
| `POST /v1/charges/{charge}/dispute` | `PostDisputesDispute` |
| `POST /v1/charges/{charge}/dispute/close` | `PostDisputesDisputeClose` |

An alias handler is the same function, so it reads its subject from `Request.path_params` by the name
the *pattern* used. `subscriptions.update` therefore looks for
`req.path_params.get("subscription_exposed_id") or req.path_params["subscription"]`; a one-line
helper `resources/_lookup.subject(req, *names)` does that lookup so eight handlers are not eight
places to get it wrong. An alias whose path carries an extra id — `{customer}` on the subscription
aliases, `{charge}` on the refund aliases — also asserts the child belongs to that parent and answers
`resource_missing` naming the child if it does not.

Functional spec §3.2 lists what is not routed and does *not* name the
`/v1/customers/{customer}/subscriptions*` family, so they are in; the research document's
recommendation to cut them was not adopted, and cutting them now would break the 148.

### 3.2 The trie

#### 3.2.1 Why a trie and not a regex list

148 patterns as an ordered regex list is 148 regex attempts per call in the worst case, and the
ordering *is* the precedence rule — which means the precedence rule lives in the order of a literal,
where a reordering edit silently changes behavior. A trie makes precedence a property of the
structure: at each node, exact children are tried before the placeholder child, always, and no entry
can be written in a way that changes that.

#### 3.2.2 Construction

```python
@dataclass(slots=True)
class _Node:
    exact: dict[str, _Node] = field(default_factory=dict)
    placeholder: _Node | None = None
    routes: dict[Method, Route] = field(default_factory=dict)
```

A pattern is split on `/` with the leading empty discarded. A segment matching `{...}` descends into
`placeholder`, creating it if absent; anything else descends into `exact[segment]`. At the last
segment, `routes[route.method] = route`.

**A placeholder node stores no name.** This is the one non-obvious decision in the trie and the table
forces it: two nodes in the routed 148 have *two differently-named* placeholder children.

| Node | Placeholder names |
|---|---|
| `/v1/credit_notes` | `{credit_note}`, `{id}` |
| `/v1/subscriptions` | `{subscription_exposed_id}`, `{subscription}` |

Stripe names the same positional id differently on different operations of the same resource — a
genuine wart in `spec3.json`, preserved because `op_id`s and discovery text quote it. If the trie
keyed placeholders by name, each of those nodes would sprout two siblings and matching would have to
pick between them arbitrarily. Instead there is at most one placeholder child per node, the captured
segments are collected positionally into `Match.path_values`, and `dispatch` zips them against the
*route's own* `ParamSpec.path` to make `Request.path_params`. The name is a property of the route,
which is where it belongs, and `/v1/credit_notes/{credit_note}/lines` and
`/v1/credit_notes/{id}/void` correctly share one node with children `lines` and `void`.

#### 3.2.3 Matching

Depth-first, exact before placeholder, with backtracking, and matching on `(method, path)` jointly:

```
match(method, path):
    segments = path.strip("/").split("/")
    method_mismatch_seen = False

    walk(node, i):                       # recursive; depth ≤ 6, so recursion is fine
        if i == len(segments):
            if method in node.routes: return node.routes[method]
            if node.routes:            method_mismatch_seen = True
            return None
        seg = segments[i]
        if seg in node.exact:                       # exact first, always
            hit = walk(node.exact[seg], i + 1)
            if hit is not None: return hit
        if node.placeholder is not None and seg != "":
            hit = walk(node.placeholder, i + 1)
            if hit is not None: return hit
        return None

    hit = walk(root, 0)
    if hit is not None:            return Match(hit, captured)
    if method_mismatch_seen:       raise StripeApiError(405, ...)
    raise StripeApiError(404, ...)
```

Three things that pseudocode settles:

**Precedence.** `/v1/credit_notes/preview` beats `/v1/credit_notes/{id}`, and
`/v1/invoices/create_preview` beats `/v1/invoices/{invoice}`, because `node.exact` is consulted
first. Those two are the *actual* collisions in the routed table — `architecture.md` §3.2 motivates
the rule with `/v1/customers/search` vs `/v1/customers/{customer}`, which is a fine illustration but
is not one of the 148, since search is cut (functional spec §3.3). When search lands it becomes a
third. The rule is right; the example is hypothetical, and a test should assert the real pair.

**Backtracking.** `GET /v1/credit_notes/preview/lines` matches exact-exact. But
`POST /v1/credit_notes/preview/void` takes the exact branch to the `preview` node, finds no `void`
child, and dead-ends. Without backtracking that is a 404 for a URL that is a perfectly legal
"void the credit note whose id is the string `preview`" — which the real API answers with
`resource_missing`, "No such credit note: preview". Backtracking returns to `/v1/credit_notes` and
takes the placeholder branch, and the call becomes the 404 it should be, from the row lookup, with
the message that tells the agent what is wrong. The cost is bounded: depth 6, at most two branches
per node, no route longer than six segments.

**Method mismatch versus no match.** The walk succeeds only at a terminal carrying the requested
method; a terminal that matches the path but not the method sets `method_mismatch_seen` and the
search *continues*. So `POST /v1/credit_notes/preview` does not 405 on the `preview` terminal — it
backtracks to the placeholder and matches `PostCreditNotesId`, which is what an agent updating a
credit note called `preview` deserves. Only when nothing anywhere carries the method does the 405
surface. Architecture §3.2 asks for "a path that matches with a different method is Stripe's method
error, not a 404", and this is the precise version of that: *no route for the method anywhere along
any matching path*.

An empty segment (`//`, or a trailing slash) never matches a placeholder, so `/v1/customers/` is a
404 rather than a retrieve of the customer with the empty id. A query string is not our problem:
parameters arrive as JSON (functional spec §2.2) and a `?` in `path` is just a character that matches
nothing.

### 3.3 Parameter validation

`dispatch/params.py`. One function:

```python
def bind(
    ctx: seahaven.Ctx,
    route: Route,
    path_values: Sequence[str],
    raw: Mapping[str, Any],
) -> Request:
    """Validate `raw` against `route.params` and lift the five central parameters out of it.

    Raises StripeApiError(400, ...) for anything wrong, with `param` in Stripe's bracket
    notation. Reads no tables and writes nothing: it runs before the call's savepoint opens."""
```

Order, and it matters:

1. **Path.** `zip(route.params.path, path_values)` → `path_params`. Lengths agree by construction
   (§3.1.2). If a `Param` for that name declares `id_prefixes`, the value is checked against them
   here, so `/v1/customers/ch_123` is `resource_missing` with `param="customer"` before any query
   runs.
2. **Lift the five.** `expand`, `limit`, `starting_after`, `ending_before`, `metadata` are removed
   from `raw` and validated by the central rules in §3.4. A lifted parameter the operation does not
   accept — `limit` on a retrieve — is `parameter_unknown`, the same as any other.
3. **Allowlist.** Every remaining key must appear in `route.params.body` (for a list, the body is
   generated from `ResourceSpec.list_filters`, §3.5.1). A key that does not:
   `parameter_unknown`, 400, `param` = the key, message `"Received unknown parameter: <key>"`.
   This is the whole reason `ParamSpec` exists rather than reading `spec3.json` at runtime: Stripe
   rejects unknown parameters, which needs a closed set, and the spec's request bodies are far wider
   than what this world implements — `POST /v1/customers` alone declares 23 properties of which we
   accept a fraction.
4. **Required.** `parameter_missing`, `"Missing required param: <name>."`
5. **Coerce and check, depth-first.** One recursive function per `Kind`, threading a path prefix so
   the `param` on a failure is Stripe's bracket notation:
   `items[0][price]`, `invoice_settings[default_payment_method]`, `created[gte]`. The rule, from
   [`gap-closure-2026-09-18.md`](../research/stripe-billing-and-payments/cross-cutting-semantics/gap-closure-2026-09-18.md)
   item 6: bare name for a whole-parameter error, `parent[child]` for a sub-field,
   `parent[N][child]` when the failing array element is known. That maps exactly onto a recursion
   that appends `[name]` on an object and `[i]` on an array.
6. **`required_one_of` and `mutually_exclusive`.** Declarations, not code:
   `mutually_exclusive=(("starting_after", "ending_before"),)` is implicit on every paginated spec,
   and `required_one_of=(("charge", "payment_intent"),)` is `POST /v1/refunds`.

Strictness matches Seahaven's own: `"5"` is not an integer, `2.0` is not an integer, `1` is not a
boolean. The world is entered through a JSON tool call, not a form post, so there is no reason to be
lenient and every reason for a mistyped amount to be a clear 400 rather than a silent cast. **No
form encoding exists anywhere in this project** (functional spec §2.2), so bracket notation appears
only in `param` on the way out, never on the way in.

`bind` is pure. It touches no tables, so the only work discarded when it raises is its own — which is
why it runs before §3.8's savepoint opens.

### 3.4 The five central parameters

Declared once in `dispatch/params.py` and never repeated in a `ParamSpec`; §3.1.2's import check
enforces that by refusing a `body` entry with one of the five names.

```python
EXPAND: Final = Param(name="expand", kind="array", item=Param(name="", kind="string", max_length=5000))
LIMIT: Final = Param(name="limit", kind="integer", minimum=1, maximum=100)
STARTING_AFTER: Final = Param(name="starting_after", kind="string", max_length=5000)
ENDING_BEFORE: Final = Param(name="ending_before", kind="string", max_length=5000)
METADATA: Final = Param(name="metadata", kind="object", unset_with_empty_string=True)
```

**`expand`** is accepted when `ParamSpec.expand` is true, which is 144 of the 148 operations —
retrieve, list, create *and* update, exactly as functional spec §6.3 requires. It is validated here
only as "an array of non-empty strings"; whether each path is expandable is
`cross_cutting.md`'s resolver, called from §3.7 *after* the body exists, because
`"This property cannot be expanded (<field>)."` needs to know the object's type. The nine operations
that set `expand=False` are listed in §2.4; `GET /v1/balance` is not among them — it accepts
`expand` like any other read.

**`limit` / `starting_after` / `ending_before`** are accepted when `ParamSpec.paginated` is true,
which is exactly the 29 operations `spec3.json` declares all three on: the 25 table-backed lists, all
of them engine-served, and the four embedded-line lists that §5 explains. `limit` defaults to 10 and is bounded 1–100 by the `Param`
itself; the two cursors are mutually exclusive and supplying both is a 400 naming `ending_before`.
They become `Request.page`, and `Request.params` never sees them.

**`metadata`** is accepted when `ParamSpec.metadata` is true — every create and update of a resource
whose `ResourceSpec.metadata` is true — and is lifted into `Request.metadata` as a small value object
so that the engine and a hand-written handler apply Stripe's semantics identically:

```python
@dataclass(frozen=True, slots=True)
class MetadataUpdate:
    clear: bool                                  # the whole `metadata: ""` (or {}) sentinel
    set: Mapping[str, str]                       # keys to write
    unset: frozenset[str]                        # keys whose value was "" or null

    def apply(self, current: Mapping[str, str]) -> dict[str, str]: ...
```

The semantics, from
[`metadata.md`](../research/stripe-billing-and-payments/cross-cutting-semantics/metadata.md):
an update is a **merge**, not a replace; a key sent with `""` is deleted; the whole parameter sent as
`""` clears every key. We accept `{}` as a synonym for the `""` sentinel, because in a JSON surface
with no form encoding `{}` is the natural spelling of "no keys" and `""` is the wire-compatibility
spelling; both mean clear. Limits are 50 keys, 40-character keys, 500-character values, checked
centrally, each violation a 400 with `param="metadata[<key>]"`. The exact message text for a limit
violation is not in any source the research reached; it goes in the conformance allow-list.

One consequence worth stating: because `metadata` is lifted, **no `ResourceSpec.creatable` or
`updatable` ever lists it**, and a resource that does not carry metadata sets
`ResourceSpec.metadata = False` and gets `parameter_unknown` for free.

### 3.5 The CRUD engine

`dispatch/resource.py`. Five functions, one per `Action`, each `(ctx, req) -> dict | Result`. The
engine never imports a resource module; it works entirely from `req.route.resource`.

The rule that decides what it serves, stated once so a reviewer can apply it: **an operation is
generated when its whole behavior is validate → touch one row, or one page of rows, of one table →
serialize.** An operation that moves money, drives a status machine, writes a second table, or
computes rather than reads is hand-written. §1.3's 77/71 is that rule applied to all 148, and
`test_router.py` asserts the two counts so the split cannot drift.

#### 3.5.1 `list`

```python
def list_(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
```

1. If `req.route.scope`, look the parent up through `resources/_lookup.py` → `resource_missing` on
   miss, and add `scope.column = ?` to the `WHERE`.
2. Translate each present `ListFilter` into a condition. `kind="exact"` is `col = ?`;
   `kind="literal"` is the same after checking `choices`; `kind="range"` is the `created`-shaped
   `{gt, gte, lt, lte}` object or a bare integer, which `spec3.json` models as
   `anyOf: [range_query_specs, integer]` and which becomes one to four comparisons against the
   TEXT timestamp column, converted by `_time.py` (§4.2 of the architecture: Unix seconds outside,
   canonical TEXT inside — the conversion is on the *parameter*, so the SQL stays a text comparison).
3. Page it. Ordering is `(created DESC, id DESC)`: under a frozen clock `created` alone is not
   unique, and without the id tiebreak a cursor page would be unstable
   ([`architecture.md`](../architecture.md) §6.2).
4. Serialize each row and wrap:
   `{"object": "list", "data": [...], "has_more": bool, "url": resource.collection_url}` — with
   `url` being the *scoped* path for a nested list, so
   `GET /v1/charges/ch_1/refunds` answers `"url": "/v1/charges/ch_1/refunds"`, which is what the real
   API does.

**No operation has a hand-written list over a table.** `architecture.md` §3.2 states that as an
absolute, and over tables it holds — all 25 table-backed list operations are generated. But it is not
true as written, and §5 says why: four routed `GET`s page over JSON nested on a parent row, not over
a table.

#### 3.5.2 `retrieve`

Parent lookup if scoped, `SELECT` by primary key (plus `scope.column = ?`), `resource_missing` on
miss with the path parameter's own name in `param`, serialize. For a `soft`-deleted row the engine
returns the `{"id", "object", "deleted": true}` stub rather than the full object, because that is
what Stripe answers for a deleted customer and it is what the schema conformance validator will
expect.

#### 3.5.3 `create`

`ParamSpec` has already allowlisted the body. The engine mints the id with
`_ids.stripe_id(ctx, spec.id_prefix)`, stamps `created` from `ctx.clock.iso()`, maps each `Param` to
its `column` (defaulting to the same name), applies `before_create` if declared, `INSERT`s, emits
`created_event` if declared, and returns the retrieve of what it just wrote. Reading back rather than
echoing the dict means the response is exactly what a later retrieve will answer, including whatever
defaults the schema applied.

#### 3.5.4 `update`

Retrieve first — so a missing row is `resource_missing` and not a zero-rowcount `UPDATE` that looks
like success. Build a `SET` clause from the present parameters only; absent means unchanged, which is
Stripe's semantics and is why the engine cannot work from a full column list. `metadata` is applied
through `MetadataUpdate.apply` over the current value. `before_update` if declared, `UPDATE`,
`updated_event` if declared, return the re-read row.

#### 3.5.5 `delete`

Retrieve first. If `DeleteSpec.requires_status` and the row's status is not in it, raise the declared
refusal. `soft` sets `deleted_at = ctx.clock.iso()`; `hard` issues `DELETE FROM`. Both answer
`{"id": ..., "object": ..., "deleted": true}` with status 200 — Stripe's delete response carries no
other field, which is why those routes set `ParamSpec.expand = False`.

Five of the eleven `DELETE` routes are engine-served this way: `coupons`, `customers`, `invoiceitems`,
`invoices` and `products`. The other six are hand-written — the two subscription cancels and their
two customer-scoped aliases drive a status machine and answer with a full Subscription, the two
`discount` deletes remove an embedded object rather than a row, and `DELETE /v1/subscription_items/{item}`
prorates.

### 3.6 Invoking a hand-written handler

One signature, no exceptions:

```python
def finalize(ctx: seahaven.Ctx, req: Request) -> dict[str, Any] | Result:
```

A handler receives `ctx` and a `Request` whose `params` are already allowlisted, coerced and
range-checked, whose `path_params` are named by the pattern, and whose `expand` / `page` / `metadata`
have been lifted out. It never parses a path, never checks for an unknown parameter, and never reads
`expand`.

It returns one of two things:

- **a `dict`** — the API object, already through its resource's serializer. `dispatch` answers
  `{"status": 200, "body": <that dict>}`. Stripe v1 returns 200 on create as well as on read, so
  there is no 201 anywhere and `Route` needs no success-status field.
- **a `Result(status, body)`** — for the one case a dict cannot express: an outcome that is both a
  real state change *and* a non-2xx response. A charge against `4000 0000 0000 0002` writes a
  `charge` row with `status: "failed"`, writes its `outcome`, emits `charge.failed`, and answers
  **402** with a `card_error` envelope. Those writes must commit. `Result` is how a handler says
  "this failed, and the failure is state".

That is the whole reason the two exist. A handler that raises `StripeApiError` is saying *nothing
happened*; a handler that returns `Result` is saying *this happened and it was a failure*. §3.8 is
where that distinction becomes a savepoint.

A handler may call the engine — `resource.retrieve(ctx, req)` after doing its work is the normal way
to produce a response — and may call `resource.page(...)` or `resource.page_embedded(...)` so that
no handler ever reimplements pagination.

### 3.7 Building the response

`dispatch/response.py`:

```python
def build(ctx: seahaven.Ctx, req: Request, result: dict[str, Any] | Result) -> dict[str, Any]:
    """{"status": int, "body": {...}} — the only shape any tool returns."""
```

1. Normalise `result` to `(status, body)`; a `dict` is `(200, dict)`.
2. If `req.expand` and `200 <= status < 300`, hand `body` and `req.expand` to
   `cross_cutting.md`'s resolver. It reads `body["object"]` to enter the expandable map, applies the
   `data.` prefix rule when that is `"list"`, enforces the depth limit, and raises
   `StripeApiError(400, …, "This property cannot be expanded (<field>).")` — or
   `"…because it doesn't exist: <field>."` — for a bad path. Expansion is skipped on an error body
   because there is nothing there to expand.
3. Attach `request.idempotency_key` to the body when one was supplied, per functional spec §6.1.
4. Return `{"status": status, "body": body}`.

Everything crossing the tool boundary is a `dict`, `list`, `str`, `int`, `float`, `bool` or `None`.
`bytes` and `set` are refused by Seahaven, and its `_proved()` check serialises the result *inside*
the call's transaction — so a return value we cannot render rolls the call back rather than
committing a write whose answer never arrived. That is free, and it is one more reason the `Result`
body is built from plain types.

### 3.8 Errors, the transaction, and what rolls back

#### 3.8.1 Where the boundary is

`dispatch` is the boundary. It is the only place that catches `StripeApiError`, and it must catch it
*before* the function returns to Seahaven, because Seahaven's per-call transaction commits on a
normal return.

```python
def dispatch(ctx, method, path, params, *, idempotency_key):
    try:
        match = ROUTER.match(method, path)                     # no writes yet
        req = params_mod.bind(ctx, match.route, match.path_values, params or {},
                              idempotency_key=idempotency_key) # no writes yet
        with ctx.db.transaction():                             # SAVEPOINT
            result = _invoke(ctx, req)
    except StripeApiError as error:
        return error.envelope()                                # savepoint already rolled back
    return response.build(ctx, req, result)


def _invoke(ctx, req):
    route = req.route
    if route.handler is not None:
        return route.handler(ctx, req)
    return _ENGINE[route.action](ctx, req)
```

`_ENGINE` is a module-level `dict[Action, Callable]` built once.

#### 3.8.2 Why the savepoint is explicit

Seahaven wraps a tool call in a transaction and commits it when the tool function returns normally
(`vendor/Seahaven/src/seahaven/call.py`, `invoke`). We return normally on a Stripe error — that is
the whole design, errors are return values — so **the call's own transaction cannot be what rolls a
failed call back.** It would commit.

`ctx.db.transaction()` inside the tool is documented as nesting as a savepoint, and its context
manager rolls back on any exception and releases on a clean exit
(`db.py:217-239`). So:

- **A raised `StripeApiError` rolls the call back.** The exception leaves the `with`, the savepoint
  rolls back, `dispatch` catches it outside the `with`, and returns the envelope with nothing
  written. A handler that half-built an invoice and then discovered the customer had no payment
  method leaves no trace, and `inst.change_log()` correctly shows nothing.
- **A returned `Result` does not roll back.** The `with` exits cleanly, the savepoint releases, and
  the 402 card decline keeps its `charge` row, its `balance_transaction`, and its `charge.failed`
  event. That is the difference the prompt asks about, and it is the difference between a world where
  a decline is invisible and one where an eval can grade it.

A handler that needs to write *some* state and then fail — record the attempt, then refuse — returns
`Result`. A handler that must record state and *also* unwind part of its own work opens its own
nested `ctx.db.transaction()` around the part it wants undone. Both are available; neither is the
default.

`StripeApiError` must never escape `dispatch`. Seahaven's `invoke` logs a traceback for any
non-`ToolError` exception before re-raising, so a leaked `StripeApiError` would fill the log with
tracebacks for ordinary card declines. `test_dispatcher.py::test_stripe_error_never_escapes` asserts
that by calling every route with deliberately bad parameters and checking the tool returned rather
than raised.

#### 3.8.3 The middleware boundary, and one problem with it

Middleware wraps `invoke`, and the per-call transaction is *inside* `invoke`. A middleware that
writes to the database therefore writes outside the call's transaction, in autocommit. For the
idempotency middleware (`architecture.md` §6.1) that means the stored response row and the state
change it describes are not committed atomically.

In this world that is survivable — one connection, one writer, synchronous, no concurrency — but it
is a real property and it belongs written down rather than discovered.
[`cross_cutting.md`](cross_cutting.md) owns the fix; the two shapes available are for the middleware
to open its own `ctx.db.transaction()` around `next_(ctx, call)` so the store and the call commit
together, or to move idempotency inside `dispatch`. The first keeps the short-circuit property the
architecture depends on — a replay never reaches the tool, so it writes nothing and leaves no
change-log records — and is the one this document assumes.

Two further notes on the seam, both in our favour: middleware does not run for `UnknownTool`, a tool
listing or startup hooks, so nothing in §3 has to be re-entrant against those; and
`error_handler.py` is registered outermost, so a `WorldBug` from §3.1.2 reaches the agent unchanged,
which is right — a malformed route table is our bug, not the agent's.

### 3.9 State

The dispatcher holds none. `ROUTER` and the `ParamSpec` / `ResourceSpec` constants are frozen,
module-level and built at import; everything per-call lives on the `Request`, which is frozen too.
Nothing is memoised against `ctx`, nothing is written to `ctx.state`, and no counter survives a call.
An instance's whole mutable state is its SQLite file, which is what makes `inst.change_log()` the
complete account of what a call did.

## 4. Dependencies

### 4.1 What the dispatcher depends on

| Depends on | For |
|---|---|
| `seahaven` | `Ctx`, `world.tool`, `DbError`, `WorldBug`; `ctx.db`, `ctx.clock`, `ctx.ids` |
| `stripe_errors.py` | `StripeApiError` and `.envelope()` |
| `errors.py` | the `ToolError` subclasses the error handler maps to |
| `resources/*` | each module's `ResourceSpec`, its serializer, and its hand-written handlers |
| `resources/_lookup.py` | `require_*` parent lookups, so a missing parent is a 404 not a `DbError` |
| `serialize/` | row → API object, called by the engine and by every handler |
| `cross_cutting.md`'s expansion resolver | step 2 of `response.build` |
| `cross_cutting.md`'s `emit_event` | the three `ResourceSpec` event fields |
| `_ids.py`, `_time.py`, `_json.py` | id minting, Unix-second conversion, canonical JSON |
| `spec/enums.py` | the `choices` tuples, so a `ParamSpec` and the schema's `CHECK` cannot disagree |

Import direction is one-way: `dispatch/` imports `resources/`, never the reverse. `routes.py` is the
only module that imports every resource, which is what makes it the single place the scope is
visible.

### 4.2 What depends on the dispatcher

| Depends on it | How |
|---|---|
| `tools/api.py` | the four tools are four lines over `dispatch` |
| `discovery/index.py` | filters `spec3.json` by `routes.ALL`; `stripe_api_details` resolves a concrete path with `Router.resolve` |
| `tools_dev/prune_spec.py` | prunes `spec3.min.json` to the routed `op_id`s |
| `tests/conformance/` | enumerates `routes.ALL` for the schema-conformance sweep |
| every `resources/` module | declares a `ResourceSpec` and writes handlers to the `Handler` signature |
| `middleware/idempotency.py` | hashes `(path, method, canonical params)` from the raw call |
| `fixtures_src/generate.py` | the through-the-tools tail of each fixture |

## 5. Problems with the architecture as written

Four, stated here rather than designed around silently.

**1. The 95/53 split is wrong; it is 77/71.** §1.3 has the count and the reason. The consequence is
for [`implementation_plan.md`](../implementation_plan.md), which should budget 63 distinct
hand-written functions, 14 of them in `invoices` alone.

**2. "No operation gets a hand-written list endpoint" is not achievable as an absolute.** Four routed
`GET`s page over JSON nested on a parent row rather than over a table:
`GET /v1/invoices/{invoice}/lines`, `GET /v1/credit_notes/{credit_note}/lines`,
`GET /v1/credit_notes/preview/lines`, and
`GET /v1/payment_intents/{intent}/amount_details_line_items`. `line_item` and
`credit_note_line_item` are deliberately not tables (functional spec §3.4), so a table-backed engine
cannot serve them — and the two `preview` ones have no stored parent at all.

The rule's *intent* survives, and the design honours it: `dispatch/resource.py` exposes a second
entry point,

```python
def page_embedded(items: Sequence[dict[str, Any]], page: Page, *, url: str) -> dict[str, Any]:
    """The same cursor semantics over an in-memory list: limit, starting_after / ending_before
    on each item's `id`, has_more, and the list envelope."""
```

so those four handlers declare their source and reuse the paginator rather than reimplementing it.
There is still exactly one implementation of `has_more` and the envelope per storage shape, and two
shapes is the honest number. The architecture sentence should read "no operation reimplements
pagination".

**3. `ParamSpec` is described two ways in `architecture.md` §3.** The prose describes a
per-*parameter* object; the code sample in §3.1 uses it as a per-*operation* one. §2.4 splits them
into `Param` and `ParamSpec` and keeps the code sample's meaning, since `routes.py` is written
against it.

**4. Pagination is assigned to two components.** §6.2 puts the implementation in
`dispatch/resource.py` (ours) and §13 puts the design in `components/cross_cutting.md`. That is
workable and this document assumes it — we specify the call site and the two entry points,
`cross_cutting.md` specifies `has_more`, the envelope, the `limit` bounds and the open
deleted-cursor question — but the reviewer of `cross_cutting.md` should know the code does not live
in that component's files.

Two smaller notes, not problems: §3.2's motivating precedence example (`/v1/customers/search`) is not
one of the 148, since search is cut — the real collisions are `credit_notes/preview` and
`invoices/create_preview`; and the "39 legacy, sub-resource and `search` operations" enumeration in
`minimum-closed-set-and-tool-budget.md` says "all four `search` endpoints" where there are seven,
though the total of 39 is right.

## 6. Test Plan

Seahaven's pytest plugin throughout, `@pytest.mark.seahaven(fixture=...)` per module, every tool
exercised through `instance.call(...)` rather than the bare function so validation, middleware and
the transaction are all in play. Errors asserted by `code` and `status`, never by message text. State
asserted through `inst.inspect()` and `inst.change_log()`.

### `tests/test_routes.py` — the table itself, no instance needed

| Test | Verifies |
|---|---|
| `test_route_count_is_148` | `len(routes.ALL) == 148`. The scope-drift tripwire; becomes 155 when search lands |
| `test_generated_count_is_77` | exactly 77 routes have `handler is None` |
| `test_hand_written_count_is_71` | exactly 71 have a handler, and they resolve to 63 distinct functions |
| `test_every_op_id_is_in_spec3` | every `Route.op_id` exists in `spec3.min.json`, with the same method and path |
| `test_no_duplicate_method_pattern` | no `(method, pattern)` appears twice |
| `test_route_shape_is_exclusive` | every route has `handler` xor `(resource, action)` |
| `test_param_spec_path_matches_pattern` | `ParamSpec.path` equals the pattern's placeholders, in order |
| `test_central_params_never_redeclared` | no `ParamSpec.body` names `expand`, `limit`, `starting_after`, `ending_before` or `metadata` |
| `test_alias_targets_exist` | every `alias_of` names a real `op_id`, and the alias shares its handler |
| `test_cut_paths_are_absent` | none of the 39 cut operations is routed — spot-checks `sources`, `cash_balance`, `tax_ids`, `features`, `balance/history` and all seven `search` paths |

### `tests/test_router.py` — the trie

| Test | Verifies |
|---|---|
| `test_exact_beats_placeholder_credit_notes_preview` | `GET /v1/credit_notes/preview` → `GetCreditNotesPreview`, not `GetCreditNotesId` |
| `test_exact_beats_placeholder_invoices_create_preview` | `POST /v1/invoices/create_preview` → `PostInvoicesCreatePreview`, not `PostInvoicesInvoice` |
| `test_placeholder_matches_a_real_id` | `GET /v1/credit_notes/cn_123` → `GetCreditNotesId`, capturing `cn_123` |
| `test_backtracks_out_of_a_literal_dead_end` | `POST /v1/credit_notes/preview/void` → `PostCreditNotesIdVoid` with `{id: "preview"}`, not a 404 |
| `test_two_placeholder_names_share_one_node` | `/v1/subscriptions/{subscription_exposed_id}/discount` and `/v1/subscriptions/{subscription}/resume` both match, each binding its own name |
| `test_credit_note_placeholder_names_share_one_node` | same for `{credit_note}/lines` and `{id}/void` |
| `test_unknown_path_is_404` | `GET /v1/widgets` → 404, `invalid_request_error`, no `code` |
| `test_method_mismatch_is_405` | `DELETE /v1/charges/ch_1` → 405, not 404 |
| `test_method_mismatch_prefers_a_placeholder_route` | `POST /v1/credit_notes/preview` → `PostCreditNotesId` (200-path), not a 405 on the literal terminal |
| `test_trailing_slash_is_404` | `GET /v1/customers/` does not retrieve the empty-id customer |
| `test_empty_segment_is_404` | `GET /v1/customers//balance_transactions` is a 404 |
| `test_every_pattern_round_trips` | for all 148, substituting a plausible id for each placeholder matches back to that same route |
| `test_match_is_linear_in_depth` | a 6-segment path visits at most 12 nodes (a guard against a regression to a scan) |

### `tests/test_params.py` — `ParamSpec`

| Test | Verifies |
|---|---|
| `test_unknown_parameter_is_rejected` | `POST /v1/customers` with `{"nope": 1}` → 400, `parameter_unknown`, `param="nope"` |
| `test_missing_required_parameter` | `POST /v1/prices` without `currency` → 400, `parameter_missing` |
| `test_nested_param_uses_bracket_notation` | a bad `invoice_settings.default_payment_method` → `param="invoice_settings[default_payment_method]"` |
| `test_array_item_param_is_indexed` | a bad `items[1].price` → `param="items[1][price]"` |
| `test_strict_types` | `{"amount": "500"}` and `{"amount": 5.0}` are both 400, not coerced |
| `test_literal_choice_rejected` | an off-list `collection_method` → 400 naming the parameter |
| `test_id_prefix_checked_before_query` | `GET /v1/customers/ch_123` → 404 `resource_missing`, `param="customer"`, with no row read |
| `test_mutually_exclusive_cursors` | `starting_after` and `ending_before` together → 400 |
| `test_limit_bounds` | `limit=0` and `limit=101` are 400; `limit` absent yields 10 |
| `test_expand_refused_where_unsupported` | `expand` on `DELETE /v1/customers/{customer}` → `parameter_unknown` |
| `test_pagination_params_refused_on_retrieve` | `limit` on `GET /v1/customers/{customer}` → `parameter_unknown` |
| `test_metadata_merge` | update with one key leaves the others |
| `test_metadata_key_unset` | a key sent as `""` is removed |
| `test_metadata_clear_all_both_spellings` | `""` and `{}` both clear every key |
| `test_metadata_limits` | 51 keys, a 41-character key and a 501-character value are each 400 with `param="metadata[…]"` |
| `test_metadata_refused_where_unsupported` | `metadata` on a resource with `metadata=False` → `parameter_unknown` |
| `test_range_filter_accepts_object_and_integer` | `created={"gte": …}` and `created=1750000000` both filter |

### `tests/test_resource_engine.py` — generated CRUD

| Test | Verifies |
|---|---|
| `test_crud_round_trip_per_resource` | parameterised over every `ResourceSpec`: create → retrieve → update → list → delete, each answering 200 and the right `object` |
| `test_created_id_has_the_right_prefix` | every generated create mints `<prefix>_…` |
| `test_soft_delete_is_still_retrievable` | a deleted customer retrieves as `{"deleted": true}` |
| `test_hard_delete_is_gone` | a deleted draft invoice retrieves as 404 `resource_missing` |
| `test_delete_requires_status` | deleting an open invoice → 400, and the row is unchanged |
| `test_update_absent_means_unchanged` | an update naming one field leaves the others |
| `test_scoped_list_filters_by_parent` | `GET /v1/charges/{charge}/refunds` shows only that charge's refunds |
| `test_scoped_list_bad_parent_is_404` | a missing `{charge}` is `resource_missing`, not an empty page |
| `test_scoped_list_url_is_the_scoped_path` | the envelope's `url` carries the concrete parent id |
| `test_list_is_reverse_chronological` | newest first |
| `test_cursor_is_stable_under_a_frozen_clock` | 30 rows sharing one `created` paginate without repeat or omission, which is what the id tiebreak buys |
| `test_before_create_hook_runs` | a magic card number becomes `brand`/`last4` on the stored payment method |
| `test_generated_create_emits_its_event` | `ResourceSpec.created_event` lands one `events` row |

### `tests/test_dispatcher.py` — invocation, response, transaction

| Test | Verifies |
|---|---|
| `test_return_shape_is_status_and_body` | every one of the 148, called once, answers a two-key dict |
| `test_stripe_error_never_escapes` | no route raises `StripeApiError` out of a tool call |
| `test_raised_error_rolls_back` | a handler that writes then raises leaves **zero** `change_log` records |
| `test_returned_failure_commits` | a 402 decline leaves the `charge`, its `balance_transaction` and its `charge.failed` event in the change log |
| `test_handler_dict_is_status_200` | a handler returning a dict answers 200 |
| `test_expand_applied_to_success_only` | `expand` on an error body is ignored, not an error |
| `test_bad_expand_path_is_400` | `expand=["nope"]` → 400 `invalid_request_error` |
| `test_idempotency_key_echoed` | `request.idempotency_key` appears on the body |
| `test_read_tool_refuses_a_write_verb` | `stripe_api_write(method="GET", …)` is a Seahaven `ArgumentError` → the world's `INVALID_INPUT`, not a Stripe envelope |
| `test_read_tool_cannot_reach_a_post_route` | `stripe_api_read` on a `POST`-only path is a 405 |
| `test_call_stripe_reaches_every_route` | the unregistered escape hatch dispatches all three verbs |
| `test_no_tool_returns_bytes_or_set` | every route's body survives `json.dumps` |

### `tests/test_dispatch_errors.py` — the envelope

| Test | Verifies |
|---|---|
| `test_error_type_is_one_of_four` | across every error this component can raise, `type` ∈ the four wire values |
| `test_404_body_shape` | `{"error": {"type": "invalid_request_error", "message": …}}` with no invented `code` |
| `test_405_body_shape` | same, status 405 — and the test cites the allow-list entry, since this is our choice, not Stripe's documented behavior |
| `test_param_is_present_on_every_parameter_error` | every `parameter_*` error names a `param` |

### Where these tests sit relative to the architecture's table

`architecture.md` §11 assigns the router "pattern precedence, 404 vs method error, the 148-count
assertion" and the resource engine "CRUD per resource, pagination boundaries, list filters". Those
are `test_router.py`, `test_routes.py` and `test_resource_engine.py` above. `test_params.py` and
`test_dispatcher.py` are additions this design needs: the parameter allowlist and the
raise-versus-return transaction distinction are the two places where a wrong decision is invisible in
a response body and visible only in the change log, which is the property this whole world exists to
make gradable.
