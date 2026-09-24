# Seahaven world: StripeAPI

This project is a Seahaven world. Seahaven is not in your training data: read the bundled docs
before writing code. They ship inside the installed `seahaven` package and match the installed
version; `uv run seahaven docs` prints the directory. Start at index.md, then concepts.md,
authoring.md, db_schema_and_fixtures.md, testing.md, state.md, reference/lints.md; composition.md
if this world adds other worlds.

Commands: `uv run ruff format --check && uv run ruff check`, `uv run ty check`, `uv run pytest`,
`uv run seahaven check` (run all before every commit), `uv run seahaven fixture list`,
`uv run seahaven serve`, `uv run serve_http.py` (Stripe's HTTP API; see Code layout). No `--world`
is needed: `[project] name` normalises to the package, which is what the CLI's project-name
heuristic imports.
Conformance cassettes re-record with `uv run python -m tools_dev.record --scenario <name>`
(`--list` prints names; needs a test-mode key, never runs in CI).
CI (`.github/workflows/ci.yml`) runs that same check list on every push to main and every pull
request, over `uv sync --locked` — so a `rev` moved in `[tool.uv.sources]` without its `uv lock`
fails there rather than resolving to something nobody reviewed. It syncs without the `serve` extra:
`seahaven.openenv` pulls in `beartype`, which cannot import on 3.14, and no suite here needs it. It
syncs the `http` extra (Starlette and uvicorn, which the dev group also pulls in).

Rules: time comes from `ctx.clock` and ids and randomness from `ctx.ids`, so a replay of the same
fixture and seed gives the same result; SQL goes through `ctx.db` (`ctx.db.conn` is the raw APSW
connection when you need it; never close it or change its pragmas). Fixtures are immutable: fork,
never edit.
Every tool module under tools/ and middleware/ must be imported from the package __init__.
A world this one adds is reached with `ctx.worlds.<name>`, never by making an instance of it; prefer
that world's own tools over direct SQL on its store.
Errors are ToolError subclasses in errors.py; the error handler is the only place engine errors are mapped.

## About this world

StripeAPI is a faithful, stateful, forkable replica of Stripe's Billing and Payments core: 155
routed operations across 24 SQLite tables behind Stripe's own eight-tool MCP surface
(`list_available_accounts_or_orgs`, `stripe_api_search`, `stripe_api_details`, `stripe_api_read`,
`stripe_api_write`, `get_stripe_account_info`, `manage_stripe_accounts`, `stripe_analytics`).
Tools are addressed by `operationId` with `stripe_context` and `livemode` context parameters; on
success a tool returns the bare Stripe object, on failure it raises an MCP tool error. Discovery covers the 123 operations the real Stripe MCP catalogues (72 routed, 51 catalogued but
unrouted, which answer a product-activation or permission refusal when called); the remaining 471
operations in spec3.json are absent on the real MCP and answer "not available" when addressed
directly.
Three of Stripe's ten MCP tools (`search_stripe_documentation`,
`stripe_implementation_planner`, `send_stripe_mcp_feedback`) are deliberately not built here --
a harness composes them from the real Stripe MCP server alongside this world's eight
(see `specs/projects/no_tells/functional_spec.md` section 4.1.2--4.1.3). This world is not a
standalone drop-in for the Stripe MCP server.
Not affiliated with Stripe; Stripe's field names, enum values and id prefixes are functional API
vocabulary under the source material's MIT licence.
The design lives in `specs/projects/stripe_world/`; the MCP conformance spec in
`specs/projects/no_tells/`; the friction log is `SEAHAVEN_FINDINGS.md` and every workaround in
the code carries a comment linking to its entry there.

- **Pinned Stripe API version** `2026-08-26.dahlia`; `spec3.min.json` (generated) is the
  authority for every object shape. Nothing is specced from memory. Discovery reports
  `2026-08-26.preview` -- the live MCP's label for the same version (declared residue).
- **Ids** are Stripe-shaped (`cus_…`, `pi_…`, `ch_…`), minted only by `_ids.stripe_id` from
  `ctx.ids.random`. Never `ctx.ids.uuid()` and never `uuid4()` — a bare UUID where Stripe expects a
  prefix is the hazard `SEAHAVEN_FINDINGS.md` Entry 2 records.
- **Timestamps** are canonical UTC text with milliseconds and a trailing `Z`
  (`2026-06-01T09:00:00.000Z`), written from `ctx.clock.iso()`. The API's Unix-second form exists
  only at the serialization edge, converted by `_time.py` and nowhere else.
