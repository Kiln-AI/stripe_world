# Minimum Closed Set and the 15–20 Table / 40–60 Tool Budget

Answers project_overview.md §5 Q1 ("what is the minimum closed set of objects that makes the in-scope
slice coherent") and sizes it against §4's target ("on the order of 15–20 tables and 40–60 tools").
**Headline finding: the honest operation count for the resources project_overview.md §4 names, taken
at face value including every documented sub-resource, is 187 HTTP operations — 3–4.7x the 40–60 tool
budget. Getting into budget requires deliberate, resource-by-resource cuts, not just "don't add
Connect."**

## The raw count

Counted directly from `spec3.json` `paths`, restricted to the 21 resource-root prefixes
project_overview.md §4 names (`/v1/customers`, `/v1/payment_methods`, `/v1/products`, `/v1/prices`,
`/v1/coupons`, `/v1/promotion_codes`, `/v1/tax_rates`, `/v1/payment_intents`, `/v1/charges`,
`/v1/refunds`, `/v1/disputes`, `/v1/setup_intents`, `/v1/balance_transactions`, `/v1/payouts`,
`/v1/balance`, `/v1/subscriptions`, `/v1/subscription_items`, `/v1/invoices`, `/v1/invoiceitems`,
`/v1/credit_notes`, `/v1/subscription_schedules`, `/v1/events`) — every path under each prefix, every
`get`/`post`/`delete` method:

| Resource | Ops | Resource | Ops |
|---|---|---|---|
| `customers` (incl. all sub-resources) | **47** | `payment_methods` | 6 |
| `invoices` | **18** | `payouts` | 6 |
| `charges` | 14 | `subscription_schedules` | 6 |
| `payment_intents` | 12 | `coupons` | 5 |
| `products` | 10 | `invoiceitems` | 5 |
| `subscriptions` | 9 | `prices` | 5 |
| `credit_notes` | 8 | `refunds` | 5 |
| `setup_intents` | 7 | `subscription_items` | 5 |
| | | `disputes` | 4 |
| | | `promotion_codes` | 4 |
| | | `tax_rates` | 4 |
| | | `balance` | 3 |
| | | `balance_transactions` | 2 |
| | | `events` | 2 |
| **Total** | | | **187** |

`customers` alone is 47 operations because it carries seven sub-resource families in the spec:
`balance_transactions`, `bank_accounts`, `cards`, `cash_balance`/`cash_balance_transactions`,
`discount`, `funding_instructions`, `payment_methods` (read-only alias), `sources`, `subscriptions`
(legacy alias), `tax_ids`. Most of these are ruled out of scope entirely in
[scope-boundary-edges.md](./scope-boundary-edges.md) (legacy Sources/Cards/bank_accounts, Cash
Balance, Tax IDs, funding instructions) — after those cuts `customers` drops to roughly 8 operations
(list, create, retrieve, update, delete, search, plus `customer_balance_transactions` list/create
since that ledger stays in scope).

Stripping every legacy-alias / sub-resource / `search` operation (39 ops: `bank_accounts`, `cards`,
`sources`, `cash_balance*`, `tax_ids`, `funding_instructions`, `features`, all four `search` endpoints,
`balance/history`) only gets the total to **148** — still roughly 2.5–3.7x budget. The rest of the
gap is structural: every payment object has 1–3 state-transition actions (`capture`, `confirm`,
`cancel`, `void`, `finalize`, `pay`, `send`, `close`, `release`, `reverse`, `resume`, `attach`,
`detach`, `migrate`…) that are each a real, distinct tool if you want one-tool-per-operation fidelity
(subtopic 5's territory — the tool-shape recommendation interacts directly with this budget: a single
generic `stripe_request(method, path, params)` escape hatch collapses this whole table to ~1 tool, at
the cost of schema-guided tool selection for the agent).

## What closes the object graph (the "minimum closed set")

A resource is in the **closed set** if it is one of project_overview.md §4's named resources, or if an
in-scope resource cannot be made coherent without it. Applying that test against the field-level
`$ref` graph in `resource-inventory.md`:

**In the closed set (21 candidate resources, but not 21 tables — see below):**

`customer`, `payment_method`, `product`, `price`, `coupon`, `promotion_code`, `tax_rate`, `discount`
(embedded, not top-level CRUD), `payment_intent`, `charge`, `refund`, `dispute`, `setup_intent`,
`balance_transaction`, `payout`, `balance` (derived view), `subscription`, `subscription_item`,
`invoice`, `invoiceitem`, `line_item` (derived), `credit_note`, `credit_note_line_item` (derived),
`event`, `customer_balance_transaction`.

`customer_balance_transaction` is the one addition beyond project_overview.md §4's literal resource
list this subtopic recommends pulling in explicitly: `invoice.starting_balance` /
`invoice.ending_balance` and credit-note-to-account-balance application are meaningless without it —
it's the ledger for `customer.balance`, which §4 already implies by listing `customers` with balance
semantics referenced throughout invoicing. Without it, "apply a credit note to account balance" has
nothing to write a row into, and "why is this invoice's `starting_balance` nonzero" has no answer.
`subscription_schedule` stays **conditional** per §4; see the note in resource-inventory.md and
subtopic 3's cost/benefit call.

**Tables vs. views — the count that actually matters for the 15–20 budget:**

Not every object in the closed set needs its own SQL table:

- **`balance`** has no `id`, is not listable, and its two numbers (`available`, `pending`) are
  arithmetically derivable from `balance_transaction.available_on` vs. the current instance clock. It
  should be a computed read, not a table.
- **`line_item`** and **`credit_note_line_item`** are never independently mutated — they're produced
  once (at invoice finalization / credit note issuance) from `invoiceitem`/`subscription_item` state
  and then frozen. Storing them as a JSON array column on the parent `invoice`/`credit_note` row
  (matching how the API already nests them under `lines: {data: [...]}`) avoids two tables whose only
  write path is "written once by the same transaction that writes the parent." This is exactly the
  "deep JSON objects" pressure point project_overview.md §12 assigns to subtopic 6 (Seahaven
  capabilities) to rule on — flagging it here as a schema-level fact subtopic 6 needs: **the OpenAPI
  spec itself already models these as nested, not flat, so a flat-row Seahaven convention would be
  fighting the source of truth, not simplifying it.**
- **`discount`** likewise has no independent list/create endpoint — it's always embedded (on
  `customer`, `subscription`, `subscription_item`, `invoiceitem`, `invoice`, `line_item`) or reached
  via a nested `GET`/`DELETE`. A `discounts` table keyed by `(owner_type, owner_id)` is defensible if
  multiple owners must query it uniformly; a JSON column per owner is defensible too. Either way it is
  not one of the "big" 15–20 tables.

