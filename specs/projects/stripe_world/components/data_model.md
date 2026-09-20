---
status: complete
---

# Component: Data Model

## Purpose and Scope

This component is the storage layer and the boundary that turns rows into Stripe objects. It owns:

- the four DDL files under `src/stripeapi/schema/` (`001_core.sql`, `002_payments.sql`,
  `003_billing.sql`, `004_infra.sql`) — every table, column, type, nullability, primary key, foreign
  key, `CHECK` and index;
- `_ids.py` — the id prefix per resource and the one minting function;
- `_json.py` — the single JSON dump convention for every JSON `TEXT` column;
- `_time.py` — the one ISO↔Unix-second conversion;
- `serialize/fields.py` — the per-resource field map (column → API field), the `object` discriminator,
  `livemode`, constant-valued fields, nested inflation, and the null-versus-absent rule.

**Not in scope here:** the expansion resolver and its depth limit, the list envelope and cursor
arithmetic, the idempotency middleware's control flow, error construction, and every state machine.
Those are `components/cross_cutting.md` and `components/billing_engine.md`. This document says what
the columns are and what one row serialises to; it does not say when a row changes.

`005_search.sql` (FTS5) is out of scope for this document — it lands in the final gated phase and its
external-content tables are declared there.

### Three corrections to the architecture, up front

1. **The table count is 24, not 21.** Architecture §4.1 says "19 tables plus two infrastructure
   tables" and then enumerates 6 + 8 + 7 = **21** Stripe tables in `001`–`003`, plus `events` and
   `idempotency_keys` in `004`. The "19" is an arithmetic slip inherited from
   [`minimum-closed-set-and-tool-budget.md`](../research/stripe-billing-and-payments/api-surface-and-object-graph/minimum-closed-set-and-tool-budget.md)
   line 102, which lists 21 resource names and then subtracts the four collapsed objects
   (`balance`, `line_item`, `credit_note_line_item`, `discount`) a second time — they were already
   absent from the list. The enumerated file contents are right; the total is wrong. **This design
   builds 24 tables** — those 21, plus `events`, `idempotency_keys` and one `counters` table that
   §3.1.2 adds for the pagination key — and functional spec §3.4's "roughly 17–19" should read
   "24".
2. **`spec3.json` contains no examples, so id prefixes cannot come from it.** The file has zero
   `"example"` keys and zero id literals (verified: `grep -c '"example"'` → 0). The prefix table in
   §3.2 below is taken from `research/repos/stripe-mock/embedded/openapi/fixtures3.json`, Stripe's
   own published mock fixtures, which carry one real id per resource. The brief's instruction to read
   prefixes out of `spec3.json` is not satisfiable.
3. **`x-expandableFields` is not the expandable set.** Architecture §4.5 says `spec/expandable.py` is
   generated from `x-expandableFields`. That key lists every property whose schema is a `$ref` or an
   `anyOf`, including plain embedded objects — `customer.address`, `charge.billing_details`,
   `dispute.evidence`, `line_item.period`, `balance_transaction.fee_details`. Feeding it straight into
   an expansion resolver would advertise `expand[]=address`, which the real API rejects. The correct
   derivation, and the one this component depends on for deciding which columns hold a bare id, is
   in §3.6.

### Table and column inventory

24 tables, 471 columns, 69 indexes. Verified by executing the four DDL blocks below against an
in-memory SQLite: every table ends in `STRICT`, every table declares a primary key, no foreign key
points at a missing table, no wall-clock expression appears anywhere, and each of the 22 listable
tables has its `x_seq` column, its unique `(x_seq DESC)` index and its seeded `counters` row.

| File | Table | Columns |
|---|---|---|
| `001_core` | `customers` | 20 |
| | `products` | 18 |
| | `prices` | 20 |
| | `coupons` | 17 |
| | `promotion_codes` | 12 |
| | `tax_rates` | 17 |
| `002_payments` | `payment_methods` | 10 |
| | `payment_intents` | 28 |
| | `charges` | 32 |
| | `refunds` | 20 |
| | `disputes` | 15 |
| | `setup_intents` | 21 |
| | `balance_transactions` | 17 |
| | `payouts` | 22 |
| `003_billing` | `subscriptions` | 36 |
| | `subscription_items` | 13 |
| | `subscription_schedules` | 16 |
| | `invoices` | 55 |
| | `invoiceitems` | 22 |
| | `credit_notes` | 30 |
| | `customer_balance_transactions` | 12 |
| `004_infra` | `events` | 8 |
| | `idempotency_keys` | 8 |
| | `counters` | 2 |

Every count except `idempotency_keys` and `counters` includes one `x_seq` column (§3.1.2), so the
resource field counts are these minus one.

`invoices` at 55 columns against the schema's 78 properties is the constants-and-nulls rule of §3.1
doing its work: 24 of Stripe's invoice fields are Connect, Stripe Tax, presentation or expand-only,
and every one of them is a constant in the serializer rather than a column in the schema hash.

## Public Interface

The DDL itself is the interface; it is given in full in §3.3–§3.5. The Python surface this component
exposes to the rest of the world is small.

```python
# _ids.py
STRIPE_ID_PREFIXES: Mapping[str, str]        # object name -> prefix, the table in §3.2
ID_ALPHABET: str = string.ascii_letters + string.digits   # 62 chars, Stripe's own shape

def stripe_id(ctx: seahaven.Ctx, prefix: str) -> str:
    """A Stripe-shaped id drawn from the instance's seeded stream.

    Returns f"{prefix}{token}" where token is 24 characters from ID_ALPHABET,
    each drawn from ctx.ids.random. Never uses ctx.ids.uuid().
    Raises WorldBug if `prefix` is not a value in STRIPE_ID_PREFIXES.
    """

def coupon_id(ctx: seahaven.Ctx, supplied: str | None) -> str:
    """Coupons are the one resource whose id is caller-suppliable and unprefixed.
    Returns `supplied` if given, else an 8-character uppercase-alphanumeric token."""

# _seq.py
def next_seq(ctx: seahaven.Ctx, table: str) -> int:
    """The next monotonic pagination key for `table`, from the `counters` row of that
    name. The only reader or writer of `counters`. Raises WorldBug for a table that has
    no counter row — i.e. one that is not listable. See §3.1.2."""

# _json.py
def dumps(value: object) -> str:
    """json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).
    The only function that writes a JSON TEXT column. Reproducible bytes across runs."""

def loads(text: str | None) -> object | None:
    """None-preserving json.loads."""

# _time.py
ISO_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"

def to_unix(iso: str) -> int:
    """Canonical Seahaven TEXT timestamp -> Unix seconds. The only such conversion."""

def from_unix(seconds: int) -> str:
    """Unix seconds -> canonical `2026-06-01T09:00:00.000Z`. Used by params.py when an
    agent supplies a Unix timestamp (trial_end, cancel_at, billing_cycle_anchor)."""

# serialize/fields.py
@dataclass(frozen=True)
class FieldMap:
    object: str                       # the `object` discriminator
    table: str
    columns: Mapping[str, str]        # column name -> API field name (identity for most);
                                      # never contains an `x_`-prefixed column, x_seq included
    timestamps: frozenset[str]        # columns converted by _time.to_unix
    json_columns: frozenset[str]      # columns inflated by _json.loads
    money: frozenset[str]             # INTEGER minor-unit columns (documentation + tests)
    decimals: frozenset[str]          # TEXT-decimal columns emitted as JSON numbers
    constants: Mapping[str, object]   # API field -> fixed value (issuer, automatic_tax, livemode…)
    derived: Mapping[str, Callable]   # API field -> function(ctx, row) for joined/computed fields
    always_present: frozenset[str]    # required ∪ nullable, from spec3.min.json
    omit_when_none: frozenset[str]    # neither required nor nullable

def to_api(ctx: seahaven.Ctx, fmap: FieldMap, row: sqlite3.Row) -> dict:
    """One row -> one Stripe object, unexpanded. References serialise as bare id strings.
    Raises WorldBug if a column in `fmap` is absent from `row`."""

def deleted_stub(object_name: str, id: str) -> dict:
    """{"id": id, "object": object_name, "deleted": True} — the three-key shape of
    deleted_customer / deleted_product / deleted_coupon / deleted_invoice / deleted_invoiceitem."""
```

## Internal Design Approach

### 1. Column conventions

These nine rules are the whole convention. They make the field map nearly empty, which is the point:
a rename that lives only in a Python dict is a rename that drifts.

1. **A column is named exactly after its Stripe field**, for every top-level scalar. There are no
   cosmetic renames. The only renames are the flattenings in rule 8 and the world-internal columns in
   rule 9. (Checked: none of the ~600 field names collides with a SQLite keyword — `end`, `check`,
   `default`, `references` and `transaction` all appear only as sub-keys inside nested JSON or as
   prefixes, e.g. `balance_transaction`.)
2. **`object` and `livemode` are never stored.** `object` comes from the `FieldMap`. `livemode` is a
   constant `false` — but see §3.8: four in-scope objects have no `livemode` field at all.
3. **Timestamps are `TEXT`** in Seahaven's canonical `2026-06-01T09:00:00.000Z` form, in a column
   keeping the Stripe field's own name (`created`, `available_on`, `arrival_date`, `due_date`,
   `trial_end`, `start_date`, `redeem_by`, `period_start`, …). No `_at` suffix is added; the
   `timestamps` frozenset on the `FieldMap` is what drives conversion, not a naming convention a
   future column could quietly fail to follow. No DDL default anywhere reads a clock.
4. **Booleans are `INTEGER NOT NULL CHECK (col IN (0, 1))`.** `STRICT` has no `BOOLEAN`. The
   serializer emits `bool(value)`.
5. **Money is `INTEGER`, minor units.** No `REAL` anywhere in the money path.
6. **Rates and percentages are `TEXT` holding an exact decimal literal** (`'25.5'`, `'8.875'`), not
   `REAL`. Stripe types `coupon.percent_off`, `tax_rate.percentage` and `tax_rate.effective_percentage`
   as JSON `number`, and there is no decimal type in `STRICT` (capability-map §Schema). Storing the
   literal keeps functional spec §4's "no floats anywhere in the money path" true for real: the
   billing engine reads these with `decimal.Decimal(text)`, and the serializer emits
   `json.loads(text)` so the wire value is a JSON number of exactly the digits stored. `REAL` would
   make `33.33` un-round-trippable and put a float into proration.
   `price.unit_amount_decimal` and `invoiceitem.quantity_decimal` are already strings on the wire, so
   they are `TEXT` in and `TEXT` out.
7. **JSON columns are `TEXT` with a `CHECK`**, always in one of two forms:
   - non-nullable object: `col TEXT NOT NULL CHECK (json_valid(col) AND json_type(col) = 'object')`
   - non-nullable array:  `col TEXT NOT NULL CHECK (json_valid(col) AND json_type(col) = 'array')`
   - nullable:            `col TEXT CHECK (col IS NULL OR (json_valid(col) AND json_type(col) = 'object'))`

   ProjectTracker's `CHECK (json_valid(payload))` is the established pattern
   (`schema/001_core.sql:105-107`); the added `json_type` clause is what stops a bare `"null"` or a
   naked number from satisfying a column that is supposed to hold an object. JSON1 is fully available
   (capability-map §Schema). Every value is written by `_json.dumps` and by nothing else.
8. **Nested API fields are flattened to columns only when a list filter or an ordering needs them.**
   Two places: `invoice.parent.subscription_details.subscription` (because `GET /v1/invoices` filters
   on `subscription`) becomes `parent_type` + `parent_subscription` +
   `parent_subscription_proration_date`, and `invoiceitem.period.{start,end}` /
   `line_item.period.{start,end}` become `period_start` + `period_end` (because proration reads the
   window). Everywhere else a nested object is one JSON column under its own name.
9. **World-internal columns carry an `x_` prefix and are never serialised.** Two are per-resource:
   `balance_transactions.x_payout` (the grouping that answers
   `GET /v1/balance_transactions?payout=…`, which is not a field on the object) and
   `payment_methods.x_behavior` (the magic-card failure-injection tag from
   [`magic-card-table.md`](../research/stripe-billing-and-payments/test-mode-clocks-and-prior-art/magic-card-table.md)).
   The third, `x_seq`, is on every listable table — see rule 10. A test asserts no `x_`-prefixed
   column appears in any `FieldMap.columns`.
10. **Every listable table carries `x_seq INTEGER NOT NULL`, the pagination key**, declared
   immediately after the primary key and covered by a unique `(x_seq DESC)` index. It is assigned on
   insert from `counters`, one row per table, by `_seq.next_seq(ctx, table)` and by nothing else.
   `x_seq` replaces `(created DESC, id DESC)` as the list ordering throughout — see §3.11 for why
   that ordering was wrong and §3.1.1 below for why this column keeps the `x_` prefix despite
   appearing almost everywhere.

**A field gets a column only if its value can differ between two rows of this world.** Constants get
no column and are emitted from `FieldMap.constants`. This is what keeps `invoices` at 54 columns
rather than 78, and it is why `invoice.issuer`, `invoice.automatic_tax` and
`subscription.automatic_tax` — required, non-nullable, and permanently fixed here — are storage-free
(§3.7). The rule is also the test: a constant that the world ever needs to vary is a schema change,
and a schema change regenerates every fixture, so the rule is stated rather than discovered.

