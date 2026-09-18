# The `expand[]` Parameter

## What's expandable, and how the schema encodes it
`spec3.json` marks expandability at the **schema level**, not per-parameter: every object schema
that has expandable fields carries an `x-stripeSpecFilename`-generator-produced
**`x-expandableFields`** array listing which of its own properties can be expanded. Two concrete
examples pulled directly from the spec:

- `api_errors` (the error envelope itself — see `errors.md`):
  ```json
  "x-expandableFields": ["payment_intent", "payment_method", "setup_intent", "source"]
  ```
  Note `charge` is a plain string id on the error object and is **not** in this list — i.e. the
  failed charge id on an error is not expandable, only `payment_intent`/`payment_method`/
  `setup_intent`/`source` are.
- List envelopes: `CustomerResourceCustomerList`'s `x-expandableFields` is just `["data"]` — meaning
  on a list response, expansion paths must be prefixed with `data.` to reach into the individual
  items, exactly as the parameter description says (see below).

This is the mechanism a faithful mock would need to replicate generically: walk `x-expandableFields`
recursively to validate/serve any `expand[]` path, rather than hand-listing paths per endpoint.

## The parameter itself (verbatim, appears identically on every GET/list/create/update op)
```json
{
  "name": "expand",
  "description": "Specifies which fields in the response should be expanded.",
  "in": "query",
  "explode": true,
  "style": "deepObject",
  "schema": { "type": "array", "items": { "type": "string", "maxLength": 5000 } }
}
```
Confirmed present on:
- **List/read (GET) endpoints** — e.g. `/v1/customers` GET.
- **Create/update (POST) endpoints** — e.g. `/v1/customers` POST request body schema includes
  `expand` as a form-encoded array field alongside the resource's own writable fields (verified
  directly against `paths./v1/customers.post.requestBody...schema.properties.expand` in
  `spec3.json` — identical `{"type":"array","items":{"type":"string","maxLength":5000}}` shape).
  So expand-on-create/update is real and schema-level, not just a GET-only convenience.

## Depth limit
**Four levels.** Per WebSearch synthesis of docs.stripe.com/api/expanding_objects (direct WebFetch to
docs.stripe.com was blocked this session — see summary.md Gaps), the documented example of the
*deepest allowed* expansion is:

> "the deepest expansion allowed when listing charges is `data.payment_intent.customer.default_source`"

That's `data.` (list-item prefix) + 3 more hops (`payment_intent` → `customer` → `default_source`) =
4 levels of traversal total counting the list-data hop, or 3 levels of "real" object-to-object
expansion beyond the initial list unwrap, depending how you count. Take the literal example as the
citable ground truth rather than my own arithmetic on it.

## The `data.` prefix for list/search requests
Confirmed by both the doc-page synthesis and the schema (`x-expandableFields: ["data"]` on every
list envelope, as above): on a list or search response, any expansion into the *items themselves*
must be written as `expand[]=data.<field>...`, e.g. `data.customer` to expand the customer on each
charge in a list-of-charges response. A bare `expand[]=customer` on a list endpoint would not
resolve — there is no top-level `customer` field on the list envelope itself, only on `data[i]`.

## Recursive / nested expansion syntax
Dot-separated paths recurse through nested expandable fields: `payment_intent.customer` expands
`payment_intent` to a full PaymentIntent object, then expands *that* object's `customer` field to a
full Customer. Multiple independent expansions are passed as multiple array entries in the same
`expand[]` parameter (this matches the OpenAPI `style: deepObject, explode: true` array encoding).

## Expansion inside subscriptions/invoices
Not independently re-verified via a live example this session (see Gaps), but the general mechanism
is uniform: any field flagged in a schema's `x-expandableFields` (e.g. `subscription.default_payment_method`,
`subscription.latest_invoice`, `invoice.customer`, `invoice.subscription`, `invoice.payment_intent`,
`invoice.charge`) is reachable by the same dot-path syntax, subject to the 4-level cap. I did not
enumerate every `x-expandableFields` list for `subscription`/`invoice` in this pass — that level of
per-object detail belongs with subtopic 1 (API surface and object graph), which owns the full field
inventory; I'm flagging the *mechanism* here, not re-deriving the full per-object expandable-field
tables (that would duplicate subtopic 1's job).

## Bad/invalid expand path — what error it returns
**Not confirmed this session.** I ran multiple targeted searches (for messages like "Cannot expand",
"unsupported expansion," "invalid expandable field") and none surfaced a citable exact error string
or even a confident secondary-source description of whether Stripe hard-errors on an unrecognized
expand path vs. silently ignoring it. This is a genuine gap — flagged explicitly rather than guessed.
Recommend resolving empirically against the live test-mode API (e.g. `expand[]=nonexistent_field` on
a `GET /v1/customers/:id`) before committing to mock behavior here, since the shape of the error (or
absence of one) materially affects whether a Seahaven Stripe world needs to validate expand paths at
all.

## Sources
- `research/stripe-openapi/spec3.json` — all verbatim schema/parameter excerpts above, queried
  directly.
- WebSearch synthesis of docs.stripe.com/api/expanding_objects (depth-limit example, `data.` prefix,
  recursive-expansion description) — direct WebFetch blocked this session.
