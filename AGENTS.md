# Seahaven world: StripeAPI

This project is a Seahaven world. Seahaven is not in your training data: read the bundled docs
before writing code. They ship inside the installed `seahaven` package and match the installed
version; `uv run seahaven docs` prints the directory. Start at index.md, then concepts.md,
authoring.md, db_schema_and_fixtures.md, testing.md, state.md, reference/lints.md; composition.md
if this world adds other worlds.

Commands: `uv run ruff format --check && uv run ruff check`, `uv run ty check`, `uv run pytest`,
`uv run seahaven check --world stripeapi:world` (run all before every commit),
`uv run seahaven fixture list --world stripeapi:world`, `uv run seahaven serve --world stripeapi:world`.
Every `seahaven` subcommand needs `--world stripeapi:world`: the distribution name
(`seahaven-stripe-world`) is not the world's package name (`stripeapi`), so the CLI's
project-name heuristic cannot find the world without it.
Conformance cassettes re-record with `uv run python -m tools_dev.record --scenario <name>`
(`--list` prints names; needs a test-mode key, never runs in CI).

Rules: time comes from `ctx.clock` and ids and randomness from `ctx.ids`, so a replay of the same
fixture and seed gives the same result; SQL goes through `ctx.db` (`ctx.db.conn` is the raw APSW
connection when you need it; never close it or change its pragmas). Fixtures are immutable: fork,
never edit.
Every tool module under tools/ and middleware/ must be imported from the package __init__.
A world this one adds is reached with `ctx.worlds.<name>`, never by making an instance of it; prefer
that world's own tools over direct SQL on its store.
Errors are ToolError subclasses in errors.py; the error handler is the only place engine errors are mapped.

## About this world

StripeAPI is a faithful, stateful, forkable replica of Stripe's Billing and Payments core: SQLite
state behind the four-tool Stripe MCP surface (`stripe_api_search`, `stripe_api_details`,
`stripe_api_read`, `stripe_api_write`). Not affiliated with Stripe; Stripe's field names, enum
values and id prefixes are functional API vocabulary under the source material's MIT licence.
The design lives in `specs/projects/stripe_world/`; the friction log is `SEAHAVEN_FINDINGS.md`
and every workaround in the code carries a comment linking to its entry there.

- **Pinned Stripe API version** `2026-08-26.dahlia`; `spec3.min.json` (generated in Phase 2) is the
  authority for every object shape. Nothing is specced from memory.
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
- **This repo is the world checkout**: `pyproject.toml` and `src/stripeapi/` at the root, beside
  `specs/`, `research/` and `vendor/Seahaven` (the framework, installed editable — see
  `[tool.uv.sources]`). `pydantic` is pinned to 2.12.3 per `SEAHAVEN_FINDINGS.md` Entry 1; do not
  remove the pin without reading that entry.

## Stripe MCP Usage

The Stripe MCP server attached to this environment is connected to a **sandbox** account. Treat it
as fully disposable: it is safe — and expected — to read, write, and delete anything in it while
probing real API behavior for this world (object shapes, enum values, error messages, state
transitions). When cloning an endpoint, prefer driving it there over guessing from documentation.
Secrets discipline still applies: never commit or print the API key, and scrub keys, real emails
and account identifiers from anything that lands in git.