#### 3.1.1 Why `x_seq` and not `seq`

Architecture §6.2 names the column `seq`. This design spells it **`x_seq`**, and
`cross_cutting.md`'s `PageOrder` should reference it by that name — a visible deviation rather than a
silent one, flagged here because it crosses a component boundary.

The reason is that the `x_` convention is only worth having if it has no exceptions. Its whole value
is one blanket test (`test_no_x_columns_serialised`) that a reviewer can trust without checking a
list; the moment one world-internal column is spelled without the prefix, the test needs an
allow-list and the convention stops meaning anything. `x_seq` is exactly as un-serialisable as
`x_payout` — it is not a Stripe field, it must never appear in a response, and an agent must never be
able to filter on it — so it takes the prefix. That it appears on 22 tables rather than one is an
argument for consistency, not against it.

#### 3.1.2 How `x_seq` is assigned

```python
# _seq.py
def next_seq(ctx: seahaven.Ctx, table: str) -> int:
    """The next pagination key for `table`. The only reader or writer of `counters`.
    Raises WorldBug if `table` has no counter row (i.e. is not a listable table)."""
    row = ctx.db.execute(
        "UPDATE counters SET value = value + 1 WHERE name = ? RETURNING value", (table,)
    ).fetchone()
    if row is None:
        raise WorldBug(f"no counter for table {table!r}")
    return row[0]
```

A `counters` table rather than `MAX(x_seq) + 1`. The `MAX` form is tempting — one fewer table, and
SQLite answers `SELECT MAX(indexed_col)` from a single index seek — but it **reuses a number after a
delete**, and this world hard-deletes rows (`invoices`, `invoiceitems`, `subscription_items`; §3.3).
A reused `x_seq` makes a cursor ambiguous: `starting_after` resolves an id to its `x_seq`, and if
that number now belongs to a different row the page silently skips or repeats. A counter that only
ever increases cannot do that. The same table then also answers "where does a monotonic sequence
live", which is the question §3.13 has to answer anyway.

One constraint this pushes onto `fixtures_src/generate.py`, worth stating because nothing else
enforces it: **the generator must insert rows in the order of its simulated timeline.** `x_seq` order
is insert order, and an eval that reads `created` off a listed object will see the two disagree if
the generator writes, say, all customers and then all their subscriptions out of timeline order. The
fixture invariant test in the plan below checks exactly this.

**Fields ruled `null` by
[`scope-boundary-edges.md`](../research/stripe-billing-and-payments/api-surface-and-object-graph/scope-boundary-edges.md)
get no column either** — every Connect field (`application`, `on_behalf_of`, `application_fee*`,
`transfer*`), every Stripe Tax field (`customer.tax`, `product.tax_code` is stubbed as a plain
string and does get a column since a caller may set it), `review`, `radar_options`,
`customer.cash_balance`, `customer.default_source`, `invoice.default_source`,
`subscription.default_source`, `managed_payments`, and every `test_clock` pointer. They serialise as
`null` from `FieldMap.constants`. That is 30-odd columns this world does not pay a schema hash for.

### 2. Id prefixes

Source: `research/repos/stripe-mock/embedded/openapi/fixtures3.json` (see §Purpose correction 2 —
`spec3.json` carries no examples). Suffix is 24 characters from `[A-Za-z0-9]` drawn from
`ctx.ids.random`; real Stripe suffixes vary in length and encode nothing, so a fixed 24 is faithful
in shape and simpler to assert.

