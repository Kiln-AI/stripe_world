# Research Plan: Real Stripe MCP — fidelity probe

## Goal

Find every way an agent can tell our world (`seahaven_stripe_world`) apart from the real Stripe MCP
server. This is primary research by **probing the live Stripe MCP** attached to this session against
a disposable sandbox account, not web reading — the web is a secondary source, used only to explain
what a probe showed. Feeds the functional spec of `specs/projects/no_tells/`, which is blocked on it.

Every finding must be recorded as a **tell**: what an agent would do, what real Stripe answered,
what our world answers (or is specified to answer), and how big the gap is.

## Run

- Model: inherit the session model (Opus 5). Five subtopic agents plus one summary agent.
- Probe target: the `mcp__stripe__*` tools in this session, pointed at a **sandbox** account that
  `AGENTS.md` declares fully disposable — reads, writes and deletes are all expected.
- Shared-account discipline: the five agents probe concurrently, so each tags every object it
  creates with `metadata: {"probe_lane": "<lane>"}` and a description prefix, never deletes an
  object it did not create, and never treats a global object count as stable.
- Secrets discipline: no key is ever printed or committed; scrub account identifiers, real emails
  and keys from every document written.

## Subtopics

- [ ] **tool-surface** — the exact MCP tool list, schemas, docstrings and return envelopes, versus
      our five
- [ ] **absence-and-refusal** — how the real server answers what it will not or cannot do:
      permission-restricted, out-of-scope, nonexistent, wrong-method, unsupported
- [ ] **discovery-tools** — `stripe_api_search` and `stripe_api_details`: exact output shape,
      ranking, coverage and failure modes
- [ ] **envelope-and-cross-cutting** — what the MCP layer does to a request and response:
      idempotency, pagination, expansion, version, the error envelope as seen through MCP
- [ ] **account-and-statistical-tells** — the account object, sandbox markers, and the tells that
      come from state rather than shape: id entropy, timestamps, volume, realism, timing

## Focus Details

### tool-surface

Enumerate the **complete** tool list the real Stripe MCP exposes in this session: every tool name,
its full description text, its input JSON schema (parameter names, types, required, defaults,
enums), and any annotations. Then call each one at least once and record the **exact** return
envelope — is it `{"status": ..., "body": ...}`, a bare object, a JSON string, MCP content blocks?
Pay particular attention to the six tools our world drops (`stripe_analytics`,
`search_stripe_documentation`, `stripe_implementation_planner`, `send_stripe_mcp_feedback`,
`list_available_accounts_or_orgs`, `manage_stripe_accounts`): what each does, what it returns, and
what an agent calling it in our world would get instead. A missing tool, a differently-worded
description or a differently-typed parameter is a tell before any API call happens. Compare against
`src/seahaven_stripe_world/tools/api.py` and `tools/account.py`. **Not yours:** the *content* of
search/details results — that is `discovery-tools`; and the account object's fields — that is
`account-and-statistical-tells`.

### absence-and-refusal

The load-bearing lane. Establish exactly what the real server answers when an agent asks for
something it will not do, across every flavour of "no" you can produce: (a) an endpoint the sandbox
key genuinely lacks permission for, if one can be found; (b) an in-Stripe-but-out-of-our-scope
resource — Issuing, Connect accounts, Terminal, Treasury, Checkout Sessions, Payment Links, Radar,
Financial Connections, Tax, Climate; (c) a path that does not exist at Stripe at all; (d) a real
path with the wrong verb; (e) `test_helpers/*` paths; (f) a legacy sub-resource (customer sources,
cards, bank accounts); (g) an unknown or malformed parameter; (h) a deleted or never-existing
object id. For each: HTTP status, the exact `error.type`, `code`, `message`, `param`, `doc_url`, and
whether the MCP layer rewrites any of it. The project's headline requirement is that unimplemented
features look like a key without access rather than like an unimplemented mock — so find out what
"a key without access" actually looks like on this server, verbatim, and whether that is
distinguishable from the other flavours of no. Note where the wire `type` contradicts our spec's
four-value claim. **Not yours:** error envelopes for *implemented* endpoints behaving normally —
that is `envelope-and-cross-cutting`.

### discovery-tools

Drive `stripe_api_search` and `stripe_api_details` hard and pin their exact contracts. For search:
what fields come back per result, how many results, in what order, how ranking behaves for exact
versus partial versus nonsense queries, what an empty result looks like, whether it covers the whole
Stripe API or a subset, whether it returns billing-scope and out-of-scope operations alike, and what
happens with an empty string, a very long query, or non-English input. For details: the exact
key set and nesting of the returned document, how parameters are described, how deep nested object
parameters go, what enums look like, and what happens for an unrouted path, a concrete path with a
real id, a pattern path, a wrong verb, and a nonexistent operation. Our implementation returns up to
ten `{method, path, summary}` triples and a six-key details document restricted to 148 routed
operations — measure the gap precisely, including whether the real server's coverage of endpoints
our world does not route is itself the tell. **Not yours:** `search_stripe_documentation`, which is
a different tool and belongs to `tool-surface`.

### envelope-and-cross-cutting

What the MCP layer adds to, removes from, or rewrites in an ordinary successful or failed API call.
Probe: does a write accept `idempotency_key`, and what does a same-key replay return through MCP —
identical body, any marker? What does a same-key-different-params replay answer? Is there a
`stripe-version` visible anywhere, and which version does the server serve? Are response headers
surfaced at all? Does `request_log_url` appear in errors? What does pagination look like through the
tool — the full list envelope with `url` and `has_more`, cursor behaviour at boundaries, an
out-of-range `limit`? What does a bad `expand[]` path answer, and a two-level expand? What does a
deliberately malformed `params` (wrong type, array where object expected) answer — Stripe's envelope
or an MCP-level error? Does the tool ever return a non-JSON or truncated body for a large response?
Record everything verbatim, then diff against `components/cross_cutting.md`. **Not yours:** refusals
of things the server will not do — that is `absence-and-refusal`.

### account-and-statistical-tells

The tells that survive a perfect shape match. Start with `get_stripe_account_info` — the exact
object, every field, and what it says about the account being a sandbox, including whether a
`livemode` marker, an account id shape, capabilities, `country`, `default_currency`, or a
dashboard/settings sub-object leaks anything a fake would have to fabricate. Then look for state
tells an agent could notice inside a normal session: the shape and entropy of real Stripe ids
(length, alphabet, prefix, whether they encode anything); whether `created` on objects made in quick
succession differs; what a freshly-created object's timestamps look like relative to other objects;
whether real sandbox data has correlations a generated fixture would not (email domains, name
distributions, amounts, currency mix, address realism, gaps in sequences); and response latency and
its variance. For each, say whether our world — frozen clock, seeded id stream, generated fixtures —
would reproduce it, and how an agent could test it in one or two calls. **Not yours:** the tool's
input schema or the shape of an error — those are other lanes.
