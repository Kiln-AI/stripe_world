---
status: complete
---

# Architecture: No Tells

Technical design for [`functional_spec.md`](functional_spec.md). Framework facts here were read from
the vendored Seahaven docs on 2026-09-22 and are cited to the file that states them; Stripe facts
come from the probe in [`research/mcp-fidelity-probe/`](research/mcp-fidelity-probe/).

## 0. The shape of the change

**This project rebuilds the edge and leaves the engine alone.** That is the single most useful thing
to know before reading further, and it is what keeps a 70-row register tractable.

```
   8 tools  ← rebuilt: signatures, descriptions, context params        ▲ ALL CHANGE
      │                                                                │
   middleware/stripe_envelope.py  ← rewritten: unwrap or raise         │
      │                                                                │
   dispatch()  → ApiResponse(status, body)                             ▼ UNCHANGED
      │
   router → routes → handlers → resources/ → billing/ → SQLite
```

| Unchanged | Changed |
|---|---|
| `dispatch/` router, `ResourceSpec`, `ParamSpec`, pagination | `tools/` — all of it |
| `resources/` handlers, `billing/`, the state machines | `middleware/stripe_envelope.py` |
| `schema/` DDL, the tables, idempotency machinery | `discovery/` — rebuilt over the full catalogue |
| `serialize/` structure and expansion resolver | `_ids.py`, `startup.py`, `spec/` pipeline |
| Conformance cassettes and the HTTP-level contract | `serialize/fields.py` constants (livemode, new fields) |

No handler changes. No new resource, route or state transition (functional spec §16).

## 1. Framework facts that constrain the design

Measured, not assumed. Each shaped a decision below.

| Fact | Source | Consequence |
|---|---|---|
| A tool's JSON schema is a pydantic model built from the signature | `authoring.md` §Arguments | `Annotated[..., Field(...)]` gives `description`, `ge`/`le`, `default`, `examples`, `alias` — enough to reproduce the real schemas exactly (§2.2) |
| Validation is **strict**: `"5"` is not an `int`, undeclared arguments are refused | same | Applies to *tool arguments only*. Leniency inside `parameters` (functional spec §5.4) stays `ParamSpec`'s job |
| `@world.tool(description=...)` overrides the docstring | `reference/lints.md` SH205 | Real descriptions live as verbatim string constants in a data module, not as docstrings (§2.3) |
| Results are JSON data; `bytes` and `set` refused | `authoring.md` §Results | Returning the bare body is native |
| A tool error travels as **data** on `error` — `{error_type, message}` plus `metadata.seahaven_error` | `serving_and_openenv.md` §Calls, results and errors | Raising is how we produce the tool-error channel; `message` is ours to set |
| `error_type` is framework-set, one of three values, never set by a world | same | We cannot vary the channel, only the text (§3) |
| Middleware is `(ctx, call, next_)` and runs **outside** the per-call transaction | `authoring.md` §Middleware; functional spec §4.5.2 | The unwrap-or-raise transform must live there, so committed writes survive a raised error |
| `@world.instance_startup` runs once per instance; hooks leave results in `ctx.state` | `authoring.md` §Startup | Where `livemode` and the account object come from (§6.2) |
| **SH206 is a warning, fires only against a prefixing host, and has no suppression mechanism** | `reference/lints.md` §SH206 | See §1.1 — this is a correction |

### 1.1 Correction: the SH206 cost is much smaller than the functional spec says

Functional spec §4.6 and §13 treat matching the real tool descriptions — which name other tools — as
a real cost that makes the world "unsafe to `add_world` under a prefixing host", and file it as a
framework finding. Reading the rule itself, that is overstated on three counts:

1. **It is a warning, not an error.** `seahaven check` reports it; it does not fail on it.
2. **It fires against the *host's* `world.py`**, not ours, and only when a host adds this world
   under a `tool_prefix`. Standalone — which is how this world is developed and tested — it never
   fires at all.
