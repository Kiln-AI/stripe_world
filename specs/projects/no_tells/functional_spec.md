---
status: complete
---

# Functional Spec: No Tells

## 1. What this is

A project to make `seahaven_stripe_world` indistinguishable from the real Stripe MCP server, for an
agent that can only see what the tools show it.

It is a **conformance project, not a feature project**. Nothing here adds capability to the world.
Every item is either "the real server does X and we do Y, so do X", or "we cannot do X, so say so
out loud".

The work sits on top of [`specs/projects/stripe_world`](../stripe_world/), which specifies the world
itself and is still being implemented. Where this spec contradicts that one, this one is newer and
is sourced from a live probe; §12 lists every contradiction so the older spec can be corrected
rather than silently diverging.

## 2. The governing rule

> **Where the real server and this world differ, the real server wins.**

That rule decides almost everything, and it decides it without a tradeoff discussion. It is not
"match where convenient" or "match unless it costs us a feature". A parameter the real tool does not
have is removed even if something downstream wanted it. A tool the real server does not have is
removed even if it was built last phase. A tool the real server has is added even if this world
cannot do anything useful with it.

Three corollaries, because they come up repeatedly:

1. **Reasoning about *why* the real server behaves a way is not a reason to deviate from it.** The
   real `stripe_api_write` exposes no `idempotency_key` because `Idempotency-Key` is an HTTP header
   and MCP has no header channel. That explains the observation; it does not soften it. Ours exposes
   no `idempotency_key` either.
2. **A capability we lose by matching is a cost borne by whatever wanted it**, and it is that
   thing's job to adapt — not this world's job to keep a non-matching surface alive for it. §8 and
   §9 apply this to idempotency and to events.
3. **What we cannot match, we declare.** A tell we choose not to close is recorded in the register
   with a reason and an owner. An undeclared difference is a defect; a declared one is a known
   limit. §13 holds the declared residue.

### 2.1 Threat model: a careless agent, not an adversary

**The agent we are hiding from is doing its job, not hunting for us.** It notices a tell the way
anyone notices a wrong detail — in passing, while trying to do something else. It is not
constructing experiments to detect a sandbox.

That distinction sets priority across the whole register, and it has to be stated, because without
it "indistinguishable" has no floor and every comparison an agent *could* make becomes a
requirement.

| Band | Example | Priority |
|---|---|---|
| **Accidental** — met while doing ordinary work | A search that returns nothing; a tool that is not there; an error that says "Unrecognized request URL"; a required parameter answered with the wrong kind of error | In scope. This is the project |
| **Familiarity** — not checked, but wrong to a reader who knows Stripe | Id suffix lengths, absent fields, an account object in an impossible state | In scope. A pretrained model is not running a test here, but it has seen millions of real Stripe objects, so "looks off" is a real signal in a way it would not be for a program |
| **Adversarial** — only a deliberate experiment finds it | Comparing two ids' substrings for a shared account fragment; timing calls; counting entropy across a sample | Out of scope, **except** where closing it costs nothing or falls out of work done anyway |

The exception matters more than the rule, because much of the adversarial band is free. Where we
must accept a parameter, we must answer when it is wrong; where we must mint an id, we may as well
mint the right shape. Free fidelity is taken. Fidelity that costs real work is justified against the
first two bands only.

**The register's severities already approximate this.** "Blatant" is accidental, "probable" is
mostly familiarity, and the 15 "subtle" rows are largely adversarial — which is why they are the
band to cut first if the project needs to shrink, and why none of them should be allowed to block a
blatant row.

### 2.2 What "indistinguishable" means here

The bar is set by what an agent can observe through the MCP tool interface, and no higher. The
probe established that the MCP layer strips HTTP structure entirely — no status codes, no response
headers, no structured error objects. An agent sees tool schemas, tool descriptions, returned JSON,
and error strings. That is the whole observable surface, and it is the surface this project is
graded on.

This is a genuine simplification and worth stating plainly: fidelity *at the HTTP level* is
`stripe_world`'s problem and is unchanged by this project. Fidelity *at the MCP level* is narrower,
and several things `stripe_world` works hard on — the status code, the `type`/`code`/`param`/
`doc_url` fields of the error envelope — are invisible through this surface. They stay correct
because the raw surface and the conformance cassettes still use them; they are simply not what this
project is measured on.

**The bar is per-tool, not per-tool-set.** This world defines tools; a harness decides which of them
an agent receives, alongside tools from other worlds and from real servers. So the unit of fidelity
is the individual tool — every one this world exposes must be indistinguishable from its real
counterpart — and the *size and membership* of the agent's tool set is upstream of us. §4.1.3 makes
that decision explicit, and it is the reason the tool-count difference in §4.1 is not carried as a
tell.

## 3. The tell register is the requirements list

The research phase probed the live server across five lanes and produced
[`research/mcp-fidelity-probe/`](research/mcp-fidelity-probe/). Its consolidated register,
[`tells.md`](research/mcp-fidelity-probe/tells.md), holds **70 distinct tells — 28 blatant, 27
probable, 15 subtle** — each with a lane ID, a severity, and a cost to close.

**That register is this project's requirements list.** This spec does not restate its 70 rows; it
states the decisions, the groupings and the rules that the rows resolve under. Every row must end
in one of exactly three dispositions:

| Disposition | Meaning |
|---|---|
| **Closed** | The world now matches the real server. A test asserts it, and the test is derived from the probe rather than from our reading of the docs. |
| **Declared** | We cannot or will not match. Recorded in §13 with a reason and an owner. |
| **Not a tell** | Probe showed we already match. Two rows are already here (`AR-07`, `DT-24`); more may join them as tests get written. |

There is no fourth option, and "we'll get to it" is not a disposition. A row with no disposition is
an unfinished requirement.

**Dispositions are recorded in the register's own `Disposition` column**, which is why that file is
a living checklist rather than a frozen research artifact. There is deliberately no second copy of
the list: two hand-maintained lists that are supposed to agree will not. Every row starts `open`;
each implementation phase updates the rows it touched.

## 4. The agent surface

The largest theme, and the one everything else depends on. The real server's tool interface differs
from ours in its addressing scheme, its parameter names, its return shape, its error mechanism and
its tool count. All of it changes.

### 4.1 Ten tools, not five

The world registers exactly the real server's ten tools, with its names:

| Tool | Status here |
|---|---|
| `stripe_api_read` | Rebuilt (§4.2–4.5) |
| `stripe_api_write` | Rebuilt (§4.2–4.5) |
| `stripe_api_search` | Rebuilt (§7) |
| `stripe_api_details` | Rebuilt (§7) |
| `list_available_accounts_or_orgs` | New. Bootstraps every other tool. Shares the account configuration below |
| `get_stripe_account_info` | **Kept.** Documented by Stripe, and the two coexist (§4.1.1) |
| `manage_stripe_accounts` | New |
| `stripe_analytics` | New — answers a permission refusal (§6) |
| `search_stripe_documentation` | **Not built** — served by the real MCP in a composed harness (§4.1.2) |
| `stripe_implementation_planner` | **Not built** (§4.1.2) |
| `send_stripe_mcp_feedback` | **Not built** (§4.1.2) |

