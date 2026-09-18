# Idempotency Keys

## What the header is and which methods honor it

- Header name: `Idempotency-Key`. It is **not** part of the OpenAPI request schema at all — `grep`
  across `spec3.json` finds no `Idempotency-Key` parameter definition on any operation. This is a
  pure cross-cutting/header-level concern layered outside the generated schema, confirming the
  premise of this subtopic. (Verified directly: `grep -n "Idempotency-Key" spec3.json` → no hits;
  `grep -n idempotency spec3.json` only turns up the *error* `type` enum value `idempotency_error`
  and the unrelated `Event.request.idempotency_key` field — see below.)
- **v1 API**: only **POST** requests accept/honor an idempotency key. GET and (v1) DELETE requests
  are idempotent by definition already, so a key sent on them has no effect.
- **v2 API**: both **POST and DELETE** accept and honor idempotency keys.
  [Source: WebSearch synthesis of docs.stripe.com/api/idempotent_requests and
  docs.stripe.com/api-v2-overview, cross-checked against client behavior below.]
- **Confirmed directly from the `stripe-python` client source**
  (`research/repos/stripe-python/stripe/_api_requestor.py`, lines ~587-593):

  ```python
  idempotency_key = options.get("idempotency_key")
  if idempotency_key:
      headers["Idempotency-Key"] = idempotency_key

  # IKs should be set for all POST requests and v2 delete requests
  if method == "post" or (api_mode == "V2" and method == "delete"):
      headers.setdefault("Idempotency-Key", _generate_idempotency_key())
  ```

  This is a significant, non-obvious, client-observed behavior: **the official Python client
  auto-generates a random idempotency key for every POST request (and every v2 DELETE) if the
  caller didn't supply one.** The generator (line 106-108):

  ```python
  def _generate_idempotency_key() -> str:
      b = os.urandom(16)
      return f"{b[0:4].hex()}-{b[4:6].hex()}-{b[6:8].hex()}-{b[8:10].hex()}-{b[10:].hex()}"
  ```

  i.e. a UUID-v4-shaped (but not RFC-4122-compliant — no version/variant bits set) random string
  from `os.urandom(16)`. This means **every mutating request made through stripe-python already
  carries a key by default**, which is what makes the client's automatic network-level retries (see
  below) safe without the caller doing anything.

## Retention period

- Community/engineering consensus (via WebSearch of `brandur.org/idempotency-keys`, a Stripe
  engineer's writeup, and secondary summaries): keys are retained roughly **24 hours**, after which
  they are pruned; reusing a key after it has aged out starts a completely new request with no
  replay protection. I could not fetch `docs.stripe.com/api/idempotent_requests` directly this
  session (see Gaps in summary.md) — this number should be treated as **corroborated but not
  independently re-verified against Stripe's own current doc page** in this run.

## Replay behavior

### Identical key + identical params
Stripe's idempotency layer saves the **full result (status code + body) of the first request that
began execution**, and replays that saved result verbatim for the lifetime of the key — including
**5xx errors**. This is the core idempotency guarantee. [WebSearch synthesis of
docs.stripe.com/api/idempotent_requests.]

### Identical key + different params
Returns a **400** `invalid_request_error` (surfaces as `IdempotencyError` in stripe-python only when
`type == "idempotency_error"`, which parameter-mismatch case appears to use rather than a distinct
class — see the exact HTTP-status→exception mapping in `errors.md`) with message text reported as:

> "Keys for idempotent requests can only be used with the same parameters they were first used
> with."

This exact string appears verbatim in numerous client-library GitHub issues (e.g.
`tipsi/tipsi-stripe#799`, `jonasbark/flutter_stripe_payment#311`), which is strong corroboration even
without a direct docs.stripe.com fetch.

### Concurrent in-flight replay (the same key used twice before the first call finishes)
Returns **HTTP 409 Conflict** with error code `idempotency_key_in_use` and message text reported
(via GitHub issue `code-corps/stripity_stripe#564` and `stripe-ruby#503`) as:

> "There is currently another in-progress request using this Idempotent Key (that probably means you
> submitted twice, and the other request is still going through)"

This is corroborated end-to-end by the **stripe-python client's own retry policy**
(`research/repos/stripe-python/stripe/_http_client.py`, `_should_retry`, lines 117-162):

```python
# Retry on conflict errors.
if status_code == 409:
    return True
```

