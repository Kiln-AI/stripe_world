# StripeAPI: technical reference

The [README](../README.md) is the pitch. This page is the detail behind it: the agent surface, what
the world covers, how conformance is checked, the conventions the code keeps, and how the repository
is laid out. Coding agents start at [AGENTS.md](../AGENTS.md).

StripeAPI is a Seahaven world: a faithful, stateful, forkable replica of Stripe's Billing and
Payments core. An agent inside it makes Stripe API calls against SQLite-backed state that responds
the way Stripe does -- objects with real ids, money that moves through a ledger, subscriptions with
real statuses, invoices that finalize and get paid -- without touching Stripe. Built for rollouts:
many in parallel, each forked from a known fixture in milliseconds, every changed row recoverable.

**Not affiliated with Stripe.** Stripe's field names, enum values, error codes and id prefixes are
functional API vocabulary under the source material's MIT licence (see
[`THIRD_PARTY_LICENSES.md`](../THIRD_PARTY_LICENSES.md)).

## The agent surface

The world exposes eight of Stripe's own MCP server's ten tools, addressed by `operationId` with
`stripe_context` and `livemode` context parameters on every call. An agent trained against this
world works against the real thing without relearning.

| Tool | Purpose |
|---|---|
| `list_available_accounts_or_orgs()` | Bootstrap: returns the accounts this session can reach, with `stripe_context` and `livemode` for every subsequent call |
| `stripe_api_search(intent, resource)` | Find Stripe API operations by intent and resource (the operation catalogue, not object search) |
| `stripe_api_details(stripe_api_operation_id)` | Full parameter documentation for one API operation |
| `stripe_api_read(stripe_api_operation_id, parameters)` | Read data with any Stripe API `GET` operation |
| `stripe_api_write(stripe_api_operation_id, parameters)` | Write data with any `POST` or `DELETE` operation |
| `get_stripe_account_info()` | The account object |
| `manage_stripe_accounts()` | Returns a URL for account management |
| `stripe_analytics(intent)` | Sigma/analytics -- answers a permission refusal (the world does not reproduce Sigma) |

On success a tool returns the bare Stripe object or list envelope. On failure it raises an MCP
tool error carrying a plain-text message -- an agent never sees a status code, response headers,
or a structured error envelope. The MCP handshake names the server `stripe-mcp`, Stripe's own
server name, so the agent reads the product and not the replica; every place a person looks says
Seahaven.

### Composition requirement

Three of Stripe's ten MCP tools are deliberately not built here:
`search_stripe_documentation`, `stripe_implementation_planner`, and `send_stripe_mcp_feedback`.
A harness that wants the complete ten-tool set composes these from the real Stripe MCP server
alongside this world's eight (see `specs/projects/no_tells/functional_spec.md` §4.1.2--4.1.3).
**This world is not a standalone drop-in for the Stripe MCP server** and should not be deployed
as one.

## Stripe's HTTP API

The same world also speaks Stripe's REST API, so the Stripe SDKs work against it without changes.

```bash
uv run serve_http.py --reset-options '{"startup": {"livemode": false}}'
```

