# Metadata

## Limits
Per WebSearch synthesis of docs.stripe.com/api/metadata / docs.stripe.com/metadata (direct WebFetch
blocked this session — see summary.md Gaps), corroborated by a long-standing `stripe-php` GitHub
issue (`stripe/stripe-php#246`, titled "Better handling of metadata 500 character value (and 40
character key) limits") which independently states the same numbers:

- **Up to 50 key/value pairs** per object.
- **Keys**: up to **40 characters**.
- **Values**: up to **500 characters**.
- Keys and values are stored as plain strings; any characters are allowed **except square brackets
  (`[` and `]`) in keys** (these would collide with Stripe's form-encoding array/object syntax).

## Deletion semantics — confirmed directly and precisely from `spec3.json`
This is schema-level, not just prose, and I pulled it directly rather than trusting a paraphrase.
From `paths./v1/customers.post.requestBody...schema.properties.metadata` (identical pattern reused
across every writable metadata-bearing resource):

```json
{
  "anyOf": [
    { "type": "object", "additionalProperties": { "type": "string" } },
    { "type": "string", "enum": [""] }
  ],
  "description": "Set of key-value pairs that you can attach to an object. This can be useful for storing additional information about the object in a structured format. Individual keys can be unset by posting an empty value to them. All keys can be unset by posting an empty value to `metadata`."
}
```

So, precisely:
- **Unset one key**: include that key in the `metadata` object with an **empty string** as its
  value, e.g. `metadata[foo]=`. (There is no separate "delete" verb/endpoint for a single metadata
  key — it's expressed as a value update.)
- **Unset all keys**: post `metadata=""` (the literal empty string) as the *entire* parameter,
  rather than an object — this is why the schema is an `anyOf` between "an object of string values"
  and "the literal empty string," not just `additionalProperties: string`. A mock/emulator that only
  modeled `metadata` as `Dict[str,str]` would need a special case to accept the bare `""` sentinel
  for full-clear.
- Metadata updates are otherwise a **merge**, not a replace: keys omitted from the `metadata` object
  in a request are left untouched (implied by "individual keys can be unset by posting an empty
  value to them" — i.e. omission ≠ deletion, only explicit empty-string does that).

## Which objects carry metadata
Counted directly against `spec3.json`: **83 schemas** have a top-level `metadata` property (Python:
`[k for k,v in schemas.items() if 'metadata' in v.get('properties',{})]` → length 83). Includes the
expected core resources — `account`, `charge`, `customer`, `invoice`, `invoiceitem`, `payment_intent`,
`credit_note`, `coupon`, `dispute`, `checkout.session`, `payment_link`, `issuing.*` resources, several
`payment_links_resource_*` sub-objects, `customer_balance_transaction`, `fee_refund`, `file_link`,
`identity.verification_session`, `entitlements.feature`, `billing.credit_grant`, and more.

**Notable objects that do *not* carry metadata**, confirmed by the same query returning `False`:
- **`balance_transaction`** — no metadata field. (If a Seahaven world needs to correlate a balance
  transaction back to application data, it must do so via the id of the object that generated it,
  not via metadata on the transaction itself.)
- **`event`** — webhook Events have no metadata field of their own (though, per `idempotency.md`,
  they do carry `request.idempotency_key`/`request.id`).
- **`file`** — no metadata (contrast with `file_link`, which *does* have metadata).

This 83-schema list and the three negative examples are worth carrying into the API-surface-and-
object-graph subtopic's per-resource tables (subtopic 1) if it hasn't already captured
metadata-presence per resource — flagging rather than duplicating that table here, per scope.

## Sources
- `research/stripe-openapi/spec3.json` — authoritative for the exact `anyOf` deletion-semantics
  schema and the 83-schema/3-negative-example metadata-presence count, both queried directly.
- WebSearch synthesis of docs.stripe.com/api/metadata for the 50-key/40-char/500-char numeric limits
  (direct WebFetch to docs.stripe.com blocked this session).
- `stripe/stripe-php#246` (GitHub issue) — independent corroboration of the 40/500 character limits.