| Object | Prefix | Note |
|---|---|---|
| `customer` | `cus_` | |
| `payment_method` | `pm_` | |
| `product` | `prod_` | |
| `price` | `price_` | |
| `coupon` | *(none)* | Caller-suppliable; otherwise 8 mixed-case alphanumerics, e.g. `hbzb1NEf` (Phase 7 recording; the design's first guess of uppercase-only was corrected by it). The one unprefixed id in the world. |
| `promotion_code` | `promo_` | Distinct from the human-facing `code` field. |
| `tax_rate` | `txr_` | |
| `payment_intent` | `pi_` | |
| `charge` | `ch_` | |
| `refund` | `re_` | |
| `dispute` | `dp_` | |
| `setup_intent` | `seti_` | |
| `balance_transaction` | `txn_` | |
| `payout` | `po_` | |
| `customer_balance_transaction` | `cbtxn_` | Not `txn_` — the two ledgers do not share a prefix. |
| `subscription` | `sub_` | |
| `subscription_item` | `si_` | |
| `subscription_schedule` | `sub_sched_` | |
| `invoice` | `in_` | |
| `invoiceitem` | `ii_` | |
| `line_item` | `il_` | Nested in `invoice.lines`; `il_tmp_` is the preview-only variant and this world never mints it. |
| `credit_note` | `cn_` | |
| `credit_note_line_item` | `cnli_` | Nested in `credit_note.lines`. |
| `discount` | `di_` | Nested in its owner. |
| `event` | `evt_` | |

Three stub prefixes, for ids this world synthesises but never resolves (per the edges doc):

| Stub | Prefix | Where |
|---|---|---|
| external account | `ba_` / `card_` | `payout.destination` |
| mandate | `mandate_` | `setup_intent.mandate`, `setup_intent.single_use_mandate` |
| setup attempt | `setatt_` | `setup_intent.latest_attempt` |

And one non-object id: the request id echoed on `event.request.id` and stored on
`idempotency_keys.request_id` is `req_` (confirmed in `stripe-python`'s test fixtures; not present in
`spec3.json`).

`invoice.number` and `credit_note.number` are **not** ids and take no prefix: they are
`{customer.invoice_prefix}-{next_invoice_sequence:04d}` and `{invoice.number}-CN-{n}` respectively,
minted at finalization / issue. Where those two sequences live is §3.13.

### 3. `001_core.sql` — 6 tables

```sql
-- StripeAPI core resources. Every table STRICT, every table an explicit primary key,
-- no wall-clock expression anywhere: timestamps are canonical TEXT written by the
-- handler from ctx.clock.iso(). Money is INTEGER minor units. Rates are TEXT decimal
-- literals, never REAL. JSON columns are TEXT guarded by json_valid + json_type.
--
-- Foreign keys are declared (Seahaven opens every connection with foreign_keys = ON)
-- but no handler lets SQLite report a missing parent: resources/_lookup.py SELECTs the
-- parent first and raises Stripe's resource_missing. The constraints are the second lock.

CREATE TABLE customers (
    id                    TEXT PRIMARY KEY,
    x_seq                 INTEGER NOT NULL,
    created               TEXT NOT NULL,
    deleted               INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1)),
    address               TEXT CHECK (address IS NULL OR (json_valid(address) AND json_type(address) = 'object')),
    balance               INTEGER NOT NULL DEFAULT 0,
    currency              TEXT,
    delinquent            INTEGER CHECK (delinquent IS NULL OR delinquent IN (0, 1)),
    description           TEXT,
    discount              TEXT CHECK (discount IS NULL OR (json_valid(discount) AND json_type(discount) = 'object')),
    email                 TEXT,
    invoice_prefix        TEXT NOT NULL,
    invoice_settings      TEXT NOT NULL CHECK (json_valid(invoice_settings) AND json_type(invoice_settings) = 'object'),
    metadata              TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    name                  TEXT,
    next_invoice_sequence INTEGER NOT NULL DEFAULT 1,
    phone                 TEXT,
    preferred_locales     TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(preferred_locales) AND json_type(preferred_locales) = 'array'),
    shipping              TEXT CHECK (shipping IS NULL OR (json_valid(shipping) AND json_type(shipping) = 'object')),
    tax_exempt            TEXT CHECK (tax_exempt IS NULL OR tax_exempt IN ('exempt', 'none', 'reverse'))
) STRICT;

CREATE UNIQUE INDEX customers_by_seq ON customers (x_seq DESC);
-- invoice.number is {invoice_prefix}-{next_invoice_sequence:04d}; the prefix is what makes
-- two customers' `-0001` invoices distinct, so it is unique. See §3.13.
CREATE UNIQUE INDEX customers_invoice_prefix ON customers (invoice_prefix);
CREATE INDEX customers_by_email   ON customers (email, x_seq DESC);

-- products.default_price and prices.product are mutually referential. Both are
-- declared; the create path writes the product with default_price NULL, then the
-- price, then UPDATEs the product. SQLite's non-deferred FKs are satisfied at every
-- step, so no DEFERRABLE clause is needed (and none is available on a STRICT table
-- without also changing the connection's pragmas, which world code may not do).
CREATE TABLE products (
    id                   TEXT PRIMARY KEY,
    x_seq                INTEGER NOT NULL,
    created              TEXT NOT NULL,
    updated              TEXT NOT NULL,
    deleted              INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1)),
    active               INTEGER NOT NULL CHECK (active IN (0, 1)),
    default_price        TEXT REFERENCES prices (id),
    description          TEXT,
    images               TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(images) AND json_type(images) = 'array'),
    marketing_features   TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(marketing_features) AND json_type(marketing_features) = 'array'),
    metadata             TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    name                 TEXT NOT NULL,
    package_dimensions   TEXT CHECK (package_dimensions IS NULL OR (json_valid(package_dimensions) AND json_type(package_dimensions) = 'object')),
    shippable            INTEGER CHECK (shippable IS NULL OR shippable IN (0, 1)),
    statement_descriptor TEXT,
    tax_code             TEXT,
    unit_label           TEXT,
    url                  TEXT
) STRICT;

CREATE UNIQUE INDEX products_by_seq ON products (x_seq DESC);
CREATE INDEX products_by_active  ON products (active, x_seq DESC);

CREATE TABLE prices (
    id                  TEXT PRIMARY KEY,
    x_seq               INTEGER NOT NULL,
    created             TEXT NOT NULL,
    active              INTEGER NOT NULL CHECK (active IN (0, 1)),
    billing_scheme      TEXT NOT NULL CHECK (billing_scheme IN ('per_unit', 'tiered')),
    currency            TEXT NOT NULL,
    currency_options    TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(currency_options) AND json_type(currency_options) = 'object'),
    custom_unit_amount  TEXT CHECK (custom_unit_amount IS NULL OR (json_valid(custom_unit_amount) AND json_type(custom_unit_amount) = 'object')),
    lookup_key          TEXT,
    metadata            TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    nickname            TEXT,
    product             TEXT NOT NULL REFERENCES products (id),
    recurring           TEXT CHECK (recurring IS NULL OR (json_valid(recurring) AND json_type(recurring) = 'object')),
    tax_behavior        TEXT CHECK (tax_behavior IS NULL OR tax_behavior IN ('exclusive', 'inclusive', 'unspecified')),
    tiers               TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(tiers) AND json_type(tiers) = 'array'),
    tiers_mode          TEXT CHECK (tiers_mode IS NULL OR tiers_mode IN ('graduated', 'volume')),
    transform_quantity  TEXT CHECK (transform_quantity IS NULL OR (json_valid(transform_quantity) AND json_type(transform_quantity) = 'object')),
    type                TEXT NOT NULL CHECK (type IN ('one_time', 'recurring')),
    unit_amount         INTEGER,
    unit_amount_decimal TEXT,
    CHECK ((type = 'recurring') = (recurring IS NOT NULL))
) STRICT;

CREATE UNIQUE INDEX prices_by_seq ON prices (x_seq DESC);
CREATE INDEX prices_by_product ON prices (product, x_seq DESC);
CREATE INDEX prices_by_active  ON prices (active, x_seq DESC);
-- Lookup-key uniqueness. Phase 7's recording corrected the design's
-- live-only reading: the conflict fires even when the holder is inactive,
-- so the uniqueness is over every holder. `transfer_lookup_key` dodges it
-- by *clearing* the holder's key (an empty-string update also clears one,
-- probed) — the holder keeps `active` as it was; nothing is archived.
CREATE UNIQUE INDEX prices_lookup_key ON prices (lookup_key) WHERE lookup_key IS NOT NULL;

CREATE TABLE coupons (
    id                 TEXT PRIMARY KEY,
    x_seq              INTEGER NOT NULL,
    created            TEXT NOT NULL,
    deleted            INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1)),
    amount_off         INTEGER,
    applies_to         TEXT CHECK (applies_to IS NULL OR (json_valid(applies_to) AND json_type(applies_to) = 'object')),
    currency           TEXT,
    currency_options   TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(currency_options) AND json_type(currency_options) = 'object'),
    duration           TEXT NOT NULL CHECK (duration IN ('forever', 'once', 'repeating')),
    duration_in_months INTEGER,
    max_redemptions    INTEGER,
    metadata           TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    name               TEXT,
    percent_off        TEXT,
    redeem_by          TEXT,
    times_redeemed     INTEGER NOT NULL DEFAULT 0,
    valid              INTEGER NOT NULL CHECK (valid IN (0, 1)),
    CHECK ((amount_off IS NULL) <> (percent_off IS NULL)),
    CHECK ((amount_off IS NULL) OR (currency IS NOT NULL)),
    CHECK ((duration = 'repeating') = (duration_in_months IS NOT NULL))
) STRICT;

CREATE UNIQUE INDEX coupons_by_seq ON coupons (x_seq DESC);

-- `valid` is computed by Stripe from redeem_by vs now and times_redeemed vs
-- max_redemptions. It is a stored column rather than a generated one because the
-- redeem_by half needs `now`, and SH103 forbids a clock expression in a generated
-- column. resources/coupons.py recomputes it on every write that can change it; the
-- clock is frozen, so a row written once stays correct for the instance's life.

CREATE TABLE promotion_codes (
    id              TEXT PRIMARY KEY,
    x_seq           INTEGER NOT NULL,
    created         TEXT NOT NULL,
    active          INTEGER NOT NULL CHECK (active IN (0, 1)),
    code            TEXT NOT NULL,
    coupon          TEXT NOT NULL REFERENCES coupons (id),
    customer        TEXT REFERENCES customers (id),
    expires_at      TEXT,
    max_redemptions INTEGER,
    metadata        TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    restrictions    TEXT NOT NULL CHECK (json_valid(restrictions) AND json_type(restrictions) = 'object'),
    times_redeemed  INTEGER NOT NULL DEFAULT 0
) STRICT;

-- Phase 7's recording settled the wire shape at the pinned version: there is
-- no top-level `coupon` field and no top-level `coupon` parameter. The
-- reference is nested under `promotion: {type: "coupon", coupon: <id>}` —
-- an embedded wrapper (never expandable itself) around an expandable coupon
-- reference (`expand[]=promotion.coupon`, probed). The column above is the
-- bare id; the serializer dresses it at the edge. Uniqueness of a `code`
-- holds among active codes ("An active promotion code with `code: X`
-- already exists.", probed).
CREATE UNIQUE INDEX promotion_codes_by_seq  ON promotion_codes (x_seq DESC);
CREATE INDEX promotion_codes_by_code     ON promotion_codes (code, x_seq DESC);
CREATE INDEX promotion_codes_by_coupon   ON promotion_codes (coupon, x_seq DESC);
CREATE INDEX promotion_codes_by_customer ON promotion_codes (customer, x_seq DESC);
CREATE UNIQUE INDEX promotion_codes_active_code ON promotion_codes (code) WHERE active = 1;

CREATE TABLE tax_rates (
    id                   TEXT PRIMARY KEY,
    x_seq                INTEGER NOT NULL,
    created              TEXT NOT NULL,
    active               INTEGER NOT NULL CHECK (active IN (0, 1)),
    country              TEXT,
    description          TEXT,
    display_name         TEXT NOT NULL,
    effective_percentage TEXT,
    flat_amount          TEXT CHECK (flat_amount IS NULL OR (json_valid(flat_amount) AND json_type(flat_amount) = 'object')),
    inclusive            INTEGER NOT NULL CHECK (inclusive IN (0, 1)),
    jurisdiction         TEXT,
    jurisdiction_level   TEXT CHECK (jurisdiction_level IS NULL OR jurisdiction_level IN
                             ('city', 'country', 'county', 'district', 'multiple', 'state')),
    metadata             TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    percentage           TEXT NOT NULL,
    rate_type            TEXT CHECK (rate_type IS NULL OR rate_type IN ('flat_amount', 'percentage')),
    state                TEXT,
    tax_type             TEXT CHECK (tax_type IS NULL OR tax_type IN
                             ('amusement_tax', 'communications_tax', 'gst', 'hst', 'igst', 'jct',
                              'lease_tax', 'mass_transit_parking_tax', 'parking_tax', 'pst', 'qst',
                              'retail_delivery_fee', 'rst', 'sales_tax', 'service_tax', 'vat'))
) STRICT;

CREATE UNIQUE INDEX tax_rates_by_seq ON tax_rates (x_seq DESC);
CREATE INDEX tax_rates_by_active  ON tax_rates (active, x_seq DESC);
```

**`deleted` tombstones.** Three tables carry one: `customers`, `products`, `coupons`. They are the
three whose `DELETE` has a `deleted_<x>` schema in `spec3.json` *and* which other rows point at —
`price.product` is `anyOf[string | product | deleted_product]`, `charge.customer` is
`anyOf[string | customer | deleted_customer]`. Those union members only make sense if a deleted row
is still reachable through a reference, so a hard delete would both break a declared foreign key and
make a documented response shape unreachable. Everywhere else `DELETE` removes the row:
`invoices` and `invoiceitems` (draft-only, and nothing points at a draft once its items are detached)
and `subscription_items`. `DELETE /v1/subscriptions/{id}` is a cancel, not a delete, and
`POST …/detach` on a payment method sets `customer = NULL`.

### 4. `002_payments.sql` — 8 tables

```sql
CREATE TABLE payment_methods (
    id              TEXT PRIMARY KEY,
    x_seq           INTEGER NOT NULL,
    created         TEXT NOT NULL,
    type            TEXT NOT NULL CHECK (type IN (
        'acss_debit','affirm','afterpay_clearpay','alipay','alma','amazon_pay','au_becs_debit',
        'bacs_debit','bancontact','billie','bizum','blik','boleto','card','card_present','cashapp',
        'crypto','custom','customer_balance','eps','fpx','giropay','grabpay','ideal',
        'interac_present','kakao_pay','klarna','konbini','kr_card','link','mb_way','mobilepay',
        'multibanco','naver_pay','nz_bank_account','oxxo','p24','pay_by_bank','payco','paynow',
        'paypal','payto','pix','promptpay','revolut_pay','samsung_pay','satispay','scalapay',
        'sepa_debit','sofort','sunbit','swish','twint','upi','us_bank_account','wechat_pay','zip')),
    allow_redisplay TEXT CHECK (allow_redisplay IS NULL OR allow_redisplay IN ('always', 'limited', 'unspecified')),
    billing_details TEXT NOT NULL CHECK (json_valid(billing_details) AND json_type(billing_details) = 'object'),
    customer        TEXT REFERENCES customers (id),
    metadata        TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    -- The rail sub-object, serialised under the key named by `type`. Full
    -- payment_method_card / payment_method_us_bank_account shapes for those two rails,
    -- `{}` for the other 55. One column, not 57: see §3.9.
    rail            TEXT NOT NULL CHECK (json_valid(rail) AND json_type(rail) = 'object'),
    -- Failure-injection tag from the magic-card table; never serialised.
    x_behavior      TEXT
) STRICT;

CREATE UNIQUE INDEX payment_methods_by_seq  ON payment_methods (x_seq DESC);
CREATE INDEX payment_methods_by_customer ON payment_methods (customer, x_seq DESC);
CREATE INDEX payment_methods_by_type     ON payment_methods (type, x_seq DESC);

CREATE TABLE payment_intents (
    id                          TEXT PRIMARY KEY,
    x_seq                       INTEGER NOT NULL,
    created                     TEXT NOT NULL,
    amount                      INTEGER NOT NULL,
    amount_capturable           INTEGER NOT NULL DEFAULT 0,
    amount_received             INTEGER NOT NULL DEFAULT 0,
    automatic_payment_methods   TEXT CHECK (automatic_payment_methods IS NULL OR (json_valid(automatic_payment_methods) AND json_type(automatic_payment_methods) = 'object')),
    canceled_at                 TEXT,
    cancellation_reason         TEXT CHECK (cancellation_reason IS NULL OR cancellation_reason IN
                                    ('abandoned','automatic','duplicate','expired','failed_invoice',
                                     'fraudulent','requested_by_customer','void_invoice')),
    capture_method              TEXT NOT NULL CHECK (capture_method IN ('automatic', 'automatic_async', 'manual')),
    client_secret               TEXT,
    confirmation_method         TEXT NOT NULL CHECK (confirmation_method IN ('automatic', 'manual')),
    currency                    TEXT NOT NULL,
    customer                    TEXT REFERENCES customers (id),
    description                 TEXT,
    last_payment_error          TEXT CHECK (last_payment_error IS NULL OR (json_valid(last_payment_error) AND json_type(last_payment_error) = 'object')),
    latest_charge               TEXT REFERENCES charges (id),
    metadata                    TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    next_action                 TEXT CHECK (next_action IS NULL OR (json_valid(next_action) AND json_type(next_action) = 'object')),
    payment_method              TEXT REFERENCES payment_methods (id),
    payment_method_options      TEXT CHECK (payment_method_options IS NULL OR (json_valid(payment_method_options) AND json_type(payment_method_options) = 'object')),
    payment_method_types        TEXT NOT NULL DEFAULT '["card"]' CHECK (json_valid(payment_method_types) AND json_type(payment_method_types) = 'array'),
    receipt_email               TEXT,
    setup_future_usage          TEXT CHECK (setup_future_usage IS NULL OR setup_future_usage IN ('off_session', 'on_session')),
    shipping                    TEXT CHECK (shipping IS NULL OR (json_valid(shipping) AND json_type(shipping) = 'object')),
    statement_descriptor        TEXT,
    statement_descriptor_suffix TEXT,
    status                      TEXT NOT NULL CHECK (status IN
                                    ('canceled','processing','requires_action','requires_capture',
                                     'requires_confirmation','requires_payment_method','succeeded'))
) STRICT;

CREATE UNIQUE INDEX payment_intents_by_seq  ON payment_intents (x_seq DESC);
CREATE INDEX payment_intents_by_customer ON payment_intents (customer, x_seq DESC);

CREATE TABLE charges (
    id                             TEXT PRIMARY KEY,
    x_seq                          INTEGER NOT NULL,
    created                        TEXT NOT NULL,
    amount                         INTEGER NOT NULL,
    amount_captured                INTEGER NOT NULL DEFAULT 0,
    amount_refunded                INTEGER NOT NULL DEFAULT 0,
    balance_transaction            TEXT REFERENCES balance_transactions (id),
    billing_details                TEXT NOT NULL CHECK (json_valid(billing_details) AND json_type(billing_details) = 'object'),
    calculated_statement_descriptor TEXT,
    captured                       INTEGER NOT NULL CHECK (captured IN (0, 1)),
    currency                       TEXT NOT NULL,
    customer                       TEXT REFERENCES customers (id),
    description                    TEXT,
    disputed                       INTEGER NOT NULL DEFAULT 0 CHECK (disputed IN (0, 1)),
    failure_balance_transaction    TEXT REFERENCES balance_transactions (id),
    failure_code                   TEXT,
    failure_message                TEXT,
    fraud_details                  TEXT CHECK (fraud_details IS NULL OR (json_valid(fraud_details) AND json_type(fraud_details) = 'object')),
    metadata                       TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    outcome                        TEXT CHECK (outcome IS NULL OR (json_valid(outcome) AND json_type(outcome) = 'object')),
    paid                           INTEGER NOT NULL CHECK (paid IN (0, 1)),
    payment_intent                 TEXT REFERENCES payment_intents (id),
    payment_method                 TEXT REFERENCES payment_methods (id),
    payment_method_details         TEXT CHECK (payment_method_details IS NULL OR (json_valid(payment_method_details) AND json_type(payment_method_details) = 'object')),
    receipt_email                  TEXT,
    receipt_number                 TEXT,
    receipt_url                    TEXT,
    refunded                       INTEGER NOT NULL DEFAULT 0 CHECK (refunded IN (0, 1)),
    shipping                       TEXT CHECK (shipping IS NULL OR (json_valid(shipping) AND json_type(shipping) = 'object')),
    statement_descriptor           TEXT,
    statement_descriptor_suffix    TEXT,
    status                         TEXT NOT NULL CHECK (status IN ('failed', 'pending', 'succeeded')),
    CHECK (amount_refunded <= amount_captured)
) STRICT;

CREATE UNIQUE INDEX charges_by_seq        ON charges (x_seq DESC);
CREATE INDEX charges_by_customer       ON charges (customer, x_seq DESC);
CREATE INDEX charges_by_payment_intent ON charges (payment_intent, x_seq DESC);

CREATE TABLE refunds (
    id                          TEXT PRIMARY KEY,
    x_seq                       INTEGER NOT NULL,
    created                     TEXT NOT NULL,
    amount                      INTEGER NOT NULL,
    balance_transaction         TEXT REFERENCES balance_transactions (id),
    charge                      TEXT REFERENCES charges (id),
    currency                    TEXT NOT NULL,
    customer                    TEXT REFERENCES customers (id),
    description                 TEXT,
    destination_details         TEXT CHECK (destination_details IS NULL OR (json_valid(destination_details) AND json_type(destination_details) = 'object')),
    failure_balance_transaction TEXT REFERENCES balance_transactions (id),
    failure_reason              TEXT,
    instructions_email          TEXT,
    metadata                    TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    payment_intent              TEXT REFERENCES payment_intents (id),
    payment_method              TEXT REFERENCES payment_methods (id),
    pending_reason              TEXT CHECK (pending_reason IS NULL OR pending_reason IN ('charge_pending', 'insufficient_funds', 'processing')),
    reason                      TEXT CHECK (reason IS NULL OR reason IN
                                    ('duplicate', 'expired_uncaptured_charge', 'fraudulent', 'requested_by_customer')),
    receipt_number              TEXT,
    -- doc-only enum: spec types this `string`, the description closes the set
    status                      TEXT CHECK (status IS NULL OR status IN
                                    ('pending', 'requires_action', 'succeeded', 'failed', 'canceled'))
) STRICT;

CREATE UNIQUE INDEX refunds_by_seq        ON refunds (x_seq DESC);
CREATE INDEX refunds_by_charge         ON refunds (charge, x_seq DESC);
CREATE INDEX refunds_by_payment_intent ON refunds (payment_intent, x_seq DESC);

CREATE TABLE disputes (
    id                         TEXT PRIMARY KEY,
    x_seq                      INTEGER NOT NULL,
    created                    TEXT NOT NULL,
    amount                     INTEGER NOT NULL,
    charge                     TEXT NOT NULL REFERENCES charges (id),
    currency                   TEXT NOT NULL,
    enhanced_eligibility_types TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(enhanced_eligibility_types) AND json_type(enhanced_eligibility_types) = 'array'),
    evidence                   TEXT NOT NULL CHECK (json_valid(evidence) AND json_type(evidence) = 'object'),
    evidence_details           TEXT NOT NULL CHECK (json_valid(evidence_details) AND json_type(evidence_details) = 'object'),
    is_charge_refundable       INTEGER NOT NULL CHECK (is_charge_refundable IN (0, 1)),
    metadata                   TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    payment_intent             TEXT REFERENCES payment_intents (id),
    payment_method_details     TEXT CHECK (payment_method_details IS NULL OR (json_valid(payment_method_details) AND json_type(payment_method_details) = 'object')),
    -- doc-only enum, 15 values
    reason                     TEXT NOT NULL CHECK (reason IN
                                   ('bank_cannot_process','check_returned','credit_not_processed',
                                    'customer_initiated','debit_not_authorized','duplicate','fraudulent',
                                    'general','incorrect_account_details','insufficient_funds','noncompliant',
                                    'product_not_received','product_unacceptable','subscription_canceled',
                                    'unrecognized')),
    status                     TEXT NOT NULL CHECK (status IN
                                   ('lost','needs_response','prevented','under_review','warning_closed',
                                    'warning_needs_response','warning_under_review','won'))
) STRICT;

CREATE UNIQUE INDEX disputes_by_seq        ON disputes (x_seq DESC);
CREATE INDEX disputes_by_charge         ON disputes (charge, x_seq DESC);
CREATE INDEX disputes_by_payment_intent ON disputes (payment_intent, x_seq DESC);

-- dispute.balance_transactions is `array<ref:balance_transaction>` — always full
-- objects, never ids. It is NOT a column: the serializer reads
-- `SELECT * FROM balance_transactions WHERE source = :dispute_id ORDER BY x_seq`.
-- Deriving makes the "withdrawal and its reversal are both type=adjustment" invariant
-- a fact about the ledger rather than a second copy that can drift from it.

CREATE TABLE setup_intents (
    id                     TEXT PRIMARY KEY,
    x_seq                  INTEGER NOT NULL,
    created                TEXT NOT NULL,
    attach_to_self         INTEGER NOT NULL DEFAULT 0 CHECK (attach_to_self IN (0, 1)),
    automatic_payment_methods TEXT CHECK (automatic_payment_methods IS NULL OR (json_valid(automatic_payment_methods) AND json_type(automatic_payment_methods) = 'object')),
    cancellation_reason    TEXT CHECK (cancellation_reason IS NULL OR cancellation_reason IN ('abandoned', 'duplicate', 'requested_by_customer')),
    client_secret          TEXT,
    customer               TEXT REFERENCES customers (id),
    description            TEXT,
    flow_directions        TEXT CHECK (flow_directions IS NULL OR (json_valid(flow_directions) AND json_type(flow_directions) = 'array')),
    last_setup_error       TEXT CHECK (last_setup_error IS NULL OR (json_valid(last_setup_error) AND json_type(last_setup_error) = 'object')),
    latest_attempt         TEXT,      -- stub: `setatt_…`, never resolved
    mandate                TEXT,      -- stub: `mandate_…`, never resolved
    metadata               TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    next_action            TEXT CHECK (next_action IS NULL OR (json_valid(next_action) AND json_type(next_action) = 'object')),
    payment_method         TEXT REFERENCES payment_methods (id),
    payment_method_options TEXT CHECK (payment_method_options IS NULL OR (json_valid(payment_method_options) AND json_type(payment_method_options) = 'object')),
    payment_method_types   TEXT NOT NULL DEFAULT '["card"]' CHECK (json_valid(payment_method_types) AND json_type(payment_method_types) = 'array'),
    single_use_mandate     TEXT,      -- stub
    status                 TEXT NOT NULL CHECK (status IN
                               ('canceled','processing','requires_action','requires_confirmation',
                                'requires_payment_method','succeeded')),
    -- doc-only enum: description names on_session / off_session, default off_session
    usage                  TEXT NOT NULL DEFAULT 'off_session' CHECK (usage IN ('on_session', 'off_session'))
) STRICT;

CREATE UNIQUE INDEX setup_intents_by_seq        ON setup_intents (x_seq DESC);
CREATE INDEX setup_intents_by_customer       ON setup_intents (customer, x_seq DESC);
CREATE INDEX setup_intents_by_payment_method ON setup_intents (payment_method, x_seq DESC);

CREATE TABLE balance_transactions (
    id                 TEXT PRIMARY KEY,
    x_seq              INTEGER NOT NULL,
    created            TEXT NOT NULL,
    amount             INTEGER NOT NULL,
    available_on       TEXT NOT NULL,
    balance_type       TEXT NOT NULL DEFAULT 'payments' CHECK (balance_type IN
                           ('issuing', 'payments', 'refund_and_dispute_prefunding', 'risk_reserved')),
    currency           TEXT NOT NULL,
    description        TEXT,
    exchange_rate      TEXT,
    fee                INTEGER NOT NULL DEFAULT 0,
    fee_details        TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(fee_details) AND json_type(fee_details) = 'array'),
    net                INTEGER NOT NULL,
    reporting_category TEXT NOT NULL,   -- open string: spec declares no set and links out
    -- 16-way polymorphic reference (charge | refund | dispute | payout in scope). No FK
    -- is possible against four tables; resources/_lookup.py resolves it on expansion and
    -- an invariant test asserts every `source` resolves in exactly one table.
    source             TEXT,
    -- doc-only enum: "either available or pending"
    status             TEXT NOT NULL CHECK (status IN ('available', 'pending')),
    type               TEXT NOT NULL CHECK (type IN (
        'adjustment','advance','advance_funding','anticipation_repayment','application_fee',
        'application_fee_refund','charge','climate_order_purchase','climate_order_refund',
        'connect_collection_transfer','contribution','fee_credit_funding','inbound_transfer',
        'inbound_transfer_reversal','issuing_authorization_hold','issuing_authorization_release',
        'issuing_dispute','issuing_transaction','obligation_outbound','obligation_reversal_inbound',
        'payment','payment_failure_refund','payment_network_reserve_hold',
        'payment_network_reserve_release','payment_refund','payment_reversal','payment_unreconciled',
        'payout','payout_cancel','payout_failure','payout_minimum_balance_hold',
        'payout_minimum_balance_release','refund','refund_failure','reserve_hold','reserve_release',
        'reserve_transaction','reserved_funds','stripe_balance_payment_debit',
        'stripe_balance_payment_debit_reversal','stripe_fee','stripe_fx_fee','tax_fee','tax_fund',
        'topup','topup_reversal','transfer','transfer_cancel','transfer_failure','transfer_refund')),
    -- world-internal: the payout this row was swept into, answering
    -- GET /v1/balance_transactions?payout=… . Not an API field.
    x_payout           TEXT REFERENCES payouts (id),
    CHECK (net = amount - fee)
) STRICT;

CREATE UNIQUE INDEX balance_transactions_by_seq      ON balance_transactions (x_seq DESC);
CREATE INDEX balance_transactions_by_type         ON balance_transactions (type, x_seq DESC);
CREATE INDEX balance_transactions_by_source       ON balance_transactions (source, x_seq DESC);
CREATE INDEX balance_transactions_by_payout       ON balance_transactions (x_payout, x_seq DESC);
-- the available/pending split in §6.4 of the architecture is a scan of this index
CREATE INDEX balance_transactions_by_available_on ON balance_transactions (available_on, currency);

CREATE TABLE payouts (
    id                          TEXT PRIMARY KEY,
    x_seq                       INTEGER NOT NULL,
    created                     TEXT NOT NULL,
    amount                      INTEGER NOT NULL,
    arrival_date                TEXT NOT NULL,
    automatic                   INTEGER NOT NULL CHECK (automatic IN (0, 1)),
    balance_transaction         TEXT REFERENCES balance_transactions (id),
    currency                    TEXT NOT NULL,
    description                 TEXT,
    destination                 TEXT,   -- stub: `ba_…` / `card_…`, never resolved
    failure_balance_transaction TEXT REFERENCES balance_transactions (id),
    failure_code                TEXT,
    failure_message             TEXT,
    metadata                    TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    -- doc-only enum
    method                      TEXT NOT NULL DEFAULT 'standard' CHECK (method IN ('standard', 'instant')),
    original_payout             TEXT REFERENCES payouts (id),
    reconciliation_status       TEXT NOT NULL DEFAULT 'not_applicable'
                                  CHECK (reconciliation_status IN ('completed', 'in_progress', 'not_applicable')),
    reversed_by                 TEXT REFERENCES payouts (id),
    -- doc-only enum
    source_type                 TEXT NOT NULL DEFAULT 'bank_account' CHECK (source_type IN ('card', 'fpx', 'bank_account')),
    statement_descriptor        TEXT,
    -- doc-only enum
    status                      TEXT NOT NULL CHECK (status IN ('paid', 'pending', 'in_transit', 'canceled', 'failed')),
    type                        TEXT NOT NULL CHECK (type IN ('bank_account', 'card'))
) STRICT;

CREATE UNIQUE INDEX payouts_by_seq      ON payouts (x_seq DESC);
CREATE INDEX payouts_by_status       ON payouts (status, x_seq DESC);
```

`balance_transactions` ↔ `payouts` and `charges` ↔ `payment_intents` are both mutually referential,
and in both cases every crossing column is nullable, so the write order (child first with the link
`NULL`, then `UPDATE`) satisfies non-deferred foreign keys at every step.

### 5. `003_billing.sql` — 7 tables, and `004_infra.sql` — 3 tables

```sql
CREATE TABLE subscriptions (
    id                          TEXT PRIMARY KEY,
    x_seq                       INTEGER NOT NULL,
    created                     TEXT NOT NULL,
    billing_cycle_anchor        TEXT NOT NULL,
    billing_cycle_anchor_config TEXT CHECK (billing_cycle_anchor_config IS NULL OR (json_valid(billing_cycle_anchor_config) AND json_type(billing_cycle_anchor_config) = 'object')),
    -- serialised as {"type": <value>}; `flexible` changes proration, so it is a column
    billing_mode                TEXT NOT NULL DEFAULT 'classic' CHECK (billing_mode IN ('classic', 'flexible')),
    billing_schedules           TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(billing_schedules) AND json_type(billing_schedules) = 'array'),
    billing_thresholds          TEXT CHECK (billing_thresholds IS NULL OR (json_valid(billing_thresholds) AND json_type(billing_thresholds) = 'object')),
    cancel_at                   TEXT,
    cancel_at_period_end        INTEGER NOT NULL DEFAULT 0 CHECK (cancel_at_period_end IN (0, 1)),
    canceled_at                 TEXT,
    cancellation_details        TEXT CHECK (cancellation_details IS NULL OR (json_valid(cancellation_details) AND json_type(cancellation_details) = 'object')),
    collection_method           TEXT NOT NULL CHECK (collection_method IN ('charge_automatically', 'send_invoice')),
    currency                    TEXT NOT NULL,
    customer                    TEXT NOT NULL REFERENCES customers (id),
    days_until_due              INTEGER,
    default_payment_method      TEXT REFERENCES payment_methods (id),
    default_tax_rates           TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(default_tax_rates) AND json_type(default_tax_rates) = 'array'),
    description                 TEXT,
    discounts                   TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(discounts) AND json_type(discounts) = 'array'),
    ended_at                    TEXT,
    invoice_settings            TEXT NOT NULL CHECK (json_valid(invoice_settings) AND json_type(invoice_settings) = 'object'),
    latest_invoice              TEXT REFERENCES invoices (id),
    metadata                    TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    next_pending_invoice_item_invoice TEXT,
    pause_collection            TEXT CHECK (pause_collection IS NULL OR (json_valid(pause_collection) AND json_type(pause_collection) = 'object')),
    payment_settings            TEXT CHECK (payment_settings IS NULL OR (json_valid(payment_settings) AND json_type(payment_settings) = 'object')),
    pending_invoice_item_interval TEXT CHECK (pending_invoice_item_interval IS NULL OR (json_valid(pending_invoice_item_interval) AND json_type(pending_invoice_item_interval) = 'object')),
    pending_setup_intent        TEXT REFERENCES setup_intents (id),
    pending_update              TEXT CHECK (pending_update IS NULL OR (json_valid(pending_update) AND json_type(pending_update) = 'object')),
    schedule                    TEXT REFERENCES subscription_schedules (id),
    start_date                  TEXT NOT NULL,
    status                      TEXT NOT NULL CHECK (status IN
                                    ('active','canceled','incomplete','incomplete_expired','past_due',
                                     'paused','trialing','unpaid')),
    trial_end                   TEXT,
    trial_settings              TEXT CHECK (trial_settings IS NULL OR (json_valid(trial_settings) AND json_type(trial_settings) = 'object')),
    trial_start                 TEXT,
    CHECK ((trial_start IS NULL) = (trial_end IS NULL)),
    CHECK (days_until_due IS NULL OR collection_method = 'send_invoice')
) STRICT;

CREATE UNIQUE INDEX subscriptions_by_seq  ON subscriptions (x_seq DESC);
CREATE INDEX subscriptions_by_customer ON subscriptions (customer, x_seq DESC);
CREATE INDEX subscriptions_by_status   ON subscriptions (status, x_seq DESC);
CREATE INDEX subscriptions_by_schedule ON subscriptions (schedule);

-- NOTE: there is no current_period_start/current_period_end on `subscription` at
-- 2026-08-26.dahlia. The billing period lives per item, below. A single period pair on
-- this row would be drift from the real object.

CREATE TABLE subscription_items (
    id                   TEXT PRIMARY KEY,
    x_seq                INTEGER NOT NULL,
    created              TEXT NOT NULL,
    billed_until         TEXT,
    billing_thresholds   TEXT CHECK (billing_thresholds IS NULL OR (json_valid(billing_thresholds) AND json_type(billing_thresholds) = 'object')),
    current_period_end   TEXT NOT NULL,
    current_period_start TEXT NOT NULL,
    discounts            TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(discounts) AND json_type(discounts) = 'array'),
    metadata             TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    price                TEXT NOT NULL REFERENCES prices (id),
    quantity             INTEGER,
    subscription         TEXT NOT NULL REFERENCES subscriptions (id),
    tax_rates            TEXT CHECK (tax_rates IS NULL OR (json_valid(tax_rates) AND json_type(tax_rates) = 'array')),
    CHECK (current_period_start < current_period_end)
) STRICT;

-- `subscription` is a REQUIRED query parameter on GET /v1/subscription_items, so this
-- index is the whole list path. `price` serves GET /v1/subscriptions?price=… .
CREATE UNIQUE INDEX subscription_items_by_seq   ON subscription_items (x_seq DESC);
CREATE INDEX subscription_items_by_subscription ON subscription_items (subscription, x_seq DESC);
CREATE INDEX subscription_items_by_price        ON subscription_items (price, subscription);

CREATE TABLE subscription_schedules (
    id                    TEXT PRIMARY KEY,
    x_seq                 INTEGER NOT NULL,
    created               TEXT NOT NULL,
    billing_mode          TEXT NOT NULL DEFAULT 'classic' CHECK (billing_mode IN ('classic', 'flexible')),
    canceled_at           TEXT,
    completed_at          TEXT,
    current_phase         TEXT CHECK (current_phase IS NULL OR (json_valid(current_phase) AND json_type(current_phase) = 'object')),
    customer              TEXT NOT NULL REFERENCES customers (id),
    default_settings      TEXT NOT NULL CHECK (json_valid(default_settings) AND json_type(default_settings) = 'object'),
    end_behavior          TEXT NOT NULL CHECK (end_behavior IN ('cancel', 'none', 'release', 'renew')),
    metadata              TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    phases                TEXT NOT NULL CHECK (json_valid(phases) AND json_type(phases) = 'array'),
    released_at           TEXT,
    released_subscription TEXT,
    status                TEXT NOT NULL CHECK (status IN ('active', 'canceled', 'completed', 'not_started', 'released')),
    subscription          TEXT REFERENCES subscriptions (id)
) STRICT;

CREATE UNIQUE INDEX subscription_schedules_by_seq      ON subscription_schedules (x_seq DESC);
CREATE INDEX subscription_schedules_by_customer     ON subscription_schedules (customer, x_seq DESC);
CREATE INDEX subscription_schedules_by_subscription ON subscription_schedules (subscription);

-- `phases` is the scoped-down array functional spec §3.2 calls for: each element carries
-- items, start_date, end_date, iterations, trial, discounts, proration_behavior,
-- collection_method and metadata, not the full 21-property phase schema. Timestamps
-- inside it are Unix seconds (see §3.10).

CREATE TABLE invoices (
    id                               TEXT PRIMARY KEY,
    x_seq                            INTEGER NOT NULL,
    created                          TEXT NOT NULL,
    amount_due                       INTEGER NOT NULL DEFAULT 0,
    amount_overpaid                  INTEGER NOT NULL DEFAULT 0,
    amount_paid                      INTEGER NOT NULL DEFAULT 0,
    amount_paid_off_stripe           INTEGER NOT NULL DEFAULT 0,
    amount_remaining                 INTEGER NOT NULL DEFAULT 0,
    amount_shipping                  INTEGER NOT NULL DEFAULT 0,
    attempt_count                    INTEGER NOT NULL DEFAULT 0,
    attempted                        INTEGER NOT NULL DEFAULT 0 CHECK (attempted IN (0, 1)),
    auto_advance                     INTEGER NOT NULL DEFAULT 1 CHECK (auto_advance IN (0, 1)),
    automatically_finalizes_at       TEXT,
    billing_reason                   TEXT CHECK (billing_reason IS NULL OR billing_reason IN
                                         ('automatic_pending_invoice_item_invoice','manual','quote_accept',
                                          'subscription','subscription_create','subscription_cycle',
                                          'subscription_threshold','subscription_update','upcoming')),
    collection_method                TEXT NOT NULL CHECK (collection_method IN ('charge_automatically', 'send_invoice')),
    currency                         TEXT NOT NULL,
    customer                         TEXT NOT NULL REFERENCES customers (id),
    customer_address                 TEXT CHECK (customer_address IS NULL OR (json_valid(customer_address) AND json_type(customer_address) = 'object')),
    customer_email                   TEXT,
    customer_name                    TEXT,
    customer_phone                   TEXT,
    customer_shipping                TEXT CHECK (customer_shipping IS NULL OR (json_valid(customer_shipping) AND json_type(customer_shipping) = 'object')),
    customer_tax_exempt              TEXT CHECK (customer_tax_exempt IS NULL OR customer_tax_exempt IN ('exempt', 'none', 'reverse')),
    default_payment_method           TEXT REFERENCES payment_methods (id),
    default_tax_rates                TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(default_tax_rates) AND json_type(default_tax_rates) = 'array'),
    description                      TEXT,
    discounts                        TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(discounts) AND json_type(discounts) = 'array'),
    due_date                         TEXT,
    effective_at                     TEXT,
    ending_balance                   INTEGER,
    footer                           TEXT,
    last_finalization_error          TEXT CHECK (last_finalization_error IS NULL OR (json_valid(last_finalization_error) AND json_type(last_finalization_error) = 'object')),
    lines                            TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(lines) AND json_type(lines) = 'array'),
    metadata                         TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    next_payment_attempt             TEXT,
    number                           TEXT,
    -- invoice.parent, flattened: `subscription` is a list filter, so it cannot live
    -- inside a JSON blob. Rebuilt on serialisation as
    -- {"type":"subscription_details","subscription_details":{"subscription":…,…}}
    parent_type                      TEXT CHECK (parent_type IS NULL OR parent_type IN ('quote_details', 'subscription_details')),
    parent_subscription              TEXT REFERENCES subscriptions (id),
    parent_subscription_proration_date TEXT,
    payment_settings                 TEXT NOT NULL CHECK (json_valid(payment_settings) AND json_type(payment_settings) = 'object'),
    period_end                       TEXT NOT NULL,
    period_start                     TEXT NOT NULL,
    post_payment_credit_notes_amount INTEGER NOT NULL DEFAULT 0,
    pre_payment_credit_notes_amount  INTEGER NOT NULL DEFAULT 0,
    starting_balance                 INTEGER NOT NULL DEFAULT 0,
    statement_descriptor             TEXT,
    status                           TEXT CHECK (status IS NULL OR status IN ('draft', 'open', 'paid', 'uncollectible', 'void')),
    status_transitions               TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(status_transitions) AND json_type(status_transitions) = 'object'),
    subtotal                         INTEGER NOT NULL DEFAULT 0,
    subtotal_excluding_tax           INTEGER,
    total                            INTEGER NOT NULL DEFAULT 0,
    total_discount_amounts           TEXT CHECK (total_discount_amounts IS NULL OR (json_valid(total_discount_amounts) AND json_type(total_discount_amounts) = 'array')),
    total_excluding_tax              INTEGER,
    total_pretax_credit_amounts      TEXT CHECK (total_pretax_credit_amounts IS NULL OR (json_valid(total_pretax_credit_amounts) AND json_type(total_pretax_credit_amounts) = 'array')),
    total_taxes                      TEXT CHECK (total_taxes IS NULL OR (json_valid(total_taxes) AND json_type(total_taxes) = 'array')),
    CHECK (parent_subscription IS NULL OR parent_type = 'subscription_details'),
    CHECK (number IS NOT NULL OR status = 'draft')
) STRICT;

CREATE UNIQUE INDEX invoices_by_seq           ON invoices (x_seq DESC);
-- number is NULL until finalization, so the uniqueness has to be partial. See §3.13.
CREATE UNIQUE INDEX invoices_number            ON invoices (number) WHERE number IS NOT NULL;
CREATE INDEX invoices_by_customer          ON invoices (customer, x_seq DESC);
CREATE INDEX invoices_by_status            ON invoices (status, x_seq DESC);
CREATE INDEX invoices_by_subscription      ON invoices (parent_subscription, x_seq DESC);
CREATE INDEX invoices_by_collection_method ON invoices (collection_method, x_seq DESC);

-- No top-level `subscription` column and no `days_until_due`: neither field exists on
-- `invoice` at 2026-08-26.dahlia. Functional spec §4 is right and the widely-documented
-- older shape is wrong for this version.

CREATE TABLE invoiceitems (
    id               TEXT PRIMARY KEY,
    x_seq            INTEGER NOT NULL,
    -- `invoiceitem` has NO `created` field; its creation timestamp is `date`. See §3.11.
    date             TEXT NOT NULL,
    amount           INTEGER NOT NULL,
    currency         TEXT NOT NULL,
    customer         TEXT NOT NULL REFERENCES customers (id),
    description      TEXT,
    discountable     INTEGER NOT NULL DEFAULT 1 CHECK (discountable IN (0, 1)),
    discounts        TEXT CHECK (discounts IS NULL OR (json_valid(discounts) AND json_type(discounts) = 'array')),
    frozen_fields    TEXT CHECK (frozen_fields IS NULL OR (json_valid(frozen_fields) AND json_type(frozen_fields) = 'array')),
    invoice          TEXT REFERENCES invoices (id),
    metadata         TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    net_amount       INTEGER,
    parent           TEXT CHECK (parent IS NULL OR (json_valid(parent) AND json_type(parent) = 'object')),
    period_end       TEXT NOT NULL,
    period_start     TEXT NOT NULL,
    pricing          TEXT CHECK (pricing IS NULL OR (json_valid(pricing) AND json_type(pricing) = 'object')),
    proration        INTEGER NOT NULL DEFAULT 0 CHECK (proration IN (0, 1)),
    proration_details TEXT CHECK (proration_details IS NULL OR (json_valid(proration_details) AND json_type(proration_details) = 'object')),
    quantity         INTEGER NOT NULL DEFAULT 1,
    quantity_decimal TEXT NOT NULL DEFAULT '1',
    tax_rates        TEXT CHECK (tax_rates IS NULL OR (json_valid(tax_rates) AND json_type(tax_rates) = 'array'))
) STRICT;

CREATE UNIQUE INDEX invoiceitems_by_seq     ON invoiceitems (x_seq DESC);
CREATE INDEX invoiceitems_by_customer ON invoiceitems (customer, x_seq DESC);
CREATE INDEX invoiceitems_by_invoice  ON invoiceitems (invoice, x_seq DESC);
-- GET /v1/invoiceitems?pending=true is exactly "not yet swept onto an invoice"
CREATE INDEX invoiceitems_pending     ON invoiceitems (x_seq DESC) WHERE invoice IS NULL;

CREATE TABLE credit_notes (
    id                           TEXT PRIMARY KEY,
    x_seq                        INTEGER NOT NULL,
    created                      TEXT NOT NULL,
    amount                       INTEGER NOT NULL,
    amount_shipping              INTEGER NOT NULL DEFAULT 0,
    currency                     TEXT NOT NULL,
    customer                     TEXT NOT NULL REFERENCES customers (id),
    customer_balance_transaction TEXT REFERENCES customer_balance_transactions (id),
    discount_amount              INTEGER NOT NULL DEFAULT 0,
    discount_amounts             TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(discount_amounts) AND json_type(discount_amounts) = 'array'),
    effective_at                 TEXT,
    invoice                      TEXT NOT NULL REFERENCES invoices (id),
    lines                        TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(lines) AND json_type(lines) = 'array'),
    memo                         TEXT,
    metadata                     TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    number                       TEXT NOT NULL,
    out_of_band_amount           INTEGER,
    post_payment_amount          INTEGER NOT NULL DEFAULT 0,
    pre_payment_amount           INTEGER NOT NULL DEFAULT 0,
    pretax_credit_amounts        TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(pretax_credit_amounts) AND json_type(pretax_credit_amounts) = 'array'),
    reason                       TEXT CHECK (reason IS NULL OR reason IN
                                     ('duplicate', 'fraudulent', 'order_change', 'product_unsatisfactory')),
    refunds                      TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(refunds) AND json_type(refunds) = 'array'),
    status                       TEXT NOT NULL CHECK (status IN ('issued', 'void')),
    subtotal                     INTEGER NOT NULL,
    subtotal_excluding_tax       INTEGER,
    total                        INTEGER NOT NULL,
    total_excluding_tax          INTEGER,
    total_taxes                  TEXT CHECK (total_taxes IS NULL OR (json_valid(total_taxes) AND json_type(total_taxes) = 'array')),
    type                         TEXT NOT NULL CHECK (type IN ('mixed', 'post_payment', 'pre_payment')),
    voided_at                    TEXT,
    CHECK ((status = 'void') = (voided_at IS NOT NULL)),
    CHECK (amount = pre_payment_amount + post_payment_amount)
) STRICT;

CREATE UNIQUE INDEX credit_notes_by_seq  ON credit_notes (x_seq DESC);
CREATE INDEX credit_notes_by_customer ON credit_notes (customer, x_seq DESC);
CREATE INDEX credit_notes_by_invoice  ON credit_notes (invoice, x_seq DESC);

CREATE TABLE customer_balance_transactions (
    id             TEXT PRIMARY KEY,
    x_seq          INTEGER NOT NULL,
    created        TEXT NOT NULL,
    amount         INTEGER NOT NULL,
    credit_note    TEXT REFERENCES credit_notes (id),
    currency       TEXT NOT NULL,
    customer       TEXT NOT NULL REFERENCES customers (id),
    description    TEXT,
    ending_balance INTEGER NOT NULL,
    invoice        TEXT REFERENCES invoices (id),
    metadata       TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    type           TEXT NOT NULL CHECK (type IN (
        'adjustment','applied_to_invoice','checkout_session_subscription_payment',
        'checkout_session_subscription_payment_canceled','credit_note','initial','invoice_overpaid',
        'invoice_too_large','invoice_too_small','migration','unapplied_from_invoice',
        'unspent_receiver_credit'))
) STRICT;

-- There is no top-level list path; the only list is scoped to one customer.
CREATE UNIQUE INDEX cbt_by_seq  ON customer_balance_transactions (x_seq DESC);
CREATE INDEX cbt_by_customer    ON customer_balance_transactions (customer, x_seq DESC);
CREATE INDEX cbt_by_invoice     ON customer_balance_transactions (invoice, x_seq DESC);
CREATE INDEX cbt_by_credit_note ON customer_balance_transactions (credit_note);
```

`credit_notes.customer_balance_transaction` and `customer_balance_transactions.credit_note` are the
third mutually-referential pair; both nullable, same two-step write.

```sql
-- 004_infra.sql

CREATE TABLE events (
    id                      TEXT PRIMARY KEY,
    x_seq                   INTEGER NOT NULL,
    created                 TEXT NOT NULL,
    api_version             TEXT NOT NULL DEFAULT '2026-08-26.dahlia',
    -- {"object": <the full API object as of the change>, "previous_attributes": {...}?}
    -- Its timestamps are Unix seconds: it is a wire snapshot, emitted verbatim (§3.10).
    data                    TEXT NOT NULL CHECK (json_valid(data) AND json_type(data) = 'object'),
    request_id              TEXT,
    request_idempotency_key TEXT,
    type                    TEXT NOT NULL
) STRICT;

CREATE UNIQUE INDEX events_by_seq ON events (x_seq DESC);
CREATE INDEX events_by_type    ON events (type, x_seq DESC);

-- `type` carries NO CHECK. The closed set is 266 values and is not derivable from
-- spec3.json at all (event.type is a bare string by design); it lives in
-- spec/event_types.py and emit_event() raises WorldBug on an unknown type at call time
-- (architecture §6.3). A 266-value CHECK would be an unreadable second copy of a list
-- that is already enforced one layer up, and it would refuse a legitimate value the
-- moment the two drifted. This is the one deliberate exception to "closed set -> CHECK".

CREATE TABLE idempotency_keys (
    key          TEXT NOT NULL,
    method       TEXT NOT NULL CHECK (method IN ('POST', 'DELETE')),
    path         TEXT NOT NULL,
    created      TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    request_id   TEXT NOT NULL,
    status       INTEGER NOT NULL,
    response     TEXT NOT NULL CHECK (json_valid(response) AND json_type(response) = 'object'),
    PRIMARY KEY (key, method, path)
) STRICT;

-- The only mutable counter in the world: one row per listable table for `x_seq`
-- (§3.1 rule 10). `value` is the last number handed out, so the first row of any
-- table gets 1. Seeding static reference rows from a schema file is explicitly
-- allowed (capability-map §Schema) and these INSERTs read no clock.
--
-- invoice.number does NOT draw from here: its sequence is per-customer and lives on
-- customers.next_invoice_sequence, which is a real Stripe field. See §3.13.
CREATE TABLE counters (
    name  TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0 CHECK (value >= 0)
) STRICT;

INSERT INTO counters (name, value) VALUES
    ('customers', 0), ('products', 0), ('prices', 0), ('coupons', 0),
    ('promotion_codes', 0), ('tax_rates', 0), ('payment_methods', 0),
    ('payment_intents', 0), ('charges', 0), ('refunds', 0), ('disputes', 0),
    ('setup_intents', 0), ('balance_transactions', 0), ('payouts', 0),
    ('subscriptions', 0), ('subscription_items', 0), ('subscription_schedules', 0),
    ('invoices', 0), ('invoiceitems', 0), ('credit_notes', 0),
    ('customer_balance_transactions', 0), ('events', 0);
```

`idempotency_keys` and `counters` both go in
`World(untracked_tables=("idempotency_keys", "counters"))`. `idempotency_keys` is infrastructure
(architecture §6.1); `counters` must join it for the same reason and a sharper one — **every insert
bumps a counter, so a tracked `counters` would put a bookkeeping row into the change log of every
graded episode**, and the idempotency eval's "a replay writes nothing" property would be false for a
reason that has nothing to do with idempotency. Both still need their explicit primary key:
capability-map records that a table without one is refused at instance creation (`SH102`) whether it
is tracked or not. Keys never expire — the clock is frozen — which is an allow-list entry, not a
column.

`GET /v1/events?delivery_success=` takes no column: there is no delivery, so the filter matches
everything (`true`) or nothing (`false`), decided in the handler and declared in the allow-list.

### 6. Which reference columns hold a bare id, and which get inflated

The distinction is mechanical from the property's schema and is the rule
`tools_dev/prune_spec.py` must use in place of `x-expandableFields` (see Purpose, correction 3):

| Schema shape | Meaning | Storage | Serialisation |
|---|---|---|---|
| `anyOf[string \| ref:X \| ref:deleted_X]` | an **expandable reference** | `TEXT` id column, FK where the target is one table | bare id string, or the full object when `expand[]` asks |
| `ref:X` or `anyOf[ref:X]` (no `string` member) | an **embedded object**, never a reference | JSON `TEXT` column | always the full object |
| `array<ref:X>` | a list of **always-inflated** objects | ids in a JSON array column, or derived | always full objects, joined from `X`'s table |
| `array<anyOf[string \| ref:X]>` | a list of expandable references | full objects in a JSON array column | ids by default, objects when expanded |

Worked consequences, each of which a naive `x-expandableFields` read gets wrong:

- `customer.address`, `charge.billing_details`, `dispute.evidence`, `line_item.period`,
  `balance_transaction.fee_details`, `promotion_code.restrictions` — embedded. `expand[]=address`
  must be a `400`.
- `customer.discount` is `anyOf[ref:discount]` — embedded, always the full object.
  `subscription.discounts` is `array<anyOf[string|discount]>` — stored as a JSON array of **full**
  discount objects and emitted as `["di_…"]` unless expanded. That is how "unexpanded references
  serialise as the bare id" holds without a `discounts` table.
- `subscription_item.price` is `ref:price` — always the full price object, joined from `prices` on
  every read of that resource, never an id.
- `invoice.default_tax_rates`, `subscription.default_tax_rates`, `subscription_item.tax_rates`,
  `invoiceitem.tax_rates` are `array<ref:tax_rate>` — the column holds `["txr_…"]` and the
  serializer joins `tax_rates`.
- `dispute.balance_transactions` is `array<ref:balance_transaction>` — derived from the ledger, no
  column at all.

### 7. The two Connect-shaped fields that cannot be nulled, and the other constants

Both are `required`, non-nullable objects. Neither gets a column, because neither can vary.

```python
# serialize/fields.py — CONSTANTS, applied after the column map
INVOICE_ISSUER      = {"type": "self"}                 # connect_account_reference, `account` omitted
INVOICE_AUTO_TAX    = {"enabled": False, "disabled_reason": None,
                       "liability": None, "provider": None, "status": None}
SUBSCRIPTION_AUTO_TAX = {"enabled": False, "disabled_reason": None, "liability": None}
```

`connect_account_reference` requires only `type`; its `account` property is neither required nor
nullable, so by the null-versus-absent rule (§3.8) it is **omitted**, not `null` — which is exactly
the degenerate self-owned shape the edges doc calls for. `automatic_tax` differs between the two
owners: `invoice` uses schema `automatic_tax` (5 properties) and `subscription` uses
`subscription_automatic_tax` (3 properties, no `provider`, no `status`, and a one-value
`disabled_reason` enum). Emitting the invoice shape on a subscription would be a schema-conformance
failure, so the two constants are separate.

The other constants, all serializer-side and storage-free:

| API field | Value | Where |
|---|---|---|
| `object` | the `FieldMap.object` | every object |
| `livemode` | `false` | every object **that has the field** — see §3.8 |
| `pending_webhooks` | `0` | `event` |
| `account`, `context` | omitted | `event` |
| `application`, `on_behalf_of`, `application_fee_amount`, `transfer_data`, `transfer_group`, `review` | `null` | wherever the edges doc rules Connect/Radar |
| `default_source` | `null` | `customer`, `invoice`, `subscription` |
| `test_clock` | `null` | `customer`, `subscription`, `invoice`, `invoiceitem`, `subscription_schedule` |
| `managed_payments` | `null` | `payment_intent`, `setup_intent` |
| `cash_balance`, `tax`, `sources`, `tax_ids`, `subscriptions` | omitted | `customer` (the last three are expand-only inline lists) |
| `credit_note.pdf` | `https://pay.stripe.com/credit_notes/{id}/pdf` | derived from the id; a required string with no meaningful value here, declared in the allow-list |
| `invoice.payments` | `{"object": "list", "data": [], "has_more": false, "url": "/v1/invoices/{id}/payments"}` | `invoice_payment` is out of scope; the empty envelope keeps the shape honest |

Two inline lists are **derived live**, not stored, because their contents are still mutable:
`charge.refunds` (from `refunds WHERE charge = …`) and `subscription.items` (from
`subscription_items WHERE subscription = …`). `invoice.lines` and `credit_note.lines` are **frozen
JSON**, because functional spec §3.4 is right that they are written once by the transaction that
writes their parent and never independently mutated. That is the whole difference between the two
treatments and it is worth stating: mutable-after-write means a table; frozen-at-write means JSON.

### 8. The serializer: null versus absent, and `livemode`

Stripe distinguishes a field being `null` from a field being absent, and `spec3.json` encodes the
distinction well enough to decide it mechanically. `serialize/fields.py` computes two frozensets per
resource at import time from `spec3.min.json`:

```
always_present  = set(schema["required"]) | {k for k, v in props.items() if v.get("nullable")}
omit_when_none  = set(props) - always_present
```

and then:

1. a field in `always_present` is **always emitted**; `None` becomes JSON `null`;
2. a field in `omit_when_none` is emitted **only when it has a value**;
3. a field in `FieldMap.constants` follows the constant's own entry — `None` means emit `null`, the
   sentinel `OMIT` means leave the key out.

This is derived, not hand-listed, so it cannot drift from the pinned spec; the cost is that Stripe's
own `nullable` flagging is not perfectly consistent (`refund.description` and
`invoiceitem.frozen_fields` are neither required nor nullable but are plausibly always present on the
wire). Those are exactly the cases a cassette replay settles, and each becomes an
`allowed_differences.py` entry with a reason rather than a guess baked into code.

**`livemode` is not universal.** Functional spec §4 says "`livemode: false` everywhere". Four
in-scope objects have no `livemode` property at all at `2026-08-26.dahlia`:

- `balance_transaction`
- `refund`
- `subscription_item`
- `discount`

(`line_item`, `credit_note_line_item`, `invoiceitem` and `balance` all do have it.) Emitting
`livemode` on those four would fail schema conformance against a strict validator and is a visible
difference from the real API. `FieldMap.constants` therefore carries `livemode` only for the
resources whose schema declares it, and a test asserts that set equals
`{r for r in ROUTED if "livemode" in spec_props(r)}`.

**Unix conversion.** Every column in `FieldMap.timestamps` goes through `_time.to_unix`. The
conversion exists in exactly one place; a test greps the serializers for `fromisoformat` and
`strftime` and fails on a hit, and a second test asserts that no value emitted for a field the spec
types `integer` with `format: unix-time` matches `_time.ISO_PATTERN`.

**`metadata`.** Stored `'{}'` and emitted `{}` on every resource that carries it, including the eight
where the spec marks it `nullable` (`payment_method`, `refund`, `coupon`, `promotion_code`,
`tax_rate`, `setup_intent`, `payout`, `invoice`, `invoiceitem`, `subscription_schedule`,
`credit_note`, `customer_balance_transaction`). `{}` validates against both the nullable and the
non-nullable declaration, and always-a-map is the behavior the real API shows. Update semantics:
`{"k": null}` or `{"k": ""}` removes key `k`; `metadata: null` clears the map to `{}`. Stripe's
limits (≤50 keys, key ≤40 characters, value ≤500 characters) are enforced in `dispatch/params.py`,
not in DDL — a `CHECK` cannot count JSON keys.

### 9. `payment_method_details` and the 57–61 rail unions

Four separate unions in the schema, with the counts read off `spec3.json`:
`payment_method` has 57 rail properties (matching its 57-value `type` enum);
`payment_method_details` has **61 properties — 60 rails plus `type`**, and `type` is its only
`required` one; `payment_intent_payment_method_options` and `refund_destination_details` (37
properties) are the other two.

**One JSON column, not 57 columns.** `payment_methods.rail` holds the sub-object for the row's
`type`; `charges.payment_method_details` holds the whole discriminated object. A column per rail
would add 57 permanently-NULL columns to `payment_methods` and cost the schema hash for nothing;
JSON1 is available, so `rail ->> '$.last4'` is there if a filter ever needs it.

Shapes, per the edges doc's model/stub ruling:

| Rail | `payment_method.rail` | `charge.payment_method_details` |
|---|---|---|
| `card` | full `payment_method_card`: `brand`, `checks`, `country`, `display_brand`, `exp_month`, `exp_year`, `fingerprint`, `funding`, `generated_from`, `last4`, `networks`, `regulated_status`, `three_d_secure_usage`, `wallet` | `{"type": "card", "card": {…payment_method_details_card…}}` |
| `us_bank_account` | full `payment_method_us_bank_account`: `account_holder_type`, `account_type`, `bank_name`, `financial_connections_account` (null), `fingerprint`, `last4`, `networks`, `routing_number`, `status_details` | `{"type": "us_bank_account", "us_bank_account": {…}}` |
| the other 55 | `{}` | `{"type": "<rail>", "<rail>": {}}` |

Note the asymmetry the functional spec's phrasing elides: on `payment_method` the rail key **is** the
`type` and the stub is an empty object under that key; on `payment_method_details` there is a
separate `type` discriminator, so the stub is `{"type": "<rail>", "<rail>": {}}`. Functional spec §4's
"`{"type": "<rail>"}`" is right for the second and incomplete for the first.

Two rail-level doc-only enums the research did not list, both on stored data and both worth a
constraint in `spec/enums.py` (they are inside a JSON column, so the validator, not the DDL, is where
they are checked): `payment_method_card.brand` ∈ `amex, cartes_bancaires, diners, discover,
eftpos_au, jcb, link, mastercard, unionpay, visa, unknown` and `payment_method_card.funding` ∈
`credit, debit, prepaid, unknown`. Both are typed `string` with the set only in the description, and
both are values the magic-card table writes.

### 10. Timestamps inside JSON are Unix seconds

Columns are canonical ISO `TEXT`; **values inside a JSON column are in API form, meaning Unix-second
integers.** `invoice.lines[].period.start`, `credit_note.lines[]`, `subscription_schedule.phases[]`,
`invoice.status_transitions.*`, `discount.start`/`end` and `event.data.object.created` all hold
integers.

The reason: these blobs are written once and emitted verbatim. Converting on read would need a
schema-aware walk of an arbitrary nested document, which is both slow and a second place conversions
can be got wrong. The capability-map rule ("timestamps are TEXT") is about columns — text that sorts
correctly and never touches a clock — and nothing in the schema sorts or filters on a value inside a
blob.

Two consequences, both designed for:

- `fixtures_src/generate.py` must write Unix integers into every nested blob. The fixture invariant
  test walks every JSON column with `json_tree` and asserts no string in it matches
  `_time.ISO_PATTERN`.
- The complementary test — no ISO string in a serialised timestamp field — must therefore run over
  the full response document, including nested lines, not just top-level keys.

### 11. List ordering is `x_seq`, and `invoiceitem` has no `created`

**Why the timestamp cannot carry the ordering.** `(created DESC, id DESC)` is stable — the id
tiebreak guarantees a total order, so cursor pagination never skips or repeats — but it is *not
creation order in this world*. Under a frozen clock every object an agent creates in one episode
shares a single `created`, so the whole sort collapses onto the tiebreak, and the tiebreak is
`ctx.ids.random`. An agent that creates three customers and lists them gets them back in an
arbitrary order where Stripe returns newest-first. Fixture rows hide this completely, because the
generator writes historical timestamps that differ — which is exactly why it would have survived
every ordering test written against a fixture.

So the ordering is a monotonic insert counter, `x_seq`, and it is the same expression on every
table:

```text
... ORDER BY x_seq DESC LIMIT :limit
-- starting_after: AND x_seq < (SELECT x_seq FROM <t> WHERE id = :cursor)
-- ending_before:  AND x_seq > (SELECT x_seq FROM <t> WHERE id = :cursor)  (then reverse)
```

Every listable table has a unique `(x_seq DESC)` index, so that is an index seek plus a backwards
scan of `limit` entries, with no temp b-tree. Composite filter indexes are `(filter, x_seq DESC)`:
the leading equality pins a slice and the rest of the index is already in the requested order. The
DESC is still spelled out rather than left to SQLite's ability to reverse-scan an ASC index —
reverse-scanning works for a bare ordering, but in a mixed `(filter ASC, x_seq DESC)` composite,
being explicit is what makes the index directly usable rather than nearly usable.

**`ResourceSpec.order_column` is dropped.** It existed only because `invoiceitem` has no `created`.
With ordering off the timestamp entirely, every table orders by `x_seq DESC` and the pagination
helper needs no per-resource knob.

**The `invoiceitem` finding still matters for filtering.** `invoiceitem` is the one in-scope resource
with no `created` property: its creation timestamp is `date` (`required`, non-nullable), and
`GET /v1/invoiceitems` nevertheless takes a `created` range filter. The column stays `date`, and
`dispatch/params.py` maps the `created` filter onto it. That mapping is now the whole of the
exception — it no longer leaks into the ordering.

**Range filters take no index of their own.** Every list accepts a `created` range, and `payouts`
and `invoices` additionally accept `arrival_date` and `due_date`. All of these are applied as a
predicate during the `x_seq DESC` scan rather than served by their own index, because an index on the
timestamp would deliver rows in the wrong order and force a sort. The cost is a scan proportional to
how far back the filter reaches: `?created[gte]=<recent>` with `limit=10` touches ~10 rows, while
`?created[lte]=<old>` walks the table. At `large`'s scale that is acceptable and it is recorded here
rather than discovered; if a profile ever says otherwise, the fix is a covering
`(created, x_seq DESC)` index on the one table that needs it, not a change to the ordering.

Two related facts for the same helper: `subscription_items` has `created` but its list endpoint
**requires** `subscription`, and `customer_balance_transactions` has no top-level list path at all.
Both still carry `x_seq` and a unique seq index — the scoped list needs the same total order, and the
uniqueness is what makes a cursor resolvable.

### 12. Where the enum `CHECK`s come from

`spec/enums.py` is generated by `tools_dev/prune_spec.py` in two passes:

1. **Typed enums** — every property with an `enum` key on an in-scope schema, taken verbatim.
2. **Doc-only enums** — properties typed as bare `string` whose description closes the set. The
   pruner does not guess: it carries an explicit table of `(schema, property) -> [values]` with the
   description sentence quoted beside each, and it **asserts at generation time** that every quoted
   value still appears inside the live description, so a Stripe wording change fails the regeneration
   test rather than silently dropping a value.

The six the brief names, with the exact description each set is read from:

| Field | Spec type | Values | Description phrase |
|---|---|---|---|
| `dispute.reason` | `string`, REQ | `bank_cannot_process, check_returned, credit_not_processed, customer_initiated, debit_not_authorized, duplicate, fraudulent, general, incorrect_account_details, insufficient_funds, noncompliant, product_not_received, product_unacceptable, subscription_canceled, unrecognized` | "Possible values are …" |
| `payout.status` | `string`, REQ | `paid, pending, in_transit, canceled, failed` | "Current status of the payout: …" |
| `payout.method` | `string`, REQ | `standard, instant` | "which can be `standard` or `instant`" |
| `payout.source_type` | `string`, REQ | `card, fpx, bank_account` | "can be one of the following: …" |
| `refund.status` | `string`, nullable | `pending, requires_action, succeeded, failed, canceled` | "This can be …" |
| `balance_transaction.status` | `string`, REQ | `available, pending` | "which are either `available` or `pending`" |
| `setup_intent.usage` | `string`, REQ | `on_session, off_session` | "Use `on_session` if … Use `off_session` if …", default `off_session` |

Each of the seven rows above becomes a `CHECK (col IN (…))` in the DDL, written out above. **Note
that is seven fields, not six** — the brief and functional spec §4 count `payout.status`/`method`/
`source_type` as one entry; there are three distinct `payout` fields, so the total is seven, from six
resources.

**Two more that the count misses**, both on data this world stores: `charge_outcome.type`
(`authorized, manual_review, issuer_declined, blocked, invalid`) and `fee.type`
(`application_fee, payment_method_passthrough_fee, stripe_fee, tax, withheld_tax`) — plus the two
card-rail enums in §3.9. All four live inside JSON columns (`charges.outcome`,
`balance_transactions.fee_details`, `payment_methods.rail`), so they belong in `enums.py` and in the
schema-conformance validator, but they cannot be DDL `CHECK`s.

**`invoice.status` is not one of them.** The research inventory records it as "typed as a nullable
bare `string`, but the description enumerates a closed set". At `2026-08-26.dahlia` it is a genuine
`enum(draft, open, paid, uncollectible, void)` that happens to be nullable. It gets a normal typed
`CHECK`, and the inventory's note should be corrected.

**One honest wrinkle.** Architecture §4.1 says the `CHECK` sets are "taken from `spec/enums.py` so the
schema and the conformance validator cannot disagree". They can: `.sql` files are static text and
cannot import Python. The sets are genuinely duplicated. The guard is a test, not a mechanism:

```
test_schema_enums_match_spec — open a blank instance, read every `sql` from sqlite_master,
regex every `CHECK (<col> IN (...))` out of it, and assert the extracted
{table: {column: frozenset}} equals enums.SCHEMA_ENUMS. Fails on a value added on
either side.
```

That test is cheap and it is the only thing standing between the two copies, so it is named in the
test plan rather than left implied.

### 13. Where the invoice-number sequence lives

Nobody owned this: `fixtures.md` needs to hand a counter to the tools, and `billing_engine.md` treats
the number as frozen at finalization without saying where it comes from. It is a schema question.

**It is per-customer, and it is already in the schema.** Stripe's own model is two fields on the
customer, both of which this design already carries because they are real API fields:

- `customers.invoice_prefix` — `TEXT NOT NULL`, an 8-character uppercase alphanumeric token minted
  from `ctx.ids.random` at customer creation, or supplied by the caller;
- `customers.next_invoice_sequence` — `INTEGER NOT NULL DEFAULT 1`, settable by the caller on create
  and update.

At finalization, `billing/invoicing.py` reads the pair, writes
`number = f"{invoice_prefix}-{next_invoice_sequence:04d}"` onto the invoice, and increments
`next_invoice_sequence` on the customer row. Nothing else touches either field.

**Why not a `counters` row.** A global or per-account counter could not honour
`POST /v1/customers {"next_invoice_sequence": 42}`, which is a real parameter on a real endpoint; the
sequence has to be addressable per customer because Stripe exposes it per customer. Putting it in
`counters` would mean either ignoring that parameter or keeping two sources of truth for one number.
The `counters` table exists for `x_seq`, which has no API surface at all, and that is the whole
difference between the two.

**Uniqueness.** `{prefix}-{seq}` is unique exactly as long as prefixes are, so:

```text
-- declared in 001_core.sql and 003_billing.sql respectively; quoted here for the argument
CREATE UNIQUE INDEX customers_invoice_prefix ON customers (invoice_prefix);
CREATE UNIQUE INDEX invoices_number ON invoices (number) WHERE number IS NOT NULL;
```

The partial predicate is what lets draft invoices coexist: `number` is `NULL` until finalization
(the table-level `CHECK (number IS NOT NULL OR status = 'draft')` is the other half of that rule),
and SQLite's `UNIQUE` would otherwise be satisfied by any number of NULLs but tell us nothing. A
tombstoned customer keeps its row and therefore keeps its prefix reserved, which matches Stripe:
deleting a customer does not free its invoice numbers.

**Per-account or global?** The world models exactly one account (functional spec §2.6 — the account
is static within an instance), so "per-account" and "global within the instance" are the same scope
here, and the sequence is per-customer *within* that one account. If the account ever becomes
configurable at reset, nothing about this changes: the prefix is still the per-customer
discriminator.

**Across composed worlds.** Under composition each node gets its own namespace and its own store, so
two hosts wrapping StripeAPI get two independent `customers` tables and two independent sequences.
They are two Stripe accounts, which is the faithful answer — invoice numbers are per-account in
reality and nothing about a host's wrapper makes two accounts into one. There is no shared counter to
coordinate, which is fortunate, because architecture §12 records that there is **no cross-world
atomicity** to coordinate it with: a sequence spanning two nodes could not be incremented safely.

If a host did point two nodes at one store, the two unique indexes above are what makes it fail
loudly — the second customer to claim a prefix is rejected at insert — rather than quietly minting
two invoices numbered `ABCD1234-0001`. That is the intended behavior and it is why the uniqueness is
declared in DDL rather than checked in a handler.

**Credit-note numbers need no counter.** `credit_note.number` is
`f"{invoice.number}-CN-{n}"` where `n` is `SELECT COUNT(*) FROM credit_notes WHERE invoice = ?` plus
one. Credit notes are never deleted — `void` keeps the row (§3.5's
`CHECK ((status = 'void') = (voided_at IS NOT NULL))`) — so the count is monotonic and the derivation
cannot collide. Deriving beats a column here for the same reason `dispute.balance_transactions` is
derived: there is no second copy to drift.

## Dependencies

**This component depends on:**

- `research/stripe-openapi/spec3.json` at `2026-08-26.dahlia`, through `spec/spec3.min.json` and the
  generated `spec/enums.py` — for every field name, type, nullability, `required` membership and enum
  set. `tools_dev/prune_spec.py` is the only reader of the full file.
- `research/repos/stripe-mock/embedded/openapi/fixtures3.json` — for the id prefixes in §3.2, since
  `spec3.json` has no examples. This is a build-time reference, not a runtime dependency; the prefix
  table is committed in `_ids.py`.
- `seahaven.sql_files(__package__, "schema")` and Seahaven's connection setup (`foreign_keys = ON`,
  the frozen-clock overrides). World code never sets a pragma.