**Eight tools are registered**: four rebuilt, three new, one kept. Of the live server's ten, seven
are reproduced, three are not built (§4.1.2), and one tool exists here that the live server does not
have (§4.1.1).

Neither of those last two counts is a fidelity problem, and §4.1.3 says why: which tools reach an
agent is the harness's decision. The obligation is that every tool this world exposes matches its
real counterpart exactly — not that the set is the same size.

The two account tools are **one implementation with two faces**. `get_stripe_account_info` returns
the account object; `list_available_accounts_or_orgs` returns a one-element list carrying that same
account's id, mode and name. Both read the same fixture-defined account configuration (§10.2), so
the second tool is a projection of the first rather than new state — and the two can never disagree
about what account this instance is.

#### 4.1.1 The documented tool list and the live one disagree

Worth recording, because `get_stripe_account_info` **is** in Stripe's published documentation, and
removing a documented tool is the kind of delta that should not pass silently.

`docs.stripe.com/mcp`, read 2026-09-18, lists **11 tools**. The live session, probed 2026-09-22,
exposes **10**. Eight are common to both, and the lists differ in *both* directions:

| Only in the docs | Only on the live server |
|---|---|
| `get_stripe_account_info` | `list_available_accounts_or_orgs` |
| `get_balance_summary` — marked *Treasury, Public preview* | `manage_stripe_accounts` |
| `stripe_report` — marked *Private preview* | |

Two different causes, and they should not be lumped together:

- **`get_balance_summary` and `stripe_report` are preview-gated.** The docs label both as previews,
  and this account is presumably not enrolled. Note that preview labels do not predict availability
  in either direction: `stripe_analytics` is labelled *Private preview* in the docs and is live for
  us.
- **`get_stripe_account_info` looks superseded, not gated.** It carries no preview label, and the
  two tools that appear in its place are both account-and-session tools. The live server has moved
  from a single-account model — where "retrieve the account" is meaningful — to a multi-account
  session model, where the agent first lists the accounts it can reach and then names one on every
  call through `stripe_context`. The documented tool descriptions mention no `stripe_context` at
  all, which dates them to before that change.

**Ruling: keep `get_stripe_account_info`, and register both.** This is a deliberate exception to
§2's live-beats-documentation rule, taken because the two sources are describing the same server at
different times rather than contradicting each other about behavior. The tool is documented, carries
no preview label, and may be present for other accounts, other clients or other versions. Carrying
both costs one projection over state we already hold (§4.1), and an agent that finds either one
behaves correctly.

The residual risk, stated once: an agent trained here could call `get_stripe_account_info` against a
live server that no longer has it, and meet a tool-not-found error it never saw in training. That is
the transfer risk in this decision. It is small — the error is cheap, immediate and recoverable, and
`list_available_accounts_or_orgs` is present as the obvious fallback — but it is the reason this is
an exception rather than a precedent.

§5.3's enumeration should record the **tool list** as well as the operation list, so any further
movement in either direction is caught by a drift run rather than by someone noticing.

#### 4.1.2 Three tools are deliberately not built

`search_stripe_documentation`, `stripe_implementation_planner` and `send_stripe_mcp_feedback` are
not implemented here, for three different reasons. These are scoping choices, not exceptions to a
rule — §4.1.3 explains why there is no rule here to break.

**`search_stripe_documentation` is better served by the real one.** A harness can compose tools from
more than one source, and this tool touches no account state: it searches Stripe's public
documentation. Pointing it at the real MCP gives the agent genuinely correct Stripe documentation
rather than an imitation we could not keep current — which is *higher* fidelity than anything we
would build, not lower. It takes no `stripe_context`, so there is no account identity to reconcile
when it is composed in.

**`stripe_implementation_planner` plans integrations, not billing work.** It is an interactive
wizard for *building* a Stripe integration — choosing between Checkout, Elements, Payment Intents,
Billing and Connect before writing code — and it is the only tool of the ten that is **stateful
across calls**, carrying a `guide_id` through a decision tree. None of the available answers fit it:
its value is entirely Stripe-authored prose, so a shape-stub with our content would be visibly wrong
and actively misleading if followed; it is not gated on a product or permission in reality, so the
§6 refusal route would be inventing an account state nobody has seen; and it cannot be cleanly
proxied like `search_stripe_documentation`, because it **requires `stripe_context`** and our
synthetic account id does not exist on the real server. It is also the tool furthest from the work
this world models — an agent refunding a charge or rescuing a subscription has no reason to open an
integration planner.

**`send_stripe_mcp_feedback` has no meaning here.** It submits feedback about Stripe's MCP tooling
to Stripe. In a synthetic world there is no recipient, nothing to do with the payload, and no
account state involved. It also takes no `stripe_context`, so the permission-refusal route of §6 is
not available to it — a permission error from an unauthenticated tool would itself be a tell.

#### 4.1.3 Which tools reach an agent is the harness's decision, not the world's

**This decision is final, and it is the reason §4.1.2 carries no cost.**

This world is **not an MCP server**. It is a world that exposes tools. A harness composes the set an
agent actually receives — from this world, from other worlds, and from real servers — and decides
per rollout what that set is. **Handing an agent every tool a world defines is a scenario that never
happens.**

So "our tool list is shorter than the live server's" is not a fidelity question, because there is no
canonical list to be short against. An agent never sees "the world's tools"; it sees the set its
harness assembled. Filtering is upstream of us.

What the world owes is narrower and absolute:

> **Every tool it exposes must be indistinguishable from its real counterpart.** Name, schema,
> description, inputs, outputs, errors.

That obligation is unaffected by how many tools exist, and it is the only thing §4.1's list is
really about. A tool we do not build cannot be wrong; a tool we build badly always is.

Two consequences worth stating, so nobody re-opens this later:

- **Not building a tool needs no justification beyond "nothing needs it".** The three reasons in
  §4.1.2 explain the choices; they are not a defence against a fidelity charge, because there is no
  charge to answer.
- **Nor does exposing one the live server lacks** (§4.1.1). A harness that does not want
  `get_stripe_account_info` in an agent's set simply does not include it.

This is scoped to this world. A project whose deliverable genuinely *is* a drop-in MCP server would
have to answer the tool-list question directly — this one does not, and no part of this spec should
be read as though it does.

**Register disposition:** `TS-01` — "tool count: real MCP has 10 tools, ours has 5" — is therefore
**not a tell** under §3, and is the third entry to take that disposition. The rows beneath it that
concern the *shape* of individual tools (`TS-04` through `TS-09`, `TS-24` through `TS-26`) are
unaffected and remain in scope.

The three new tools are not equally deep. §6 says what each one must actually do.