i.e. the official client treats a 409 as automatically retryable (with exponential backoff — see
below) up to `max_network_retries`. Default `max_network_retries` is **2**
(`stripe/__init__.py`: `max_network_retries: int = 2`).

### Whether errors are cached
Yes, with one carve-out: **results are only cached once the endpoint has begun execution.** If
incoming parameters fail validation before the endpoint starts, or the request conflicts with
another in-flight request on the same key (the 409 case above), Stripe does **not** save an
idempotent result for that attempt — because "no API endpoint initiated the execution." Practically:
a 400 (validation failure) or 409 (concurrent) response is *not* itself cached/replayed under the
key; only a response from a request that actually reached endpoint execution (success or otherwise,
including 5xx) is cached and replayed on subsequent uses of that same key+params.
[WebSearch synthesis of docs.stripe.com/api/idempotent_requests.]

### Retry semantics observed in the client, precisely

From `_http_client.py` `_should_retry`:
```python
if rheaders is not None and "stripe-should-retry" in rheaders:
    if rheaders["stripe-should-retry"] == "false":
        return False
    if rheaders["stripe-should-retry"] == "true":
        return True

# Retry on conflict errors.
if status_code == 409:
    return True

# Retry on 500, 503, and other internal errors.
if status_code >= 500:
    return True

return False
```
So the server can explicitly steer client retry behavior via a `Stripe-Should-Retry` response
header (overriding the client's own default heuristic in either direction), and absent that header
the client retries automatically on 409 and any 5xx, with exponential backoff + jitter
(`_sleep_time_seconds`/`_add_jitter_time`, same file, lines 164-190: `INITIAL_DELAY * 2^(n-1)`
capped at `MAX_DELAY`, randomized to `[delay/2, delay]`, floored at `INITIAL_DELAY`).

## `request.idempotency_key` echo — where it actually lives

This is **not** a field echoed on the synchronous API response body/headers of the original request.
It is a field on the **Event** object (i.e. on webhooks), confirmed directly from `spec3.json`'s
`notification_event_request` schema:

```json
{
  "id": {
    "description": "ID of the API request that caused the event. If null, the event was automatic (e.g., Stripe's automatic subscription handling). ...",
    "nullable": true, "type": "string"
  },
  "idempotency_key": {
    "description": "The idempotency key transmitted during the request, if any. *Note: This property is populated only for events on or after May 23, 2017*.",
    "nullable": true, "type": "string"
  }
}
```

So: `event.request.idempotency_key` tells you which idempotency key (if any) caused a given webhook
Event to fire, and `event.request.id` is null for events Stripe generated automatically (e.g.
automatic subscription billing) rather than in direct response to an API call. The **May 23, 2017**
cutoff for population is a precise, citable detail straight from the schema description — a strong
signal that a faithful emulation should treat this as always-populated for any event created "now."

*Source: `research/stripe-openapi/spec3.json` → `components.schemas.notification_event_request`
(exact JSON above); `components.schemas.event.properties.request` references it, described as
"Information on the API request that triggers the event."*

## HTTP status → client exception mapping (ties idempotency errors into the general error model)

From `stripe-python`'s `specific_v1_api_error` (`_api_requestor.py` lines 430-482) — this is the
authoritative, client-observed mapping and is covered fully in `errors.md`, but the idempotency-
specific branch is:

```python
elif rcode in [400, 404]:
    if error_data.get("type") == "idempotency_error":
        return error.IdempotencyError(...)
    else:
        return error.InvalidRequestError(...)
```

Note the **404** branch is grouped with 400 here — i.e. the client treats a same-key/different-params
idempotency conflict and a generic invalid-request the same way structurally, distinguished only by
the wire `type` field being `"idempotency_error"` vs `"invalid_request_error"`. The 409 concurrent
case does not appear in this status-code switch at all — it falls through to the generic
`error.APIError(...)` branch (`else:` at the end), which is consistent with 409 being treated as a
*retryable network-layer* condition rather than a semantically distinct exception class.

## Open items for this file specifically
- Exact 24-hour retention figure not re-confirmed against Stripe's own current doc page this
  session (egress to docs.stripe.com was blocked — see summary.md Gaps).
- Whether the *original* successful/failed response itself carries the idempotency key back as a
  response header (as opposed to appearing in webhook `event.request.idempotency_key`) was not
  confirmed either way from a source I could fetch this session.