`serve_http.py` is `seahaven.http.main(world, handle)`. Every `/worlds/<id>` is its own instance
with its own data, created the first time a request uses that id; `PUT /worlds/<id>` replaces it
with a fresh one and `DELETE /worlds/<id>` destroys it. With no options an instance is blank (the
same rows as the `empty` fixture) and in live mode, the world's default; the `--reset-options`
above makes every instance a test-mode account. `uv run serve_http.py --help` lists every option
(`--host`, `--port`, `--max-instances`, `--fixture`, `--seed`, `--now`, `--clock-mode`,
`--reset-options`), and Seahaven's
[`http_apis.md`](https://github.com/Kiln-AI/Seahaven/blob/main/src/seahaven/docs/http_apis.md)
describes the server.

The handler lives in `src/seahaven_stripe_world/http_api/`: `wire.py` decodes form bodies and query
strings (bracket notation) and coerces their strings by each operation's `Param`s; `handler.py`
checks for a key (any key; none is Stripe's 401), honours `Idempotency-Key` through
`middleware/idempotency.replay_or_run`, runs `dispatch` in a savepoint so a raised `StripeApiError`
loses only that request's writes, and answers the bare object or the error envelope with
`Request-Id` and `Stripe-Version` headers. The HTTP face is additive: it changes no tool behaviour
and no world default.

## What it covers

**155 routed operations** across 24 SQLite tables, pinned to Stripe API version
`2026-08-26.dahlia`. The operation count is asserted by a test and cannot drift silently.
Discovery covers the 123 operations the real Stripe MCP catalogues (72 routed, 51 catalogued but
unrouted, which answer a product-activation or permission refusal when called); the remaining 471
operations in spec3.json are absent on the real MCP and answer "not available" when addressed
directly.

The two faces reach different sets. Stripe's HTTP API answers all 155 routed operations, so an SDK
can create payment methods and confirm payment intents. The MCP tools reach the 72 routed
operations the real Stripe MCP catalogues, as the real server does: an agent cannot, for example,
create a payment method through `stripe_api_write`, and nor can it on Stripe's own server.

### Resources

`customers`, `payment_methods`, `products`, `prices`, `coupons`, `promotion_codes`, `tax_rates`,
`payment_intents`, `charges`, `refunds`, `disputes`, `setup_intents`, `balance`,
`balance_transactions`, `payouts`, `subscriptions`, `subscription_items`,
`subscription_schedules`, `invoices`, `invoiceitems`, `credit_notes`,
`customer_balance_transactions`, `events`.

### Billing engine

The behavior that lives in prose and observed behavior rather than the OpenAPI spec:

- **Subscription lifecycle** -- all eight statuses, every transition, trials, `cancel_at_period_end`
  versus immediate cancel, pause, `payment_behavior`, incomplete expiry.
- **Invoice status machine** -- `draft` to `open` to `paid`/`void`/`uncollectible`, with
  `auto_advance`, `collection_method`, `billing_reason`, and `automatically_finalizes_at`.
- **Proration** -- credit and debit lines, each rounded independently. The half-cent tie-break
  (floor) is settled by a live recording against Stripe's API.
- **Dunning** -- retry configuration envelope, three end-of-schedule outcomes, nine hard-decline
  codes. No invented retry schedule: Smart Retries is ML-scheduled.
- **Credit notes** -- the three-channel settlement model (refund, customer balance, out-of-band).
- **Balance ledger** -- a `balance_transaction` for every money movement, `net = amount - fee`,
  `available` versus `pending` with `available_on`, payout draw-down.

### Search

Seven `/v1/*/search` endpoints (customers, products, prices, invoices, charges, payment intents,
subscriptions) with FTS5-backed query execution, per-resource field allowlists, and
`page`/`next_page` pagination.

### Cross-cutting

- **Idempotency** -- key-scoped, with same-params replay, different-params rejection, and error
  caching. A replay writes nothing to the change log.
- **Pagination** -- cursor-based, reverse-chronological, `limit` 1-100 default 10.
- **Expansion** -- `expand[]` on retrieve, list, create and update, with depth limits and the
  `data.` prefix for lists. Bad paths are hard 400s.
- **Events** -- every state change writes an `evt_`-prefixed event, listable and retrievable at
  `/v1/events`. No webhook delivery; events are queryable state.
- **Magic cards** -- test-mode card numbers as failure injection. A payment method created from a
  magic number produces the corresponding decline, dispute or 3DS outcome.

### Conformance

Behavioral conformance is validated against recordings of the real Stripe API. 19 cassettes
(committed, redacted) are replayed in CI against this world, with every permitted difference
declared in `tests/conformance/allowed_differences.py`. CI never needs a Stripe key and never
opens a socket. Schema conformance validates every returned object against the pinned
`spec3.min.json`.

Cassettes re-record with `uv run python -m tools_dev.record --scenario <name>` (`--list` prints the
names). Recording needs a test-mode key and `api.stripe.com` egress, and never runs in CI.

## Fixtures

Only the `empty` fixture ships today -- the schema and nothing else, frozen at
`2026-09-01T14:00:00.000Z`. The `small` and `large` fixtures described in the spec are **deferred**
and not yet built. The eval suite is also deferred.

## Development

```bash
# Install (requires read access to the private Seahaven repository)
uv sync

# Run the checks -- all four before every commit; CI runs the same list
uv run ruff format --check && uv run ruff check
uv run ty check
uv run pytest
uv run seahaven check

# List fixtures
uv run seahaven fixture list

# Serve over OpenEnv (the `serve` extra; needs a final CPython 3.14, not a release candidate)
uv run --extra serve seahaven serve

# Serve to one MCP client over stdio (the `mcp` extra; cannot be installed beside `serve`)
uv run --extra mcp seahaven mcp

# Serve Stripe's HTTP API
uv run serve_http.py --reset-options '{"startup": {"livemode": false}}'
```

The `serve` and `mcp` extras conflict, so each `uv run --extra serve ...` or
`uv run --extra mcp ...` swaps the environment over to that extra; name the extra on every command
that needs it.

No `--world` flag is needed anywhere: the distribution name (`seahaven-stripe-world`) normalises to
the package (`seahaven_stripe_world`), which is what the CLI and pytest plugin find.

CI (`.github/workflows/ci.yml`) runs that check list on every push to main and every pull request,
over `uv sync --locked`. It syncs without the `serve` extra -- `seahaven.openenv` pulls in
`beartype`, which cannot import on the 3.14 release candidates, and no suite here needs it -- and
with the `http` extra (Starlette and uvicorn).

### Using it in code

```python
import seahaven_stripe_world

with seahaven_stripe_world.world.instance("empty") as inst:
    # Bootstrap: get the account context
    accounts = inst.call("list_available_accounts_or_orgs")
    ctx_id = accounts["accounts"][0]["stripe_context"]
    mode = accounts["accounts"][0]["livemode"]

    # Create a customer
    result = inst.call(
        "stripe_api_write",
        stripe_api_operation_id="PostCustomers",
        parameters={"name": "Jane Doe", "email": "jane@example.com"},
        stripe_context=ctx_id,
        livemode=mode,
    )

    # Read it back
    cust_id = result["id"]
    result = inst.call(
        "stripe_api_read",
        stripe_api_operation_id="GetCustomersCustomer",
        parameters={"customer": cust_id},
        stripe_context=ctx_id,
        livemode=mode,
    )
```

## Conventions

- **Timestamps** are canonical UTC text with milliseconds and a trailing `Z`
  (`2026-06-01T09:00:00.000Z`), from `ctx.clock.iso()`. The API's Unix-second form exists only
  at the serialization edge, in `_time.py`.
- **Ids** are Stripe-shaped (`cus_...`, `pi_...`, `ch_...`), minted by `_ids.stripe_id` from
  `ctx.ids.random`. Never `ctx.ids.uuid()`.
- **Money** is integer minor units; rates are TEXT decimal literals. No floats in the money path.
- **Two error systems.** Stripe errors (`stripe_errors.py`) are return values -- a 402 decline
  keeps its writes. Seahaven errors (`errors.py`) are authoring mistakes -- a raised error rolls
  back. Raise loses the writes, return keeps them.
- **JSON columns** are written only through `_json.dumps` for reproducible fixture bytes.

## The framework

`seahaven` is taken from its own (private) repository at a pinned commit SHA --
`[tool.uv.sources]` in `pyproject.toml` holds the full SHA, and `uv.lock` records the resolution.
A checkout that cannot read `github.com/Kiln-AI/Seahaven` over HTTPS cannot sync. The framework's
docs ship inside the installed package: `uv run seahaven docs` prints the directory.

`pydantic` is pinned to 2.12.3 through `override-dependencies`; the comment beside it in
`pyproject.toml` says why, and when to revisit it.

## Project structure

```
pyproject.toml
serve_http.py                           # serves http_api/ (seahaven.http.main)
AGENTS.md                               # coding-agent instructions
README.md                               # the pitch
docs/technical.md                       # this file
THIRD_PARTY_LICENSES.md
.github/workflows/ci.yml                # the check list, on every push to main and every PR
src/seahaven_stripe_world/
  world.py                              # the World object
  startup.py                            # per-instance account (livemode startup option)
  openenv_app.py                        # the ASGI app `seahaven serve` builds
  errors.py  stripe_errors.py           # the two error systems
  _ids.py  _time.py  _json.py  _seq.py  # shared helpers
  schema/                               # 001-005, DDL for 24 tables + FTS5
  dispatch/                             # router, ResourceSpec engine, params, response
  resources/                            # one module per resource + hand-written actions
  billing/                              # proration, invoicing, dunning, ledger, lifecycle, magic cards
  search/                               # FTS5 executor, parser, field allowlists
  serialize/                            # row-to-API-object, expansion
  discovery/                            # stripe_api_search and stripe_api_details
  tools/                                # the eight registered tools
  middleware/                           # error handler, stripe envelope, idempotency
  http_api/                             # Stripe's HTTP API as a seahaven.http handler
  spec/                                 # spec3.json, spec3.min.json, mcp_catalogue.jsonl, products table
fixtures/empty/                         # the one shipped fixture
fixtures_src/                           # the generators fixtures are frozen from
tools_dev/                              # build-time tools: spec pruning, catalogue enumeration, cassette recorder
tests/                                  # 1,100+ tests
  conformance/                          # cassette replay, allowed differences
  schema_conformance/                   # spec validation of every returned object
  billing/                              # proration, invoicing, dunning, subscription machine
  http_api/                             # the HTTP face, including the Stripe SDK against it
specs/projects/stripe_world/            # functional spec, architecture, component designs
specs/projects/no_tells/                # the MCP conformance project
research/MANIFEST.md                    # record of the primary sources fetched (payloads git-ignored)
```

## Design documentation

The world itself is designed in `specs/projects/stripe_world/`:

- `functional_spec.md` -- what the world does and why
- `architecture.md` -- how it is built
- `components/*.md` -- detailed designs for dispatcher, discovery, data model, cross-cutting
  behavior, billing engine, fixtures, conformance, and evals
- `implementation_plan.md` -- the 23-phase build

The MCP conformance project lives in `specs/projects/no_tells/`:

- `functional_spec.md` -- the 70-row tell register, the governing rule, the refusal model,
  discovery, identity, serialization, and declared residue
- `architecture.md` -- technical design for the conformance work
- `research/mcp-fidelity-probe/` -- probe artifacts and the living tell register