- `ctx.ids.random` for id minting and `ctx.clock.iso()` for every timestamp.

**Depends on this component:**

- `dispatch/resource.py` — the `ResourceSpec` engine reads `table`, `id_prefix` and the list-filter
  columns declared here, and orders every list by `x_seq DESC`. It no longer needs an
  `order_column` (§3.11).
- `cross_cutting.md`'s `PageOrder` — which must spell the column `x_seq`, not `seq` (§3.1.1).
- `billing/invoicing.py` — for `customers.invoice_prefix` / `next_invoice_sequence` (§3.13).
- `serialize/expand.py` — the id-versus-object decision in §3.6 is what tells it which columns are
  references.
- every module in `resources/` and `billing/` — they write these rows.
- `fixtures_src/generate.py` — and through it all three fixtures, which is why this document should
  be settled before `large` is built (architecture §12).
- `tests/conformance/` schema validation.

## Test Plan

Named tests, all through `instance.call(...)` or `inst.inspect()` unless stated.

**Schema shape**
- `test_schema_executes` — `World()` constructs, which means the four files applied cleanly to a blank database.
- `test_every_table_is_strict_with_pk` — `sqlite_master` rows all end in `STRICT` and declare a primary key. (Duplicates a `seahaven check` lint deliberately, so it fails in pytest too.)
- `test_no_wall_clock_in_ddl` — no `CURRENT_TIMESTAMP`, `datetime('now')`, `date('now')` or `unixepoch()` anywhere in `sqlite_master.sql`.
- `test_table_count_is_24` — 21 Stripe tables plus `events`, `idempotency_keys` and `counters`, named explicitly, so an added table is a decision rather than an accident.
- `test_column_names_match_spec_fields` — for every `FieldMap`, every non-`x_` column is either a spec property name of that resource or one of the seven declared flattenings.
- `test_every_listable_table_has_seq` — each of the 22 listable tables has `x_seq INTEGER NOT NULL`, a unique `(x_seq DESC)` index, and a seeded `counters` row; `idempotency_keys` and `counters` have none.
- `test_schema_enums_match_spec` — §3.12's `CHECK`-versus-`enums.py` comparison.
- `test_doc_only_enum_values_still_in_descriptions` — regenerating the pruner's doc-only table against the live `spec3.json` descriptions; guards the seven fields in §3.12 plus the four in §3.9.