3. **The documented fix is to accept it.** The rule's own text: *"drop the `tool_prefix` on that
   `add_world`, or accept the mention: check never rewrites a description… That is why it is a
   warning: accept the mention and move on."* Rewriting the description is *"deliberately not an
   option"* because *"the description is the added world's statement about its own product."*

So the framework already takes our side: a description is the world's own statement and the host
must not edit it. There is no finding to file. The residual behavioral risk is real but is the
host's to manage — an agent under a prefixing host reads `stripe_api_search` in a description and
calls the unprefixed name — and the host's remedy is not to prefix.

**Action:** §13's `SH206` residue row should be downgraded from "the world becomes unsafe under a
prefixing host" to "a prefixing host earns a warning it is documented to accept", and the
`SEAHAVEN_FINDINGS.md` entry should not be written. Flagged here rather than edited into the
functional spec unilaterally, because that spec is `complete`.

## 2. The tool layer

### 2.1 Layout

```
tools/
  __init__.py
  api.py               # stripe_api_read / _write / _search / _details
  accounts.py          # list_available_accounts_or_orgs, get_stripe_account_info,
                       #   manage_stripe_accounts
  analytics.py         # stripe_analytics — refusal only
  _descriptions.py     # verbatim real description strings, generated + committed
  _context.py          # stripe_context / livemode validation, shared by every tool
```

`call_stripe` stays in `api.py`, unregistered, with its current signature including
`idempotency_key` (functional spec §16).

### 2.2 Signatures

Every signature is fixed by the captured real schema in
[`tool-surface/tool-schemas.md`](research/mcp-fidelity-probe/tool-surface/tool-schemas.md). The four
API tools:

```python
OperationId = Annotated[str, Field(description="The operation ID to execute",
                                   examples=["PostCustomers", "GetPaymentIntents"])]
StripeContext = Annotated[str, Field(description="The account to target for this request. …")]
LiveMode = Annotated[bool, Field(description="Whether to operate in livemode (true) or test mode/ sandbox (false). …")]

@world.tool(description=_descriptions.STRIPE_API_READ)
def stripe_api_read(ctx, stripe_api_operation_id: OperationId,
                    parameters: dict[str, Any],
                    stripe_context: StripeContext, livemode: LiveMode) -> Any: ...
```

Four properties the schema must have, each checked by a test (§8.1):

- **`parameters` is required**, not defaulted — the real schema lists it in `required`.
- **No `method` on write**; the verb comes from the operation id.
- **No `idempotency_key`** anywhere (functional spec §8).
- **`limit` on search** is `Annotated[int, Field(ge=1, le=20, default=5, …)]`.

`stripe_api_write` additionally takes `human_confirmation: dict | None`. It is accepted, schema-
matched, and **ignored** — this world never demands approval. That is a declared difference; the
alternative is inventing a policy for which operations are sensitive, which nothing measured.

### 2.3 Descriptions are data, not docstrings

`_descriptions.py` holds one `Final[str]` per tool, copied verbatim from the captured schemas, and
`@world.tool(description=...)` uses them. Docstrings then describe the *implementation* for a human
reader, which is what they are for, and cannot drift into the agent surface.

A test asserts each registered description is byte-identical to the captured artifact, so an
accidental edit fails rather than silently reopening a blatant tell.

### 2.4 Context validation

`_context.py` exposes one function, called first in every tool:

```python
def check(ctx: seahaven.Ctx, stripe_context: str, livemode: bool) -> None:
    """Raise the session-validation refusal if either does not match this instance."""
```

Reads `ctx.state["account"]` (§6.2). Two refusals, both verbatim from the probe:

| Condition | Message |
|---|---|
| `stripe_context` names another account | `No account found for the provided stripe_context and livemode. Use the list_available_accounts_or_orgs tool to see the accounts you can access.` |
| `livemode` differs from the instance's | live-side: the instance is live, retry with `livemode` true. Sandbox-side, measured: `The provided account {id} is a sandbox account. Retry with livemode set to false.` |

The live-side wording is **inferred** (functional spec §4.7) and is the one string here not measured.