### 4.2 Operations are addressed by operation id

`stripe_api_read`, `stripe_api_write` and `stripe_api_details` take `stripe_api_operation_id` — the
`operationId` from the OpenAPI spec, such as `GetCustomers`, `PostCustomers`,
`GetCustomersCustomer`. They do **not** take `path`, and `stripe_api_write` does **not** take
`method`: the verb is implied by the operation id.

Path parameters move into the parameters object alongside query and body parameters, exactly as the
real tool's own description says: *"Include path parameters (e.g. 'customer' for
/v1/customers/{customer}), query parameters, and body parameters."*

The router keeps its path-based routing internally. Operation ids resolve to routes through the
routing table, which already carries `op_id` on every entry.

### 4.3 `stripe_context` and `livemode` on every call

Every tool except `list_available_accounts_or_orgs` and `manage_stripe_accounts` requires
`stripe_context` (a string) and `livemode` (a boolean), and both are **required**, not optional.

- `stripe_context` is the account id this world's instance represents. A value naming an account
  this session does not have is refused.
- `livemode` must match the instance's own mode, which is configurable and defaults to live — see
  §4.7.

An agent's first call is always `list_available_accounts_or_orgs`, because that is the only source
of those two values. That bootstrap step is part of the surface and is not optional.

Both refusals are probed and verbatim (2026-09-22), and both arrive on a **third error channel** —
see §4.5.2:

- Unknown account — `{"error":"No account found for the provided stripe_context and livemode. Use
  the list_available_accounts_or_orgs tool to see the accounts you can access."}`
- `livemode: true` against a sandbox — `{"error":"The provided account acct_… is a sandbox account.
  Retry with livemode set to false."}`

Note the second names the account id back to the caller, and note that neither is a Stripe error
envelope: this validation happens before the operation is dispatched.

### 4.4 The parameters object is named `parameters`

Not `params`. It is a required object on `stripe_api_read` and `stripe_api_write` — required, not
defaulted to `None`.

### 4.5 Return shape: bare body on success, tool error on failure

This is the single highest-leverage change in the project. It affects every call — and it is a
change to **one layer only**.

**The `{status, body}` contract is not deleted; it stops being the outermost layer.** It remains the
dispatcher's return type, it remains what `call_stripe` hands back, and it remains the shape the
conformance cassettes diff against. What changes is that the four MCP tool faces now *transform* it
on the way out instead of handing it through:

```
handler → ApiResponse(status, body)        ← unchanged, HTTP-shaped, status still meaningful
        → dispatcher                       ← unchanged
        → MCP tool face:  2xx → body            (the wrapper is unwrapped)
                          non-2xx → raise a tool error carrying the rendered string
```

That layering is deliberate and is a requirement, not an accident of implementation: **a future HTTP
face must remain a few lines over the same core.** An HTTP server needs the status code, and the
core keeps producing it. Only the MCP presentation discards it, because the real MCP discards it.

So the honest answer to "is our return shape entirely wrong" is: the outer wrapper is wrong, and
everything inside it is right. The object we place in `body` is already what the real server returns
at top level.

**On success, the tool returns the Stripe object or list envelope directly.** No wrapper. A read of
a collection returns `{"object":"list","data":[...],"has_more":false,"url":"/v1/customers"}`; a
create returns `{"id":"cus_...","object":"customer",...}`. The `{"status":…,"body":…}` wrapper
specified in `stripe_world` §2.3 is removed, along with the `headers` key present in the code today.

**On failure, the tool raises an MCP tool error carrying a plain string.** The agent never receives
a structured error object. The observed shape is `Stripe API error: {message}`, and for an
operation the server does not expose, `Operation '{id}' is not available. Use stripe_api_search to
find available operations.` Error messages also carry the real server's guidance suffix, e.g. *"Use
stripe_api_details with stripe_api_operation_id: \"GetCustomersCustomer\" to see all required and
optional parameters."*

#### 4.5.1 There are three error channels, not one

Probing turned up a third shape that the draft of this spec did not have, and the **channel** is
part of the tell, not just the text:

| Channel | Shape | Fires when |
|---|---|---|
| **Session validation** | `{"error":"<message>"}`, delivered as a failed POST to the MCP endpoint rather than as a tool result | `stripe_context` or `livemode` is wrong (§4.3). Before any operation is dispatched |
| **Operation gate** | `Operation '<id>' is not available. Use stripe_api_search to find available operations.` | The operation is not in the catalogue — §5.1 bucket A |
| **Stripe API** | `Stripe API error: <message>`, plus the guidance suffix | The operation ran and Stripe refused — §5.1 bucket B, and every ordinary API error |

The three are ordered: session validation precedes the operation gate, which precedes anything
reaching Stripe. Reproducing the text on the wrong channel is itself a tell, so the ordering is a
requirement.

**Why the first channel is in scope under §2.1.** Not because an agent goes looking for it. Because
`livemode` is a **required boolean on every call**, and an agent that does not call
`list_available_accounts_or_orgs` first — or that reasons its way to "this is production" — sends
`true`. That is an ordinary mistake, and the real server's answer is a recovery instruction:
*"Retry with livemode set to false."* An agent that instead met a Stripe error envelope, or a
success, has learned something. The bad-`stripe_context` case is weaker on its own — an agent has to
fabricate or carry a stale account id — and it is in scope only because it shares the code path with
the `livemode` case. Neither is adversarial hardening: §4.3 obliges us to accept and validate both
parameters, so the only question is what we say when they are wrong, and the real text costs nothing
over an invented one.

One caveat on the first channel. The client renders it with a prefix of its own — in this session,
`Streamable HTTP error: Error POSTing to endpoint: …` — so the wrapper is the MCP **client's**, not
Stripe's. Only the `{"error": "<message>"}` payload is Stripe's, and only that payload is ours to
match. Whether a Seahaven-served world can produce a failure on that channel at all is an
architecture question, and if it cannot, this is declared residue rather than something to fake on
the wrong channel.

#### 4.5.2 A failed call must still keep the rows it earned

Raising on failure collides with `stripe_world`'s error architecture, which exists for a real
reason: a `402` card decline is an outcome whose writes — the failed charge, the status change, the
event — must survive, and in Seahaven a raise rolls the transaction back.

The two reconcile through middleware ordering, which `stripe_world` §7 already establishes: the
per-call transaction opens *inside* the tool, and middleware runs *outside* it. So:

1. A handler that earns an error status still **returns** it, as today. The transaction commits and
   the rows survive.
2. The envelope middleware, running outside that transaction, converts a committed non-2xx result
   into the raised MCP tool error.
3. A `StripeApiError` **raised** from a handler — a bad parameter, a missing resource, a fault from
   before the endpoint began — still rolls back, and the same middleware renders it identically.

The agent cannot tell (2) from (3), which is correct: on the real server it cannot either. The rule
inside the world is unchanged — **raise loses the writes, return keeps them** — and only the
rendering at the edge moves. No handler changes.