**Ids**
- `test_every_prefix_minted` — one object of each resource created through the tools; each id matches `^{prefix}[A-Za-z0-9]{24}$`.
- `test_coupon_id_is_unprefixed` — a caller-supplied id round-trips; an omitted one is eight uppercase alphanumerics.
- `test_no_uuid_in_resource_modules` — greps `resources/` and `billing/` for `ctx.ids.uuid(` (architecture §4.4).
- `test_fixture_ids_match_prefixes` — over all three fixtures, every primary key in every table matches its resource's prefix.
- `test_ids_are_deterministic` — same fixture and seed, same ids.

**JSON columns**
- `test_json_columns_reject_non_json` — a direct insert of `'not json'` into each JSON column raises.
- `test_json_columns_reject_wrong_type` — `'[]'` into an object column and `'{}'` into an array column both raise, proving the `json_type` half of the `CHECK` earns its place.
- `test_json_dump_is_canonical` — `_json.dumps` output is byte-identical across two runs and has sorted keys, no spaces.
- `test_no_iso_timestamps_inside_json` — `json_tree` over every JSON column in every fixture; no string matches `_time.ISO_PATTERN` (§3.10).

**Serializer**
- `test_timestamps_are_unix_seconds` — every field the spec types `format: unix-time` comes back an `int`, at top level and nested.
- `test_object_discriminator` — one retrieve per resource; `object` equals the spec's `properties.object.enum[0]`.
- `test_livemode_only_where_declared` — `livemode` is `false` on every resource whose schema declares it and **absent** on `balance_transaction`, `refund`, `subscription_item` and `discount` (§3.8).
- `test_required_fields_always_present` — every `required` property appears on every serialised object.
- `test_nullable_fields_present_as_null` — every `nullable` property appears, `null` where empty.
- `test_optional_fields_omitted_when_empty` — a non-card `payment_method` has no `card` key; a `subscription_item` without a quantity has no `quantity` key.
- `test_invoice_issuer_is_self` — `{"type": "self"}`, with no `account` key.
- `test_automatic_tax_shapes_differ` — invoice's has `provider`/`status`; subscription's does not.
- `test_no_top_level_invoice_subscription` — a subscription invoice has no `subscription` and no `days_until_due` key; the id is at `parent.subscription_details.subscription`.
- `test_rail_stub_shapes` — a `klarna` payment method serialises `{"klarna": {}}`; its charge serialises `payment_method_details == {"type": "klarna", "klarna": {}}`; a card charge carries the full card block.
- `test_discounts_serialise_as_ids` — `subscription.discounts` is `["di_…"]` unexpanded and full objects under `expand[]=discounts`.
- `test_always_inflated_references` — `subscription_item.price` and `invoice.default_tax_rates` are full objects with no `expand[]`.
- `test_dispute_balance_transactions_derived` — the array equals the ledger rows whose `source` is the dispute, in `x_seq` order.
- `test_decimal_fields_round_trip` — `percent_off: 33.33` stored and returned as the JSON number `33.33`, and `Decimal("33.33")` inside the engine.
- `test_deleted_stub_shape` — `DELETE /v1/customers/{id}` returns exactly three keys; a subsequent retrieve returns the same stub; the customer is gone from `GET /v1/customers`.