Both raise `SessionValidation`, an `errors.py` `ToolError` subclass — they are pre-dispatch faults,
so rolling back is correct and nothing has been written yet.

`list_available_accounts_or_orgs` and `manage_stripe_accounts` take no context parameters and skip
this.

### 2.5 The envelope transform

The core mechanism, and the reason it is middleware and not a tool-level helper.

```
tool function          →  returns ApiResponse(status, body)        [inside the transaction]
   ↓
idempotency middleware →  may short-circuit with a stored ApiResponse
   ↓
stripe_envelope        →  2xx: return body            (bare dict)  [outside the transaction]
                          non-2xx: raise StripeToolError(rendered)
   ↓
error_handler          →  maps the rest
```

`middleware/stripe_envelope.py` is rewritten rather than removed. Today it renders
`{status, body}`; now it does one of two things with what `next_()` returns:

```python
def stripe_envelope(ctx, call, next_):
    try:
        result = next_()
    except StripeApiError as error:          # raised: the transaction already rolled back
        raise _render(error.status, error.envelope()) from None
    if isinstance(result, ApiResponse):
        if result.status < 400:
            return result.body               # the bare object — no wrapper
        raise _render(result.status, result.body)   # earned error: writes already committed
    return result                            # non-dispatch tools pass through
```

**Why this is correct and a tool-level unwrap would not be.** A `402` decline must keep its rows.
The handler returns rather than raises, the per-call transaction commits when the tool function
exits normally, and only then does middleware — running outside it — convert the committed failure
into a raised error. Raising inside the tool would roll back the charge we are trying to record.

`_render` builds the agent-visible string (§3) and returns a `StripeToolError` for the caller to
raise, so the `raise` is always at the middleware boundary and never buried in a helper.

**Middleware order is unchanged**: `error_handler → stripe_envelope → idempotency`. Idempotency
still sits inside the envelope, so a replay short-circuits before any rendering and still writes
nothing.

## 3. Error channels

Functional spec §4.5.1 names three channels. The framework gives a world exactly two mechanisms —
return a value, or raise — and `error_type` is framework-set and not ours to vary. So:

| Channel | Real mechanism | Ours | Text |
|---|---|---|---|
| Session validation | failed POST to the MCP endpoint | raised `ToolError` | `{"error": "<message>"}` payload, verbatim |
| Operation gate | tool error | raised `ToolError` | `Operation '{id}' is not available. Use stripe_api_search to find available operations.` |
| Stripe API | tool error | raised `ToolError` | `Stripe API error: {message}` + guidance suffix |

**The first channel's *mechanism* is not reproducible, and the spec already anticipated this.** A
Seahaven world cannot fail a transport POST; it can only raise. The text is reproduced exactly and
the transport distinction is **declared residue** — with the mitigating fact from functional spec
§4.5.1 that the `Streamable HTTP error: …` wrapper an agent sees is added by the *MCP client*, not
by Stripe, so it is not part of what the real server emits either.

The guidance suffix is assembled centrally:

```
Stripe API error: {stripe_message}

Use stripe_api_details with stripe_api_operation_id: "{op_id}" to see all required and
optional parameters.
```

Emitted only where the real server emits it — measured on `resource_missing` and parameter faults,
absent from the operation gate.

## 4. The catalogue and the refusal model

### 4.1 The artifact

`src/seahaven_stripe_world/spec/mcp_catalogue.jsonl` — generated, committed, one record per line:

```json
{"op": "GetCustomers", "verdict": "catalogued", "permissions": ["customer_read"], "probed": "2026-09-22"}
{"op": "GetEvents", "verdict": "absent", "probed": "2026-09-22"}
{"op": "PostPayouts", "verdict": "absent", "probed": "2026-09-22"}
{"op": "GetFooBar", "verdict": "error", "detail": "transport", "probed": "2026-09-22"}
```