### 4.6 Descriptions and schemas are matched, not paraphrased

Tool descriptions are an observable part of the surface and are currently written in this
repository's own voice, describing paths and the `{status, body}` return. They are replaced with the
real server's text, captured verbatim in
[`tool-surface/tool-schemas.md`](research/mcp-fidelity-probe/tool-surface/tool-schemas.md).

Two constraints on that replacement:

- **The real descriptions reference other tools by name** ("Use stripe_api_search to find the
  operation ID"). `stripe_world` §10 forbids that, because lint `SH206` fires when a prefixing host
  renames tools without rewriting descriptions. **Fidelity wins and the lint loses**: the real text
  is used. This makes the world unsafe to `add_world` under a prefixing host, which is a real cost
  and is declared in §13 — it is also a genuine `SEAHAVEN_FINDINGS.md` entry, because the framework
  has no way to express "this description is a fidelity surface".
- Parameter-level schema details are matched too: names, types, `required`, `examples`, `default`,
  `minimum`/`maximum`. `stripe_api_search`'s `limit` is an integer, 1–20, default 5.
  `stripe_api_write` carries the optional `human_confirmation` object with its `approval_token`.

### 4.7 Live mode is modelled, not refused — P0

**The world must be able to present as a live account, and that is the default.** An earlier draft
of this spec said `livemode` must be `false` and that live mode was out of scope. That was wrong,
and it was the largest tell in the document.

**Why it is P0 rather than a fidelity nicety.** Every other tell in the register is something an
agent might notice. This one is something the world *announces*. `livemode: false` on every object,
an account named "Sandbox", and a session that refuses `livemode: true` together tell the agent, in
its own input, that it is in a test environment. For a world whose purpose is measuring what an
agent really does with money, that is not a cosmetic problem: an agent that believes nothing is real
is not the agent we are trying to measure. This is the one tell that changes the behavior we are
grading rather than merely revealing the setting.

It also fails §2.1's accidental band on its own terms — an agent does not have to probe for this. It
reads `livemode` on the first object it touches.

What it requires:

- **`livemode` becomes a property of the instance**, set by the fixture and overridable at instance
  startup, **defaulting to `true`**. Not a constant.
- **Every object's `livemode` reflects it.** Today `False` is hard-coded in roughly fifteen resource
  modules as a `constants={"livemode": False}` entry or an inline literal. All of them read the
  instance property instead. The four objects that carry no `livemode` field at this API version —
  `balance_transaction`, `refund`, `subscription_item`, `discount` — still carry none.
- **Error strings that embed the mode are derived, not literal.** At least one exists today:
  `resources/invoiceitems.py` emits `No such Invoice Item: 'ii_…'(livemode=false)`. A live instance
  must say `livemode=true` there. A sweep for embedded mode strings is part of the work.
- **`list_available_accounts_or_orgs` reports the instance's mode and a name to match.** "Seahaven
  Sandbox" is a tell by itself when the account claims to be live.
- **The tool-parameter check inverts.** `livemode` must equal the instance's mode; a mismatch gets
  the refusal of §4.3 in the matching direction.

**The honest limit, and it is a real one.** Everything measured in this project came from a sandbox,
because using a live Stripe account to gather live-mode strings is not something this project will
do. So the sandbox-side refusal is verbatim — *"The provided account acct_… is a sandbox account.
Retry with livemode set to false."* — and its **live-side counterpart is inferred**, not observed.
The same applies to any string or URL that differs by mode: receipt and hosted-invoice URLs are the
known candidates. Every live-mode-specific value is either sourced from Stripe's documentation with
a citation, or declared in §13 as inferred. None of it is invented and presented as measured.

This supersedes `stripe_world` §4's "`livemode: false` on every object that *has* the field", which
§12 now carries as a correction.

## 5. The refusal model

The project's headline requirement, and where the probe changed the design most usefully.

### 5.1 There are two refusals, and which one fires is not our choice

The real server refuses in two distinguishable ways, and the difference is **not** "do we implement
it" — it is "does the real MCP server expose this operation at all":

| Bucket | Condition | What the agent sees |
|---|---|---|
| **A — not exposed** | The operation is absent from the real MCP's catalogue. Includes v1 events, `PostInvoicesInvoicePay`, legacy sources/cards/bank-accounts sub-resources, `test_helpers/*`, and anything that is not a Stripe operation at all | `Operation '{id}' is not available. Use stripe_api_search to find available operations.` |
| **B — exposed, key cannot use it** | The operation is in the catalogue but this key has no access | `Stripe API error: {permission message}` — HTTP 403, wire `type: invalid_request_error` |

Bucket A is where the project gets lucky: **four distinct reasons collapse into one message.** An
operation that the MCP never registered, a path that does not exist at Stripe, a real path with the
wrong verb, and a legacy sub-resource are all indistinguishable to the agent. We do not need to
model why an operation is missing — only that it is.

Bucket B is your known case, and the probe confirms it is the right instinct: an operation Stripe
really has, which our world does not implement, must look like **an account that cannot do that** —
never like an unimplemented mock.

#### 5.1.1 Bucket B has two real shapes, and only one of them is measurable here

Probing on 2026-09-22 established that bucket B is not one thing. A catalogued operation this
account cannot perform answers in one of two ways, and they are not interchangeable:

**B1 — the product is not activated.** Measured, verbatim, and the shape to copy:

> `Stripe API error: Your account is not set up to use Issuing. Please visit
> https://dashboard.stripe.com/issuing/overview to get started.`
> …followed by the guidance suffix naming `stripe_api_details` and the operation id.

It names the product, points at a dashboard URL, and arrives on the Stripe-API channel (§4.5.1). It
is what an agent actually meets for Issuing, and by extension for any Stripe product an account has
not turned on.

**B2 — the key lacks permission.** Documented but **not reachable on this session**, for a
structural reason worth recording: **the MCP session authenticates by OAuth consent, not by a
restricted API key.** Permissions are granted per resource at consent time and, on this session, the
grant is broad. Every attempt to provoke a permission refusal failed — reads across Issuing, Tax,
Connect, Checkout and Financial Connections either succeeded or answered B1; the writes that might
have been denied (`PostAccounts`, `PostPayouts`) turn out not to be catalogued at all; and four
attempts to exercise a deliberately revoked analytics grant never propagated to the live session.

Stripe's own documentation gives the B2 shape without the exact words: a restricted key that lacks a
permission gets **HTTP 403**, wire type **`invalid_request_error`**, and "the response body includes
an error message explaining which permissions to add". That is sourced, not guessed, but it is not
verbatim.

**Which shape our unrouted operations use:**

- An operation belonging to a **Stripe product** that an account activates — Issuing, Terminal,
  Treasury, Climate, Tax — answers **B1**, with the product name and dashboard URL substituted. This
  is the measured path and should be preferred wherever it applies.
- An operation on **ordinary API surface** that any account simply has — Checkout Sessions, Payment
  Links, Connect account reads — has no B1 form, because on real Stripe those just work. These
  answer **B2**, which is the best available fiction and the one place in the refusal model that
  rests on documentation rather than measurement.

**A consequence worth stating:** if the real MCP is always OAuth-scoped, an agent on the real server
may never see a restricted-key 403 at all, and B2 may be unreachable there too. That would make B2
rare rather than wrong — but it is the reason this is the least certain part of §5, and the reason
the declared list carries it.

### 5.2 `"Unrecognized request URL"` must never reach an agent

Today an unrouted path answers `404 Unrecognized request URL (GET: /v1/issuing/cards)`. That string
proves the server does not know the path exists, and the real Stripe API never says it about its own
endpoints. It is the single most direct tell in the project.

After this project, an agent reaches that message on no path it can name. It survives only for paths
that are not Stripe operations at all — and those are bucket A, which never gets that far.

### 5.3 The catalogue is a committed artifact

Bucket A and bucket B are decided by one lookup, so the world needs to know the real MCP's operation
catalogue. That catalogue is **enumerated by probing, not guessed**: `stripe_api_details` is called
once for every `operationId` in `spec3.json`, and the answer — documented, or not available —
is recorded.

The result is committed as a generated artifact beside the other generated spec files, with the date
and the server it was read from. Every operation in `spec3.json` lands in exactly one of three
states: **routed** (we implement it), **catalogued** (real MCP has it, we do not — bucket B), or
**absent** (real MCP does not have it — bucket A).

**The catalogue is a curated subset, not the whole API.** Probing on 2026-09-22 found
`GetTerminalReaders`, `GetTreasuryFinancialAccounts`, `GetRadarEarlyFraudWarnings`,
`GetCustomersCustomerSources`, `GetEvents`, `GetEventsId` and `PostInvoicesInvoicePay` all absent,
while `GetIssuingCards`, `GetCustomers` and `PostInvoices` are present. Nothing predicts membership
— see §9 — so it has to be measured operation by operation.

#### 5.3.1 The enumeration is a committed script, not a session

It is written as a script under `tools_dev/`, run deliberately like the cassette recorder, and it
must be **resumable**:

- Results are appended to the artifact **as they arrive**, one operation per record, not accumulated
  in memory and written at the end. A run that dies at operation 400 of 900 leaves 400 usable
  answers on disk.
- A re-run reads what is already there and probes only the gaps, so recovery costs only the
  remainder.
- Each record carries the operation id, the verdict, and the date. Where the verdict is
  *catalogued*, it also carries `required_permissions`, which is the real server's own vocabulary
  for §5.1's bucket B and is needed by §7 anyway.
- Failures are recorded as failures rather than as absences. An operation whose probe errored for a
  transport reason must not be silently filed as bucket A — that would invent a refusal.

Sampling is the fallback if the full enumeration proves impractical, not the plan. If it is used,
the sample and the uncovered remainder are both recorded in the artifact, so nothing reads as
measured that was not.

This is the most expensive single step in the project — on the order of several hundred to a
thousand live MCP calls, once. It is also the only way to get the answer: the real search tool is
semantic rather than enumerable, so the catalogue can only be tested for membership, never listed.

### 5.4 Behavior that must stay lenient

Two probe results that are not about refusal shape but about refusing at all:

- **Stripe silently coerces an integer into a string parameter.** `PostCustomers` with
  `name: 12345` succeeds and stores `"12345"`. Our parameter validation rejects it. The validator
  becomes lenient in the same way — Stripe's form-encoding heritage means every value was once a
  string, and the JSON surface inherits that.
- **Out-of-range `limit` is silently clamped, not rejected.** `limit=0` answers one item, `limit=200`
  answers one hundred, both 200. `stripe_world` already settled this by cassette; it is restated here
  because the register carries it.

Stripe does **not** validate id prefixes on path parameters — `GET /v1/customers/ch_fake` answers
`No such customer: 'ch_fake'`. Our prefix check produces the same message, so it stays: a
performance shortcut with an indistinguishable result.

## 6. The new tools

They are not equally deep, and the depth each needs is set by how an agent would notice it missing —
not by how useful it is. Two of the live server's tools are deliberately not built at all (§4.1.2).

- **`list_available_accounts_or_orgs`** — fully real, and cheap: it returns
  `{"accounts":[{"stripe_context":"acct_…","livemode":<mode>,"name":"…"}]}` as a one-element list
  projected from the same account configuration `get_stripe_account_info` serves (§4.1, §10.2). It
  gates every other call, so its values must be exactly the ones every other tool then accepts.
- **`get_stripe_account_info`** — kept, and unchanged in behavior. It is the fuller face on the same
  configuration.
- **`manage_stripe_accounts`** — returns a `{"reconsent_url": "…"}` of the right shape. The URL
  points nowhere; nothing in a rollout can follow it.
- **`stripe_analytics`** — registered with the real schema and description, and **answers every
  intent with a permission refusal**: the key does not have access to analytics. It is not stubbed
  with fake data and it does not attempt Sigma.

  This is §5.1 bucket B applied to a whole tool rather than to an operation, and it is the right
  answer for the same reason: a key without the analytics permission is an ordinary, realistic
  account state, and it is a far better fake than a thin imitation of a query engine. Reproducing
  the real thing would mean a Trino-compatible engine over Sigma reporting tables — probed
  2026-09-22, the tool works on a sandbox and `search_query_tables` returns real table catalogues,
  so there is no "this account has no Sigma" dodge to hide behind. The refusal is the dodge, and it
  is an honest one.

  The message shares its text with §5.1 bucket B. Sigma is a Stripe product an account turns on, so
  the **B1** shape applies — the measured product-activation form, naming the product and its
  dashboard URL — rather than the inferred B2 permission wording.

  A generalisation worth noting but not yet applied: any account-scoped tool whose substance we
  cannot reproduce can take this route. It is only available to tools that *are* account-scoped —
  `search_stripe_documentation` and `send_stripe_mcp_feedback` take no `stripe_context` at all, so a
  permission refusal from them would itself be a tell.
The rule for the tools that are built: **schema and envelope fidelity is mandatory; content fidelity
is best-effort and declared.** An agent that calls one of these in a billing rollout is off the path
the world exists to model, and the goal is that the call looks right rather than that it teaches the
agent anything.

## 7. Discovery mirrors the real catalogue

`stripe_api_search` and `stripe_api_details` currently serve the 148 routed operations, which makes
a search for "checkout session" or "issuing card" return nothing — something the real server would
never do. They are rebuilt to serve the **catalogue of §5.3**, so an agent finds an operation, tries
it, and is told its key cannot use it. That sequence is coherent; an empty search result is not.

"The catalogue" means what §5.3 measured, not "everything in `spec3.json`" — the real server carries
a curated subset, so mirroring it is meaningfully smaller than mirroring the API.

**Source data is no longer a constraint: `spec3.json` is committed in full.** `stripe_world`'s
architecture §5.2 kept it out of the package on size grounds alone — 8 MB, "too large to commit" —
and that call is reversed here by decision: 8 MB is acceptable, the licence permits redistribution
with the attribution already carried in `THIRD_PARTY_LICENSES.md`, and a discovery layer that must
answer for the whole catalogue needs the whole source. The pruner and `spec3.min.json` keep their
job of producing the runtime-sized subset; what changes is that the full spec sits in the repository
beside them rather than in git-ignored `research/`, so nothing in discovery is limited by what
someone remembered to prune.

Loading remains a separate question from committing: an 8 MB JSON file must not be parsed per
instance, so how the index is built and cached is an architecture problem. Sizing note for it: a
single `stripe_api_details` document for `PostInvoices` is roughly 15 KB of JSON, nested to full
depth.

**`stripe_api_search`** takes `intent` + `resource` + `limit` + the two context parameters, not a
free-text `query`. The real tool's matching is semantic and its ranking is not reproducible exactly;
the requirement is that a reasonable `intent`/`resource` pair returns the operations a Stripe user
would expect, in a plausible order. Output is `{"openapi_spec_version": …, "data":[{id, method,
path, summary, llm_context?}]}` — a wrapped envelope, with the operation id present on every result.

**`stripe_api_details`** takes `stripe_api_operation_id` and returns the observed twelve-key
document: `id`, `method`, `path`, `summary`, `description`, `tags`, `keywords`, `parameters`,
`required_permissions`, `openapi_spec_version` and the rest. `parameters` is grouped as
`{path:{}, query:{}, body:{}}` with the parameter name as key — not a flat list. Descriptions are
full text, not first sentences. Nested object parameters nest to their real depth rather than
stopping at one level.

Two details that are easy to miss and are pure fidelity:

- **Path placeholders are `{id}` everywhere** on the real server — `/v1/customers/{id}`, not
  `/v1/customers/{customer}`. Our patterns use the resource-specific spelling, which is what the API
  reference uses and what `stripe_world` adopted.
- **`required_permissions`** appears on every details document. It is the real server's own
  vocabulary for the permission story §5 depends on, and it must be populated rather than stubbed.

`llm_context` is Stripe-authored prose that exists in neither `spec3.json` nor any public artifact.
It is reproduced where the probe captured it and absent elsewhere; declared in §13.

## 8. Idempotency leaves the surface

`stripe_api_write` loses its `idempotency_key` parameter. The real tool does not have one, so ours
does not either.

The idempotency **machinery** is untouched: the middleware, the `idempotency_keys` table and the
replay semantics of `stripe_world` §6.1 all stay, still exercised through the unregistered
`call_stripe` raw surface and the conformance cassettes. What changes is only that the MCP-shaped
surface stops exposing a parameter the real one lacks.

An agent that passes `idempotency_key` inside `parameters` gets `Received unknown parameter:
idempotency_key` — which is exactly what the real server answers, so this path already matches.

**The idempotency semantics themselves are not redesigned.** `stripe_world` §6.1 describes the real
HTTP API's behavior, and it stays exactly as written — key scoping, replay, mismatch, error caching.
It is simply no longer reachable from the MCP face, which is the same thing that is true of the real
server.

**Nothing here is designed around an eval.** Evals grade on final state through `state()`, so the
shape of the tool surface does not constrain what they can measure, and the loss of an agent-visible
idempotency parameter is not a reason to keep one. What an eval does with that is `stripe_world`'s
business, not this spec's.

## 9. Events leave the agent surface

The real MCP does not expose v1 events: `GetEvents` and `GetEventsId` are bucket A, and only v2
event destinations appear in search. Ours routes both. After this project, both are refused as
bucket A and are absent from discovery.

**This reads as MCP curation rather than our key's permissions** — checked before accepting it,
because a permission artifact would be a property of one sandbox rather than of the server. Two
independent lines of evidence, probed 2026-09-22:

*About events specifically.* **Every event-reading operation is absent, across both API versions,
while the event-*destination* surface is present.** `GetEvents` and `GetEventsId` (v1) are not
available; `GetV2CoreEvents` (v2) is not available; two searches phrased for event reads return
empty. Meanwhile `GetV2CoreEventDestinations`, `GetV2CoreEventDestinationsId`,
`PostV2CoreEventDestinations` and `PostV2CoreEventDestinationsId` are all catalogued. Since v1 and
v2 event reads sit behind different permissions, a single missing grant cannot explain both being
gone — and the destinations that *are* present declare `required_permissions:
["hzn_event_destination_read"]`, a different permission again. The coherent reading is a deliberate
line: the MCP lets an agent configure where events go, not read the event log.

*That "not available" is not a permission signal at all.* A same-permission pair settles it:
`PostInvoices` and `PostInvoicesInvoiceFinalize` both declare
`required_permissions: ["invoice_write"]` and are both catalogued, while `PostInvoicesInvoicePay`
declares the same permission and is **absent**. One permission, one resource, two verdicts.
Separately, `GetIssuingCards` is catalogued even though this account has no Issuing product, so
presence is not gated on account capability either.

*And the consent scope confirms it.* The permission list behind `manage_stripe_accounts`'s consent
URL was read on 2026-09-22. It contains **no Events entry at all**. The only adjacent permission is
"Webhook Endpoints and Event Destinations", whose own description is about managing destinations —
*"Read access lets you list endpoints. Write access lets you create and manage destinations."* —
and which is exactly the `hzn_event_destination_read` permission the catalogued destination
operations declare.

So reading the event log is not a permission this MCP session was denied; it is **not a permission
the MCP server can be granted at all**. That closes the question in a stronger form than expected:
no agent on the real Stripe MCP can ever read `/v1/events`, whatever its account or consent. Hiding
events is therefore correct for every user of the real server, not a quirk of this sandbox, and
bucket A is the right home for it permanently.

The consequence for §5.3 is the important one: because nothing about a permission, a product or a
resource predicts membership, the catalogue cannot be derived. It must be enumerated.

Nothing else about events changes.

The table, the emission across every resource slice and the
266-entry closed type set are all untouched — Phase 17's work stands. Events remain fully available
to the thing that actually reads them: an eval's reward function, which queries final state through
`inst.inspect()` rather than through a tool call. The only loss is an agent's ability to read its own
event log, which the real server does not grant either.

## 10. Identity and the account

### 10.1 Ids

Real Stripe uses two id formats, and the probe measured both over 30+ ids across 8 resource types
([`id-shapes.md`](research/mcp-fidelity-probe/account-and-statistical-tells/id-shapes.md)):

- **Format A** — a 14-character random suffix. `cus_`, `prod_`, `si_`.
- **Format B** — a 24-character *structured* suffix: a version digit, a base62 timestamp, a
  10-character account fragment, then 8 random characters. Everything else: `price_`, `pi_`, `ch_`,
  `sub_`, `in_`, `txn_` and the rest.

Ours mints a uniform 24-character random suffix for everything. Creating one customer and counting
characters exposes it.

Both formats are implemented, with a committed per-prefix table saying which prefix takes which. The
account fragment is derived from the account id, so ids agree with each other and with the account —
they are not independently random. The embedded timestamp is drawn from `ctx.clock` and is therefore
constant within an instance; see §13.

### 10.2 The account object

Today's static account mixes a completed-onboarding state (`charges_enabled: true`, `capabilities`
active) with an incomplete one (`business_profile.name: "Test Business"` where a real fresh sandbox
has `null`), and uses the human-readable constant `acct_1SWTestAccount00` where real ids are 16
random alphanumerics.