**Indexes and pagination**
- `test_list_order_uses_index` — `EXPLAIN QUERY PLAN` for each resource's default list shows the `_by_created` index and no `TEMP B-TREE`.
- `test_filtered_list_uses_index` — same for each declared list filter.
- `test_invoiceitems_created_filter_maps_to_date` — `GET /v1/invoiceitems` is ordered by `x_seq DESC` like everything else, and `?created[gte]=` filters the `date` column (§3.11).
- `test_frozen_clock_list_is_creation_order` — create five customers in one episode through the tools, list them, and assert the order is the exact reverse of creation. This is the test that `(created DESC, id DESC)` would have failed and that no fixture-based test could have caught, because every row shares one `created` and the old tiebreak sorted on a random id.
- `test_pagination_stable_under_identical_created` — 100 objects created in one instance paginate in 10-object pages with no repeat and no omission.
- `test_seq_never_reused_after_delete` — create three draft invoices, hard-delete the newest, create a fourth; its `x_seq` is strictly greater than the deleted one's, and a cursor naming the surviving second invoice still pages correctly.
- `test_seq_matches_timeline_in_fixtures` — over all three fixtures, ordering each table by `x_seq` gives the same sequence as ordering by its timestamp column. This is the guard on the generator's insert order (§3.1.2).
- `test_pending_invoiceitems_partial_index` — `?pending=true` uses `invoiceitems_pending`.