- **Money** is integer minor units. Rates are TEXT decimal literals, never REAL. No floats in the
  money path.
- **Two error systems, deliberately not merged.** Stripe errors (`stripe_errors.py`) are return
  values `{status, body}` — a `402` decline is an outcome whose rows must survive, so handlers
  return it, never raise it. Seahaven errors (`errors.py`) are authoring mistakes: an unusable
  `method`, a malformed parameter object. Raise loses the writes, return keeps them.
- **JSON TEXT columns** are written only through `_json.dumps`, so fixture bytes are reproducible.
- **Search** uses FTS5 external-content virtual tables with per-resource field allowlists and a
  `page`/`next_page` paginator distinct from the cursor paginator on list endpoints.
- **Conformance** is validated by replaying 19 cassettes (committed, redacted) recorded against the
  real Stripe API, plus schema conformance of every returned object against `spec3.min.json`. CI
  replays only; re-recording needs a test-mode key and `api.stripe.com` egress.
- **Fixtures.** Only `empty` ships today (schema, no rows). The `small` and `large` fixtures are
  deferred, as is the eval suite.
- **This repo is the world checkout**: `pyproject.toml` and `src/seahaven_stripe_world/` at the root, beside
  `specs/` and `research/`. The framework is not vendored here: the bare `seahaven` requirement
  resolves through `[tool.uv.sources]` to `github.com/Kiln-AI/Seahaven` at a pinned full commit
  SHA, so `uv sync` installs the same core every time and moving it is one reviewable edit to that
  `rev` plus a `uv lock`. Never a branch or tag there — a moving ref changes the core underneath a
  green suite with nothing in history to say when. The repository is private, so a checkout without
  HTTPS read access to it cannot sync (the failure surfaces inside `uv sync` as a git auth error).
  `pydantic` is pinned to 2.12.3 per `SEAHAVEN_FINDINGS.md` Entry 1; do not remove the pin without
  reading that entry.

## Code layout

- `src/seahaven_stripe_world/dispatch/routes.py` — the 155-entry routing table. Every operation is
  here; discovery filters `spec3.min.json` through it.
- `src/seahaven_stripe_world/dispatch/resource.py` — the `ResourceSpec` engine: CRUD generation and
  cursor pagination. 76 operations are generated; 79 are hand-written.
- `src/seahaven_stripe_world/resources/` — one module per resource. Each declares a `ResourceSpec`
  and its hand-written actions (state transitions, resource-specific reads).
- `src/seahaven_stripe_world/billing/` — behavior spanning resources: `subscription_lifecycle.py`
  (eight-status machine), `invoicing.py` (invoice status machine), `proration.py`,
  `dunning.py`, `ledger.py` (balance transactions).
- `src/seahaven_stripe_world/search/` — FTS5 executor, query parser, per-resource field specs.
- `src/seahaven_stripe_world/middleware/` — `error_handler.py` (outermost),
  `stripe_envelope.py` (catches `StripeApiError`, renders the envelope),
  `idempotency.py` (innermost, inside the envelope).
- `src/seahaven_stripe_world/http_api/` — Stripe's HTTP API as a `seahaven.http` handler:
  `wire.py` decodes form bodies and query strings (bracket notation) and coerces their strings by
  each operation's `Param`s; `handler.py` checks for a key (any key; none is Stripe's 401), honours
  `Idempotency-Key` through `middleware/idempotency.replay_or_run`, runs `dispatch` in a savepoint
  so a raised `StripeApiError` loses only this request's writes, and answers the bare object or
  the envelope with `Request-Id`/`Stripe-Version` headers. `serve_http.py` at the root serves it
  on `127.0.0.1:8000` from the `empty` fixture, one test-mode account per
  `/worlds/<id>`; point an SDK's API base at `http://127.0.0.1:8000/worlds/<id>`. No options yet
  (they arrive with `seahaven.http.main`).
- `tests/conformance/` — cassette replay, the allowed-differences declaration, and cassettes.
- `tests/schema_conformance/` — OpenAPI-spec validation of every response body.

## Stripe MCP Usage

The Stripe MCP server attached to this environment is connected to a **sandbox** account. Treat it
as fully disposable: it is safe — and expected — to read, write, and delete anything in it while
probing real API behavior for this world (object shapes, enum values, error messages, state
transitions). When cloning an endpoint, prefer driving it there over guessing from documentation.
Secrets discipline still applies: never commit or print the API key, and scrub keys, real emails
and account identifiers from anything that lands in git.