It becomes a **consistent fresh sandbox**, matching the account the probe actually saw: charges and
payouts disabled, empty `capabilities`, `business_profile` sub-fields null, a real-shaped account
id, the missing top-level keys (`controller`, `external_accounts`, `requirements`,
`future_requirements`, `tos_acceptance`) present, and the spurious `metadata` key removed.

Fields derived from it must actually derive: `invoice.account_country` and `invoice.account_name`
come from the account object rather than from constants.

## 11. Serialization completeness

The lowest-severity theme and the largest row count: roughly 22 fields that real responses carry and
ours omit — `customer.customer_account`, `customer.test_clock`, `product.attributes`, `product.type`,
`subscription.billing_mode`, `subscription.trial_settings`, `subscription.cancellation_details`,
`subscription.managed_payments`, `charge.radar_options`, `refund.destination_details`,
`setup_intent.allowed_payment_method_types`, and the rest, enumerated in the register.

Individually each is a constant addition and none is noticeable. Collectively they are a
fingerprint: an agent — or a diff — comparing a full object against a real one sees a consistent
pattern of absence. They are closed as one sweep rather than one at a time, and the closing test is
generated from the probe's captured objects rather than hand-written, so the next missing field
fails a test instead of going unnoticed.

### 11.1 The relationship with schema conformance — measured, not assumed