**Foreign keys and invariants** (written as SQL over `inst.inspect()`, reusable as eval rewards)
- `test_circular_fk_write_order` — product+price, charge+payment_intent, credit_note+customer_balance_transaction each create cleanly with foreign keys on.
- `test_balance_transaction_net` — `net = amount - fee` on every row (also a DDL `CHECK`; the test proves the `CHECK` is reachable).
- `test_balance_transaction_source_resolves` — every non-null `source` resolves in exactly one of `charges`, `refunds`, `disputes`, `payouts`.
- `test_no_over_refund` — `amount_refunded <= amount_captured` on every charge.
- `test_invoice_lines_sum_to_subtotal` — `json_each(lines)` amounts sum to `subtotal`.
- `test_subscription_item_periods_never_overlap` — per subscription item, successive periods abut.
- `test_customer_balance_matches_ledger` — `customers.balance` equals the latest `customer_balance_transactions.ending_balance`, and the ledger's `amount`s sum to it.
- `test_no_x_columns_serialised` — no `FieldMap` maps an `x_`-prefixed column, `x_seq` included, and no list filter resolves to one.
- `test_counters_untracked` — a create through the tools produces change-log records for the resource row and nothing for `counters`; a replayed idempotent write produces none at all.
- `test_invoice_number_sequence` — two invoices finalized for one customer are `{prefix}-0001` and `{prefix}-0002`; a third for a different customer starts again at `-0001` under a different prefix; `next_invoice_sequence` supplied at customer creation is honoured.
- `test_invoice_prefix_unique` — a second customer created with an existing `invoice_prefix` is rejected.
- `test_draft_invoice_has_no_number` — `number` is null in `draft` and non-null from `open` onward; two drafts coexist under the partial unique index.
- `test_credit_note_numbering` — three credit notes against one invoice are `-CN-1`, `-CN-2`, `-CN-3`, and voiding the second does not renumber the third.