JSONL rather than JSON precisely because it must be **appendable mid-run** (functional spec §5.3.1):
a partial file is a valid partial result, and a re-run reads the `op` set already present and probes
only the gaps. `verdict: "error"` is never treated as `absent` — that would invent a refusal.

### 4.2 The enumeration script

`tools_dev/enumerate_catalogue.py`, run deliberately like the cassette recorder, never in CI.

- Input: every `operationId` in the committed `spec3.json`.
- For each not already in the artifact: call the real `stripe_api_details`; a document means
  `catalogued` (record `required_permissions`), the not-available string means `absent`, anything
  else means `error`.
- Append-and-flush per record. Ordered by operation id so runs are comparable.
- `--resume` is the default; `--sample N` is the declared fallback, and writes a `sampled: true`
  marker plus the uncovered remainder so nothing reads as measured that was not.
- **It also records the live server's tool list**, not only its operations, as a `{"kind": "tools",
  "tools": [...], "probed": "…"}` record. Functional spec §4.1.1 requires this: the documented and
  live tool lists already disagree in both directions, and a drift run must catch further movement —
  in particular `get_stripe_account_info` reappearing, which would retire that section's exception.

### 4.2.1 Re-running it as a drift job

The enumeration is not a one-off. Stripe curates the catalogue and has already been observed to
exclude operations no permission or product explains (functional spec §9), so membership can move
under us. Re-running with `--resume` against a fresh file and diffing against the committed artifact
is the drift check, and it is the same command — there is no separate drift script to keep in step.

A diff is a finding, not a failure: an operation moving between `catalogued` and `absent` changes
which bucket it refuses in, and the generated refusal test (§8.2) will fail on it, which is the
intended alarm.

### 4.3 Routing a call

In `dispatch/router.py`, before route matching:

```
op_id not in catalogue          → bucket A  ("Operation '…' is not available.")
op_id catalogued, not routed    → bucket B  (§4.4)
op_id catalogued and routed     → the handler
```

Note the ordering: **the catalogue is consulted before the route table.** An operation we route but
the real server does not expose — `GetEvents`, `PostInvoicesInvoicePay` — must answer bucket A even
though a handler exists (functional spec §9). The route table stays as it is; the catalogue gates it.

### 4.4 B1 versus B2

Functional spec §5.1.1 splits bucket B by whether the operation belongs to an activatable Stripe
product. That needs a table, and it is small:

`spec/products.py` — `{tag_prefix: (product_name, dashboard_url)}`, e.g.
`"issuing" → ("Issuing", "https://dashboard.stripe.com/issuing/overview")`. Keyed on the `tags`
already present in the details document, so it is derived from the spec rather than hand-listed per
operation.

- Tag matches a product → **B1**, the measured form:
  `Your account is not set up to use {product}. Please visit {url} to get started.`
- No match → **B2**, the inferred permission form, naming `required_permissions` from the catalogue
  record, which is why §4.1 stores them.

`stripe_analytics` takes B1 with Sigma's product name and URL (functional spec §6).

## 5. Discovery

### 5.1 Packaging the spec

`spec3.json` is committed in full (functional spec §7) at
`src/seahaven_stripe_world/spec/spec3.json` — 8 MB, in-package because discovery reads it.

**It is never parsed at runtime.** `tools_dev/prune_spec.py` gains a second output:
`spec/discovery_index.json`, generated and committed, holding one precomputed **details document**
per catalogued operation in exactly the twelve-key shape §5.3 returns, plus the search fields. The
runtime loads that file **once at module import** into a module-level singleton — immutable
reference data shared by every instance in the process, not per-instance state.

This keeps three properties: the 8 MB file is reviewable in git, the runtime cost is one parse per
process, and discovery cannot drift from the spec because the index is generated from it with a test
asserting the pruner reproduces the committed output.

### 5.2 Search

Real ranking is semantic and not reproducible deterministically. The design is a scored keyword
match over the precomputed index, which is close in behavior and exactly reproducible:

- **Fields indexed**: `keywords` (the real details document already carries them — `["v1",
  "customers", "get", "list", "customer"]` — and the pruner generates them the same way), `tags`,
  path segments, `summary`.
- **`resource` terms** score against `tags` and `keywords` (weight 3) and path segments (weight 2).
- **`intent` terms** score against the operation's verb class — `create`→`POST` collection,
  `list`→`GET` collection, `retrieve`→`GET` item, `update`→`POST` item, `delete`→`DELETE` — plus a
  match against `summary` (weight 1).
- Ties break on operation id, so ordering is stable across runs.
- Return the top `limit`, default 5.

Ranking will not match the real server's order. That is declared; the tell that matters is an empty
result for a common resource, which this design closes.

### 5.3 Details

A direct read from the index: `id`, `method`, `path`, `summary`, `description` (full text, not the
first sentence), `tags`, `keywords`, `parameters` grouped `{path, query, body}` with the parameter
name as key and nesting to full depth, `required_permissions`, `openapi_spec_version`.

Path placeholders are normalised to `{id}` in discovery output only. The router keeps its
resource-specific patterns internally; normalisation happens in the pruner, so runtime does no
rewriting.

## 6. Identity

### 6.1 Ids

`_ids.py` gains the two measured formats
([`id-shapes.md`](research/mcp-fidelity-probe/account-and-statistical-tells/id-shapes.md)):

```
Format A   prefix + 14 random chars from [A-Za-z0-9]        cus_, prod_, si_
Format B   prefix + V(1) + T(5) + A(10) + R(8) = 24 chars   everything else
             V  version digit
             T  base62 timestamp group — equal for objects created in the same second
             A  the account fragment: the last 10 chars of the account id's suffix
             R  8 random chars from [A-Za-z0-9]