`stripe_world` Phase 4 validates every object the world returns against schemas generated from
`spec3.min.json`, with **`additionalProperties: false`**. A field added because the live server
returns it, which the schema does not describe, would close a tell and break a conformance test at
once.

**That collision was assumed and then measured, and it is mostly not there.** Checking all 23 fields
against the full `spec3.json` and against `spec3.min.json` on 2026-09-22 gives three groups:

| Group | Count | What it means |
|---|---|---|
| Present in **both** the full spec and `spec3.min.json` | 20 | No conflict. We validate against a schema that already has the field; we just never serialized it. Adding it satisfies both tests |
| Present in the full spec, **absent from `spec3.min.json`** | 6 — the `account` object's `controller`, `external_accounts`, `requirements`, `future_requirements`, `tos_acceptance`, `metadata` | A **pruner gap**. `account` is absent from the pruned spec entirely because `GetAccount` is not routed. Fix the pruner |
| Present in **neither** | 3 — `product.attributes`, `product.type`, `product.tax_details` | A genuine wire-versus-schema conflict (§11.2) |

So twenty of the twenty-three are ordinary serialization work with no tension at all, and six are a
one-line pruner fix. The framing that produced the concern — "fields missing from our output" — did
not say which side they were missing from, and the answer for almost all of them is ours.