That leaves roughly **18–19 real tables**: `customer`, `payment_method`, `product`, `price`, `coupon`,
`promotion_code`, `tax_rate`, `payment_intent`, `charge`, `refund`, `dispute`, `setup_intent`,
`balance_transaction`, `payout`, `subscription`, `subscription_item`, `invoice`, `invoiceitem`,
`credit_note`, `customer_balance_transaction`, `event` (that's 21 minus `balance`/`line_item`/
`credit_note_line_item`/`discount` collapsed into views or JSON = **17**, or **19** if `discount` gets
its own table and `subscription_schedule` is included) — **this fits the 15–20 table budget
comfortably, and is the strongest evidence that the budget is achievable, provided the "derived,
frozen, nested" objects are built as views/JSON rather than tables.** This is the one part of the
budget question where the honest count does *not* exceed target — table count is fine; **operation
(tool) count is the part that blows through it.**

## The tool count does not fit without real cuts

Even after collapsing views, a naive "one tool per HTTP operation" mapping over the trimmed 148-op
count, further trimmed by removing operations for objects that don't get their own tool (`balance`
collapses to 1 read tool; `line_item`/`credit_note_line_item` collapse into their parent's read/action
tools; `discount` collapses into 1–2 generic apply/remove tools), lands in the neighborhood of
**60–75 tools** by this subtopic's estimate — still above the top of the 40–60 range. Getting inside
budget requires choices outside this subtopic's mandate (tool granularity is subtopic 5's call), but
the schema-level facts that should drive that decision are:

1. **Cut `search` endpoints** (7 ops in the in-scope set: `customers/search`, `prices/search`,
   `products/search`, `payment_intents/search`, `invoices/search`, `charges/search`,
   `subscriptions/search`) as a first pass; `list` with filters covers the same fixture-scale need and
   search's Lucene-like query language is a second parser to build for marginal value in a synthetic
   world with bounded fixture size.
2. **Cut every legacy alias path** — `/v1/customers/{customer}/subscriptions*`,
   `/v1/customers/{customer}/bank_accounts*`, `/v1/customers/{customer}/cards*`,
   `/v1/customers/{customer}/sources*`, `/v1/charges/{charge}/refund` (singular, superseded by the
   plural `/v1/refunds` collection), `/v1/balance/history*` (superseded by `/v1/balance_transactions`)
   — these exist in the real API only for backward compatibility with pre-2019 integrations and add
   zero new object-graph coverage.
3. **Collapse `products/{product}/features`** (3 ops) into the `product.marketing_features` array
   field, edited via `product` update, rather than a child-resource CRUD surface — it's a list of
   `{name}` strings with no independent lifecycle worth modeling as separate tools.
4. **Decide `subscription_schedules`' fate as a unit** (6 ops) — subtopic 3's call, but note it here
   since cutting it is worth roughly 6 tools and a table.
5. Even after 1–4, the state-transition-action tools (`capture`, `confirm`, `cancel`, `void`,
   `finalize`, `pay`, `send`, `close`, `release`, `reverse`, `resume`, `attach`, `detach`) number
   around 20 on their own and are exactly the fidelity-bearing operations the project's whole thesis
   (§2: "a botched proration is invisible in a transcript and obvious in the change log") depends on —
   **these are the wrong place to cut.** If the budget still doesn't fit after 1–4, the remaining lever
   is tool *granularity* (one generic `stripe_request`-style dispatcher, or grouping e.g. all invoice
   line-management actions behind one parameterized tool) rather than cutting fidelity-bearing
   state machines. That trade-off is explicitly subtopic 5's to make (project_overview.md §12.1).

## Bottom line for the functional spec

- Table count target (15–20) is achievable and this subtopic's recommended closed set fits it, given
  the view/JSON collapses above.
- Tool count target (40–60) is **not** achievable with naive one-tool-per-operation over the full
  named resource list, even after cutting every clearly-legacy and clearly-out-of-scope path. The
  functional spec needs to either accept a tool count nearer 60–75, or adopt tool-grouping /
  generic-dispatcher patterns from subtopic 5's recommendation, or make an explicit further scope cut
  (e.g. drop `subscription_schedules` and one of `disputes`/`credit_notes`) and say so out loud, per
  project_overview.md §4's own instruction: **"If the design comes in much bigger than that, the scope
  is wrong and we should cut before building."**