```

Three design consequences:

- **The account fragment comes from the account object**, so ids agree with the account and with
  each other. It is read from `ctx.state["account"]`, which means `_ids` depends on startup having
  run — enforced by a `WorldBug` if it has not.
- **`T` derives from the timestamp being stamped, not from "now".** Under a frozen clock those are
  the same thing and every id in a rollout shares a `T` group — which is also true of real ids
  created in the same second. Passing the timestamp explicitly costs nothing now and makes the
  deferred fixture generator correct for free, since it stamps historical rows.
- **The version digit** is observed to correlate with direct creation (`1`) versus side-effect
  creation (`3`). That pattern is from one sample and is **not** load-bearing: `_ids` takes it as a
  per-call argument defaulting to `1`, and the handful of side-effect paths pass `3`. If the pattern
  is wrong, one argument changes.

The existing guard tests extend: the grep for `ctx.ids.uuid(` stays, and the per-prefix format test
replaces the blanket 24-char assertion.

### 6.2 `livemode` and the account: one startup hook

`startup.py` builds one `ctx.state["account"]` dict from fixture configuration: the account id, the
mode, the name, and the full account object of §6.3. Everything downstream reads it — `_ids` for the
fragment, `_context` for validation, the account tools for their two faces, the serializer for
`livemode`.

**The `livemode` sweep is the widest edit in the project** (functional spec §4.7), and the design
makes it a deletion rather than a substitution:

- **Remove `livemode` from every `ResourceSpec` `constants=` and every inline literal** — roughly
  fifteen modules today.
- **Add it centrally in `serialize/fields.py`**, driven by one set naming the four objects at this
  API version that carry no `livemode` field: `balance_transaction`, `refund`, `subscription_item`,
  `discount`. One rule, one place, and the four-exception carve-out stops being copied per module.
- **Embedded mode strings are derived.** The known case is
  `resources/invoiceitems.py`'s `No such Invoice Item: 'ii_…'(livemode=false)`. A test greps the
  source for `livemode=false` and `livemode=true` string literals and fails on a hit.

### 6.3 The account object

One fixture-defined object, presented two ways (functional spec §4.1):

- `get_stripe_account_info()` → the full object.
- `list_available_accounts_or_orgs()` → `{"accounts": [{"stripe_context", "livemode", "name"}]}`,
  projected from the same dict.

Shape is a consistent fresh sandbox (functional spec §10.2): charges and payouts disabled, empty
`capabilities`, `business_profile` sub-fields null, the missing top-level keys present, no
`metadata`, and a real-shaped `acct_` + 16-char id. `invoice.account_country` and
`account_name` derive from it rather than from constants.

`manage_stripe_accounts()` returns `{"reconsent_url": "https://access.stripe.com/mcp/oauth2/authorize/sessions/oases_…"}`
with a deterministic id from `ctx.ids`.

## 7. Serialization completeness

Three groups, measured (functional spec §11.1), and each has a different fix:

| Group | Count | Fix |
|---|---|---|
| In the schema, never serialized | 20 | Add to the resource's field map. Ordinary work; conformance already permits them |
| In the full spec, absent from `spec3.min.json` | 6 (the `account` object) | **Pruner fix**: `account` is absent because `GetAccount` is not routed. The pruner must retain schemas reachable from *tool outputs*, not only from routed operations |
| In neither | 3 (`product.attributes`, `product.type`, `product.tax_details`) | **Not emitted** (functional spec §11.2). A test asserts their absence, so a future contributor does not "fix" it |

The generated check: a test walks objects captured in the probe, and for each field present there
and absent from our output, fails with the field name — so the next missing field is a test failure
rather than a discovery.

## 8. Testing

Two new kinds, both generated rather than hand-written.

### 8.1 Surface conformance

The cheapest test in the project and the one covering the largest cluster of blatant tells. The
captured real schemas are committed as data (`tests/surface/real_tool_schemas.json`); the test
registers the world, reads `inst.tools()`, and asserts per tool:

- the name is present
- the description is byte-identical
- the input schema's `properties` key set, per-property `type`, `required` list, `default`,
  `minimum`/`maximum` and `examples` all match

Failure prints a diff. This is what makes §2.2 and §2.3 hold under future edits.

### 8.2 Refusal conformance

Generated from `mcp_catalogue.jsonl`. For every record, assert the world's answer lands in the right
bucket: `absent` → the operation-gate string; `catalogued` and unrouted → B1 or B2 by the product
table; `catalogued` and routed → not a refusal. A routing change cannot silently move an operation
between buckets.

Sampling is honoured: operations the enumeration did not cover are skipped with a reported count,
never asserted.

### 8.3 Naming

Every closed tell gets a test named for its register id — `test_ts_04_read_takes_operation_id` — so
a reader can go from the register to the test. A tell with no test is not closed (functional spec
§15).

## 9. Risks

- **The catalogue enumeration is the long pole**, and it is the only step that cannot be parallelised
  or shortened by better design: several hundred to a thousand live calls, once. Everything in §4
  and §5 depends on its output, so it should run early and in the background rather than blocking.
- **Search ranking will not match.** Declared, and the failure mode is benign — a plausible order
  rather than the real one.
- **The `livemode` sweep touches ~15 modules** and is the most likely place for a missed literal.
  The grep test is the guard; it should be written before the sweep, not after.
- **`human_confirmation` is accepted and ignored.** If a real operation this world routes turns out
  to demand approval, an agent would meet a flow here that does not exist. Nothing measured says
  which operations trigger it.

## 10. Component designs

The material above is the architecture. Three areas have enough internal detail to warrant their own
document, and one is large enough that writing it inline would bury everything else:

| Component | Why it needs its own doc |
|---|---|
| `components/tool_surface.md` | Eight tools × exact schema, description, validation order and return path. Mechanical, long, and the thing a coding agent will read most often |
| `components/catalogue_and_refusal.md` | The enumeration script's CLI and resume logic, the artifact schema, the product table, and the bucket routing decision tree |
| `components/discovery.md` | The pruner's second output, the index format, the scoring function with worked examples, and the details document assembly |

`livemode`, ids, the account object and the serialization sweep stay here — they are each a page and
they interlock through `ctx.state["account"]`, so splitting them would scatter one decision across
four files.