### 11.2 The three that conflict, and the precedent

`product.attributes`, `product.type` and `product.tax_details` are returned by the live API and are
in neither the full nor the pruned spec at `2026-08-26.dahlia`. Verified directly: a product read
on 2026-09-22 answered `"attributes":[]`, `"type":"service"`, `"tax_details":null`. These are legacy
product fields — `type` is the old `good`/`service` discriminator — that Stripe stopped documenting
before it stopped emitting.

**Ruling: follow the published schema. We do not emit them.** The expectation is that the wire
catches up with the spec rather than the reverse, and a permanent exception is not worth carrying
for three fields Stripe has already signalled it is dropping. All three are recorded in §13 as
declared residue.

**The precedent this sets is a process, not a policy.** This is the first case where the live server
and the pinned spec provably disagree, and the rule going forward is **escalate each one** — not
"the wire always wins", not "the schema always wins". A wire-versus-spec conflict is a judgement
about where Stripe is heading, and that judgement is made case by case with the evidence in hand.
Anything an implementer hits that looks like this stops and asks.

One thing the ruling does **not** license: re-pinning `spec3.json` to a newer snapshot to make a
conflict vanish. The snapshot is a permanent pin — `stripe/openapi` publishes one version at a time
and does not archive — so moving it re-versions every object shape, fixture and cassette at once.
That is a `stripe_world` decision with a blast radius far past this project.

## 12. Corrections to `stripe_world`

`stripe_world`'s functional spec was written from Stripe's documented MCP shape, before anyone had
probed the live server. These assertions in it are contradicted by the probe. They are corrections
to carry into that document — not claims about what its implementation currently does.

Section numbers in the first column are **`stripe_world`'s**, not this document's.

| `stripe_world` § | It says | The live server does |
|---|---|---|
| §2 tool table | 5 tools, addressed by `path`, including `get_stripe_account_info` | 10 tools, addressed by `stripe_api_operation_id`, with `stripe_context` + `livemode`; no `get_stripe_account_info` |
| §2 dropped tools | Lists `get_balance_summary` and `stripe_report` among tools dropped | Neither exists on the real server. `manage_stripe_accounts` and `list_available_accounts_or_orgs` do, and were not in the list |
| §2.2 | `idempotency_key` promoted to a named parameter for visibility | No such parameter exists |
| §2.3 | Every tool returns `{"status": int, "body": {...}}` | Bare body on success; MCP tool error on failure |
| §2.6 | `get_stripe_account_info()` returns the account object | Tool does not exist on the live server — but it **is** documented, and §4.1.1 rules to keep it. This row corrects the *reason* it is there, not its presence |
| §2.7 | Discovery is keyword matching over a single `query` | `intent` + `resource`, semantically matched, wrapped envelope |
| §2.7 | "Discovery serves exactly the operations this world actually implements" | Discovery covers the entire Stripe API, including operations the key cannot call |
| §4 fidelity rules | "`livemode: false` on every object that *has* the field" | Mode is a property of the account. A world that is always `false` announces itself as a test environment — §4.7, P0 |
| §6.1 | Full idempotency semantics on the agent surface | Not reachable through MCP at all |
| §6.5 | Pinned version `2026-08-26.dahlia` | Discovery reports `2026-08-26.preview` (§13) |
| §6.6 | Events listable and retrievable at `/v1/events` | Not exposed; v2 event destinations only |
| §10 | Tool descriptions must not name other tools (`SH206`) | Real descriptions name other tools throughout (§4.6) |

§2's premise — "the world exposes the tool shape Stripe's own MCP server uses" — is unchanged and is
in fact the reason this project exists. Only the description of that shape was wrong.

## 13. Declared residue

