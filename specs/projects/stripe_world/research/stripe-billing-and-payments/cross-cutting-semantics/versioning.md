# API Versioning (`Stripe-Version`)

## What the spec itself says about its own version
`spec3.json`'s `info` block, read directly:
```json
{
  "info": {
    "title": "Stripe API",
    "version": "2026-08-26.dahlia",
    "description": "The Stripe REST API. Please see https://stripe.com/docs/api for more details.",
    "x-stripeSpecFilename": "spec3"
  }
}
```
So the pre-fetched spec is a **single, pinned snapshot** at version `2026-08-26.dahlia` — it does not
itself enumerate other versions or a version history; that's expected, since OpenAPI specs are
generated per-version. `stripe-python`'s own `_api_version.py` (generated file, not hand-written)
pins the *client's* default request version identically:
```python
class _ApiVersion:
    CURRENT = "2026-08-26.dahlia"
    CURRENT_MAJOR = "dahlia"
```
Two independent artifacts in this research bundle (the OpenAPI spec and the Python SDK) agree on the
exact same current version string, which is good corroboration that `2026-08-26.dahlia` really is
"current" as of when these were fetched. Versions are **date-stamped with a trailing human-readable
codename** (`dahlia`) — the `CURRENT_MAJOR` field suggests Stripe treats the codename as the
"major" identifier for a family of dated versions (i.e. multiple dated snapshots can share a
codename before the next major naming bump), though I did not find independent confirmation of
exactly how codenames map to date ranges this session.

## How versioning works, mechanically (from WebSearch synthesis of docs.stripe.com/api/versioning and
stripe.com/blog/api-versioning — direct WebFetch to both blocked this session, see Gaps)

- Every Stripe **account** has a pinned **default API version**, settable/upgradable from the
  Dashboard (Workbench). Absent any override, requests are served at that account default.
- A single request can **override the account default** by sending a `Stripe-Version` request
  header with a specific date(+codename) value.
- The documented internal model (per the synthesized description of docs.stripe.com/api/versioning):
  the API always **computes/formats the response at the current (latest) version internally, then
  transforms it down** to whatever target version was determined (header override, else account
  default) before sending it back. This "compute at head, transform down" design is the same pattern
  described in Stripe engineering's public writeups on API versioning (`stripe.com/blog/api-
  versioning` — not independently re-fetched this session, but consistent with what the search
  synthesis reported).
- **Webhook Events** also default to the account's pinned API version for the shape of the objects
  embedded in them, **unless** a specific API version was set on the webhook **endpoint itself** at
  creation time — i.e. version pinning for webhooks is a property of the *endpoint*, not just the
  account or the triggering request.
- **Automated/Stripe-initiated operations** (the synthesis specifically calls out subscription-cycle
  invoice generation as an example) run at the **account's default API version**, not at whatever
  version happened to be current when the subscription was originally created — this matters for a
  faithful Seahaven emulation of recurring billing, since object shapes produced by "Stripe itself"
  (as opposed to a direct API call) should track the account default version, not a per-object frozen
  version.

## Unknown/invalid `Stripe-Version` values
**Not confirmed from a source I could fetch or find a confident secondary description of this
session.** I could not determine whether the real API 400s on an unrecognized/malformed
`Stripe-Version` string, silently falls back to the account default, or something else. This is a
genuine, explicitly-flagged gap (see summary.md).

What **is** confirmed, from `stripe-mock`'s source (`research/repos/stripe-mock/server/server.go`,
read directly) is the *reference-mock's* behavior, which is deliberately much simpler than whatever
the real API does:
```go
if s.strictVersionCheck {
    stripeVersion := r.Header.Get("Stripe-Version")
    if stripeVersion != "" && stripeVersion != s.spec.Info.Version {
        message := fmt.Sprintf(invalidStripeVersion, stripeVersion, s.spec.Info.Version)
        stripeError := createStripeError(typeInvalidRequestError, message)
        writeResponse(w, r, start, http.StatusBadRequest, stripeError)
        return
    }
}
```
i.e. **by default stripe-mock does not check `Stripe-Version` at all** — every request gets served
at the single embedded spec version regardless of what header was sent. Only with the opt-in
`-strict-version-check` CLI flag does it reject (400, `invalid_request_error`) a request whose
`Stripe-Version` doesn't exactly string-match the spec's pinned version — and even then, it's an
exact-match check, not real multi-version support: there is no version-*transformation* engine in
stripe-mock at all, so "supporting" a different version by claiming to isn't actually possible in
this tool. `invalidStripeVersion`'s message template (`server.go` line 590) reads:
```go
invalidStripeVersion = "Version sent in `Stripe-Version` header '%s' " + ...
```
(message continues past what I captured in this read — the constant is defined starting line 590;
worth a follow-up read of the full string if a Seahaven world wants to reproduce it verbatim, since
I only captured the prefix in this pass).

## Response headers carrying version info
**Not confirmed this session.** I could not verify from a source I could fetch whether the live API
echoes back a `Stripe-Version` response header reflecting the version the response was actually
rendered at. `stripe-mock` does **not** set any such header — the only headers it sets are
`Idempotency-Key` (echo), `Request-Id` (hardcoded stub `req_123` in the mock), `Content-Type`, and
its own `Stripe-Mock-Version` (the mock tool's *own* release version, not an API version — do not
confuse the two). `stripe-python`'s response-header handling (`_api_requestor.py`) reads
`Stripe-Notice` (a separate, unrelated header used for deprecation/notice messages — see
`_maybe_emit_stripe_notice`) and `Stripe-Should-Retry` (see `idempotency.md`), and passes through
`request-id` for `StripeError.request_id`, but I did not find it reading back a version header
anywhere in the requestor/http-client code I inspected. Flagged as an open question — recommend
checking a real API response's headers directly (e.g. via a test-mode `curl`) before assuming either
way.

## What actually changes between versions
Not deeply researched this session (out of budget for this subtopic, and largely subtopic-1's
territory re: "how many distinct API versions the repo publishes" and schema-level version deltas).
What I can say from the artifacts on disk: the pre-fetched `spec3.json`/`spec3.sdk.json` are single-
version snapshots (`2026-08-26.dahlia` only) — there is no version-history or multi-version diff data
in the pre-fetched bundle for this subtopic to mine. Subtopic 1 owns "how many distinct API versions
the repo publishes and how the spec is keyed to `Stripe-Version`" per the research plan; I'm not
duplicating that here.

## Sources
- `research/stripe-openapi/spec3.json` (`info.version`) and `research/repos/stripe-python/stripe/_api_version.py`
  — both read directly, agree on `2026-08-26.dahlia`.
- `research/repos/stripe-mock/server/server.go` — read directly for exact strict-version-check logic
  and header-setting behavior.
- WebSearch synthesis of docs.stripe.com/api/versioning and stripe.com/blog/api-versioning — direct
  WebFetch to both was blocked this session (`EGRESS_BLOCKED`); treat the "compute at head, transform
  down" mechanism description and the webhook-endpoint-version / automated-operations-use-account-
  default claims as corroborated-by-synthesis rather than independently re-verified against Stripe's
  own current page text.