**Schema-hash discipline**
- `test_schema_hash_pinned` — the hash of the applied schema equals a committed constant. Changing it is a deliberate edit to that constant plus a fixture regeneration, which is what architecture §12 asks for and what makes the cost visible in a diff.

## Open questions

1. **`balance_transaction.reporting_category` has no closed set anywhere in the spec** — the
   description only links to a docs page, and the inventory already flags it as a gap. It is `NOT
   NULL` with no `CHECK`. The world will write `charge`, `refund`, `dispute`, `payout`, `fee` and
   `payout_reversal`; whether those strings are exactly right is unverified and goes to a cassette.
2. **Retrieve-after-delete for `product` and `coupon` — settled by Phase 7's recording.** The
   tombstone decision in §3.3 is grounded in the `deleted_product` / `deleted_coupon` union members
   existing, and the recording settles what a subsequent retrieve answers: a **404**
   `resource_missing` naming the path id — for both products and coupons, while a deleted *customer*
   retrieves as the three-key stub (probed both ways, Phase 7). The split is carried by
   `DeleteSpec.deleted_retrieve`; the delete-event snapshots also carry the flipped column
   (`product.active: false`, `coupon.valid: false`), which is why both deletes zero a column before
   the snapshot row is re-read.
3. **`invoiceitem.quantity_decimal` is `required` and non-nullable but has no documented default.**
    `'1'` is the chosen default; a cassette should confirm Stripe does not return `'1.0'` or a
    higher-precision form.