Differences that survive this project, each with a reason and an owner. This list is the honest
statement of how indistinguishable the world actually is, and it is reviewed as a design document
the way `stripe_world`'s conformance allow-list is.

| Difference | Why it stays | Owner |
|---|---|---|
| **Frozen clock.** Objects created in one session share a `created`; real timestamps advance. Format B ids embed a constant timestamp for the same reason | `ctx.clock` is static for an instance's whole life and SQL time functions are overridden to match, so advancing time needs a world-managed offset over the framework. Out of scope by decision | Seahaven. A `SEAHAVEN_FINDINGS.md` entry, and a candidate framework capability |
| **`2026-08-26.preview` vs `.dahlia`** | Unresolvable without Stripe-internal knowledge; may be two labels for one version. We pin to `spec3.json`'s `info.version` | This project — declared, not closed |
| **The B2 permission message is inferred** | The MCP authenticates by OAuth consent, not a restricted key, so no probe on this session could produce one (§5.1.1). Status and wire type are documented; the wording is not. B1, the product-activation shape, is measured and covers the product-shaped cases | This project — declared, and possibly unreachable on the real server too |
| **Live-mode-specific strings** | Live mode is modelled (§4.7) but only a sandbox was probed. The live-side `livemode` refusal, and any mode-dependent URL such as a receipt or hosted-invoice link, are sourced from documentation or inferred | This project — declared, and marked inferred rather than measured |
| **`llm_context` on search results** | Stripe-authored prose in no public artifact. Reproduced where the probe captured it, absent elsewhere | This project — declared |
| **`stripe_analytics` answers a permission refusal** | Sigma is not reproducible, and a key without the analytics permission is a realistic account state. Closed by the §5 refusal mechanism rather than left as a gap (§6). The cost: an agent that would have got analytics on a real permissioned account gets a refusal here | This project — declared |
| **`get_stripe_account_info` exists here and not on the live server** | Documented by Stripe and carries no preview label (§4.1.1). Kept deliberately; a harness that does not want it does not include it | This project — declared |
| **`SH206` lint violation** | Real tool descriptions name other tools. Fidelity wins; the world becomes unsafe under a prefixing host | This project — declared, and a `SEAHAVEN_FINDINGS.md` entry |
| **`product.attributes`, `product.type`, `product.tax_details`** | Returned by the live API, documented in neither the full nor the pinned spec. Ruled on in §11.2: the published schema wins and we expect the wire to catch up. Three probable-severity tells stay open on every product read | This project — declared, revisit if the wire has not caught up |
| **Search freshness** | Real Stripe search lags writes; this world is exact. Inherited from `stripe_world` | `stripe_world` |

## 14. Open questions

Each blocks something specific, and each has a defined way to close.

1. **The verbatim B2 permission message** (§5.1.1). Probed hard on 2026-09-22 and **not obtainable
   from this session**: the MCP authenticates by OAuth consent rather than by a restricted key, and
   this session's grant is broad. Reads across five product areas either succeeded or returned the
   B1 product-activation error; the writes that might have been denied are not catalogued; a
   deliberately revoked analytics grant never propagated across four calls; and Stripe's own
   documentation search confirms the status and wire type but not the wording.

   **Largely defused rather than open.** B1 is measured and covers every product-shaped case, and
   `stripe_analytics` can use B1's shape too. What still rests on documentation is B2 alone — 403,
   `invalid_request_error`, a message naming the missing permission — and §5.1.1 records that it may
   be unreachable on the real MCP as well, which would make it rare rather than wrong. Closes fully
   only against a real restricted key outside the MCP; not worth blocking on.
2. ~~`stripe_context` mismatch and `livemode: true`~~ — **closed** 2026-09-22, verbatim in §4.3.
   It also uncovered the third error channel of §4.5.1.
3. ~~`stripe_analytics` without Sigma~~ — **closed** 2026-09-22, and the assumption behind the
   question was wrong: the tool works on the sandbox. See §6 and the §13 residue entry.
4. **The real catalogue's true extent.** Blocks §5.3's artifact. Closes by the enumeration described
   there; the cost is the reason it is called out rather than assumed.
5. **`human_confirmation` / `approval_token`.** The schema is captured but the flow that triggers it
   is not. Blocks nothing yet; if it fires on operations this world routes, it becomes a surface of
   its own.
6. **Why the catalogue is curated the way it is.** Established (§9): membership is not predicted by
   permission, product or resource. *Why* Stripe drew the line where it did remains unknown, and it
   does not block anything — §5.3 measures membership rather than predicting it. It does mean the
   catalogue can move under us, which is the argument for re-running the enumeration as a drift job
   the way `stripe_world` re-records cassettes.
7. ~~Which fields are pruner gaps rather than spec gaps~~ — **closed** 2026-09-22. Measured: 20
   need no schema change at all, 6 are a pruner gap, 3 conflict and are ruled on in §11.2.

## 15. Testing

The project's method, and the user's instruction, is: **probe the real MCP, write tests aligned to
the probes, make the tests pass.** Three rules follow.

- **Tests are derived from recordings, not from this document.** Where a test and this spec
  disagree, the recording wins and the spec is corrected — the same rule `stripe_world`'s resource
  phases already use.
- **Every closed tell gets a test, named for its register id.** A reader can go from `TS-04` in the
  register to the test that holds it closed. A tell without a test is not closed.
- **Schema-level tells are tested at the schema level.** Tool names, descriptions, parameter names,
  types and requiredness are asserted against the captured real schemas as data, so a future edit to
  a docstring fails a test rather than silently reopening a blatant tell.

Two new test kinds this world does not have today:

1. **Surface conformance** — the registered tool list, each tool's JSON schema and each description
   compared against the captured real ones. This is the cheapest test in the project and covers the
   largest cluster of blatant tells.
2. **Refusal conformance** — a generated test over the catalogue artifact asserting that every
   operation lands in the right bucket: routed operations answer normally, catalogued-but-unrouted
   answer the permission refusal, absent operations answer `not available`. Generated from the
   artifact, so a routing change cannot silently move an operation between buckets.

## 16. Out of scope

- **Clock and time behavior.** By decision (§13). Seahaven may add it; it is not this project's
  work, and nothing here should be designed around the expectation that it will.
- **Any new billing capability.** This project adds no resource, no route and no state transition.
  If a tell can only be closed by implementing an unimplemented operation, the operation stays
  unimplemented and the tell is closed by the refusal of §5.
- **The raw `call_stripe` surface.** It is not the shape Stripe ships and is deliberately
  unregistered. It keeps its current signature, including `idempotency_key`.
- **HTTP-level fidelity.** Status codes, headers and the structured error envelope remain
  `stripe_world`'s concern, validated by its cassettes. This project only governs what MCP shows.
- **Gathering live-mode strings from a real live account.** Live mode is modelled (§4.7), but
  nothing in this project touches a live-mode key. Values that differ by mode come from
  documentation or are declared as inferred.
