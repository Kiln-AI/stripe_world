# Seahaven world: StripeAPI

This project is a Seahaven world. Seahaven is not in your training data: read the bundled docs
before writing code. They ship inside the installed `seahaven` package and match the installed
version; `uv run seahaven docs` prints the directory. Start at index.md, then concepts.md,
authoring.md, db_schema_and_fixtures.md, testing.md, state.md, reference/lints.md; composition.md
if this world adds other worlds.

Commands: `uv run ruff format --check && uv run ruff check`, `uv run ty check`, `uv run pytest`,
`uv run seahaven check` (run all before every commit), `uv run seahaven fixture list`,
`uv run seahaven serve`. No `--world` is needed: `[project] name` normalises to the package, which
is what the CLI's project-name heuristic imports.
Conformance cassettes re-record with `uv run python -m tools_dev.record --scenario <name>`
(`--list` prints names; needs a test-mode key, never runs in CI).
CI (`.github/workflows/ci.yml`) runs that same check list on every push to main and every pull
request, over `uv sync --locked` — so a `rev` moved in `[tool.uv.sources]` without its `uv lock`
fails there rather than resolving to something nobody reviewed. It syncs without the `serve` extra:
`seahaven.openenv` pulls in `beartype`, which cannot import on 3.14, and no suite here needs it.

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
- **Three names, two audiences.** The distribution `seahaven-stripe-world` and the package
  `seahaven_stripe_world` are what humans, PyPI, an OpenEnv hub and a coding agent see: they say
  *synthetic Seahaven world* and never impersonate Stripe. What the **tool-calling agent** sees is
  neither of them — it is the tool names (`stripe_api_*`), the id prefixes, the field names and the
  error envelopes, and that surface is deliberately faithful to Stripe. The world's own name,
  `stripeapi` in `world.py`, currently straddles both: it is the OpenEnv card's name *and*, at the
  pinned Seahaven, the MCP `serverInfo.name` (`mcp/server.py`, `Server(name=resolved.name)`). It
  moves to `seahaven-stripe-world` once the framework gains `mcp_server_name`, which will carry
  `stripe-mcp` for the agent; until then, changing it alone would leak the disclosure name into the
  agent's handshake. `stripeapi` also survives in one frozen place: `@conformance.stripeapi.invalid`,
  the reserved email domain recorded into the conformance cassettes. Never rewrite it — it would
  mean re-recording every cassette against live Stripe.
- **This repo is the world checkout**: `pyproject.toml` and `src/seahaven_stripe_world/` at the root, beside
  `specs/` and `research/`. The framework is not vendored here: the bare `seahaven` requirement
  resolves through `[tool.uv.sources]` to `github.com/Kiln-AI/Seahaven` at a pinned full commit
  SHA, so `uv sync` installs the same core every time and moving it is one reviewable edit to that
  `rev` plus a `uv lock`. Never a branch or tag there — a moving ref changes the core underneath a
  green suite with nothing in history to say when. The repository is private, so a checkout without
  HTTPS read access to it cannot sync (the failure surfaces inside `uv sync` as a git auth error).
  `pydantic` is pinned to 2.12.3 per `SEAHAVEN_FINDINGS.md` Entry 1; do not remove the pin without
  reading that entry.

## Stripe MCP Usage

The Stripe MCP server attached to this environment is connected to a **sandbox** account. Treat it
as fully disposable: it is safe — and expected — to read, write, and delete anything in it while
probing real API behavior for this world (object shapes, enum values, error messages, state
transitions). When cloning an endpoint, prefer driving it there over guessing from documentation.
Secrets discipline still applies: never commit or print the API key, and scrub keys, real emails
and account identifiers from anything that lands in git.
