# API Versions, `Stripe-Version` Keying, and What `spec3.sdk.json` Adds

> **2026-09-18 update:** `docs.stripe.com/api/versioning` is now directly fetched and confirms this
> file's version-model claims verbatim, including resolving the codename-to-major-release mapping. See
> [gap-closure-2026-09-18.md](./gap-closure-2026-09-18.md#8-docsstripecomapiversioning-cross-check)
> item 8.

## How many distinct API versions does the repo publish?

**One.** `spec3.json`'s `info.version` is a single string, `2026-08-26.dahlia`, and that is the only
API version described anywhere in the file — there is no per-version bundle, no array of versions, no
version-selector structure in the OpenAPI document. Verified directly:

```python
>>> spec['info']['version']
'2026-08-26.dahlia'
```

This matches `stripe/stripe-mock`'s own stated limitation (`research/repos/stripe-mock/README.md`,
quoted verbatim): *"It's locked to the latest version of Stripe's API and doesn't support old
versions."* — stripe-mock, built directly from this same spec, inherits the single-version limitation
because the spec itself only ever describes one version at a time.

**The `stripe/openapi` GitHub repository as a whole is also single-version at any given moment**, not
an archive of historical versions. Per the repo's own README (fetched 2026-09-18; `WebFetch` to
`docs.stripe.com` was blocked by this session's egress proxy, so this is sourced from the repo README
and its releases page, not Stripe's prose docs — see Gaps in `summary.md`):

- The repo publishes three concurrent spec *tracks*, not historical versions: `/latest/` (current GA,
  v1+v2 endpoints, recommended), `/preview/` (latest + public-preview endpoints), and `/openapi/`
  (legacy v1-only — this is the directory `spec3.json`/`spec3.sdk.json` were fetched from, per
  `MANIFEST.md`).
- The repo is released continuously via sequentially-numbered tags (`v2500`, `v2499`, `v2498`... —
  observed on the releases page at fetch time, several releases *per day*), each one re-publishing
  whatever the current GA API version is at that moment. **The tags are release-sequence numbers, not
  Stripe-Version identifiers** — the actual `Stripe-Version` string (`2026-08-26.dahlia`) only appears
  inside `info.version` of the spec file itself, changing whenever Stripe ships a new API version.
- **Consequence for this project:** there is no way to `git clone` or download this repo at a specific
  historical `Stripe-Version` and get that version's spec — the repo's history is a history of *when
  the current-version spec was regenerated*, not an archive indexed by API version. To pin a specific
  historical Stripe-Version's shape (relevant to project_overview.md §8's "pin the API version, record
  it in metadata and fixtures" requirement), the only path is: (a) capture `spec3.json` now and treat
  *that* file, permanently, as this project's pinned version — which is what `MANIFEST.md` already
  does (records the sha256 and fetch date) — or (b) reconstruct an older version from a `git log`
  walk of the repo if an old commit happens to have been made while that version was current
  (unverified whether that repo's history goes back far enough or is squashed/rewritten — not checked
  in this session, flagged as a gap).

## How the spec is keyed to `Stripe-Version` at request time (schema-level fact only — semantics belong to subtopic 2)

The OpenAPI document contains **no `Stripe-Version` header parameter** anywhere in `paths` — checked
directly against a sample endpoint (`GET /v1/customers`)'s full parameter list, which has only
`created`, `email`, `ending_before`, `expand`, `limit`, `starting_after`, `test_clock`; no version
header parameter is declared on any operation, and there is no `securitySchemes` entry or global
`parameters` array referencing one either (`components` has exactly two keys: `schemas` and
`securitySchemes`, and `securitySchemes` covers only the Basic-Auth-style secret-key scheme). **This
means `Stripe-Version` is entirely out-of-band from the agent's/generator's point of view** — real
Stripe reads it from a request header at the HTTP layer, outside anything this OpenAPI document
describes, and defaults to the version pinned on the API key's account when the header is absent. The
exact request/response header semantics (whether a mismatched version errors, what happens to old
integrations, what the version affects field-by-field) is prose-doc behavior, not schema — that's
subtopic 2's "Versioning" section to own, per the research plan's division of labor. This subtopic's
contribution is narrower and purely structural: **the spec you're building from describes exactly one
version's shape, with no version-conditional branching anywhere in the schema**, so a Seahaven world
built from it does not need (and the spec gives no mechanism for) per-version response shape switching
— pick the one pinned version and build to it.

## What `spec3.sdk.json` adds over `spec3.json`

Both files share `info.version` (`2026-08-26.dahlia`) and the same `openapi: 3.0.0` / `servers` /
`security` top matter; they diverge in `paths` and `components.schemas`:

| | `spec3.json` | `spec3.sdk.json` |
|---|---|---|
| `paths` | 419 | 386 |
| `components.schemas` | 1454 | 1735 |

**Paths: `spec3.sdk.json` has strictly fewer, and it is a strict subset of `spec3.json`'s path set** —
verified by set difference: every one of `spec3.sdk.json`'s 386 paths also exists in `spec3.json`
(`path_set(sdk) - path_set(public) == {}`), and 33 paths exist in the public spec but not the SDK spec.
All 33 are exactly the legacy/back-compat paths this subtopic's `scope-boundary-edges.md` independently
recommends cutting for the same reason: `/v1/customers/{customer}/bank_accounts*` (3 paths),
`/v1/customers/{customer}/cards*` (2 paths), `/v1/customers/{customer}/subscriptions*` (3 paths — the
legacy nested-subscription alias), `/v1/charges/{charge}/dispute*` (2 paths, superseded by top-level
`/v1/disputes/{dispute}`), `/v1/charges/{charge}/refund` (singular, superseded by
`/v1/charges/{charge}/refunds`), `/v1/balance/history*` (2 paths, superseded by
`/v1/balance_transactions`), plus several genuinely-out-of-scope Connect/Issuing paths
(`/v1/accounts/{account}/bank_accounts*`, `/v1/accounts/{account}/people*`,
`/v1/application_fees/{id}/refund`, `/v1/external_accounts/{id}`,
`/v1/issuing/settlements/{settlement}`). **Reading: `spec3.sdk.json` is what Stripe's own SDK
generator treats as "the real, current operation set" — official client libraries implement these
legacy paths (if at all) as hand-written custom methods layered outside the generic
operation-per-endpoint generation, not through the generic generator.** This is corroborating,
independent evidence (from Stripe's own tooling, not just this subtopic's judgment) for cutting the
same 33 paths recommended in `minimum-closed-set-and-tool-budget.md`.

**Schemas: `spec3.sdk.json` has 281 *more* schemas** despite having fewer paths, and per Stripe's own
README (fetched via `WebFetch`, 2026-09-18): *"SDK specs (`spec3.sdk.{json,yaml}`) contain special
annotations, deprecated endpoints, and pre-release features specifically intended to support generating
Stripe API libraries."* Verified structurally: every schema present in `spec3.json` is also present in
`spec3.sdk.json` with the same base shape (`type`, `properties`, `required`, `x-expandableFields`,
`x-resourceId`), plus, in the SDK version only, four additional annotation keys:

```
sdk-only keys on e.g. components.schemas.customer:
  x-stableId          -> "customer"   (a version-independent identifier for codegen)
  x-stripeMostCommon   -> ["address","customer_account","description","email","id","metadata","name","phone","shipping","tax"]
                          (which fields SDKs should surface prominently / default-populate in generated examples)
  x-stripeOperations   -> [{method_name, method_on, method_type, operation, path}, ...]
                          (the generated-client method table: e.g. {"method_name": "delete", "method_on": "service",
                          "method_type": "delete", "operation": "delete", "path": "/v1/customers/{customer}"})
  x-stripeResource     -> {"class_name": "Customer", "has_collection_class": true, "has_search_result_class": true, "in_package": ""}
                          (codegen metadata: generated class name, whether a paginated collection wrapper exists, etc.)
```

The 281 extra schemas are the request-body/parameter schemas Stripe's generator needs as distinct named
types for strongly-typed SDK method signatures (e.g. separate `*_create_params` / `*_update_params`
shaped schemas) that the public `spec3.json` inlines directly into each operation's `requestBody`
instead of naming as reusable components. **Practical takeaway for this project: `spec3.json` is the
right file to build the world's response/object shapes from (leaner, matches what a direct HTTP-API
agent actually receives); `spec3.sdk.json`'s `x-stripeOperations` tables are a genuinely useful
secondary source for "what does Stripe's own SDK call each operation" if the functional spec wants tool
names that mirror `stripe-python`'s method names — that naming-convention question is subtopic 5's to
answer, but `x-stripeOperations.method_name` is the concrete data to pull it from if needed.**

## Sources

- `research/stripe-openapi/spec3.json`, `spec3.sdk.json` — read directly, this session, via Python
  `json.load` + dict traversal (scripts not committed, ran from the scratchpad; reproducible in a few
  lines against `info`, `paths`, `components.schemas`).
- `research/repos/stripe-mock/README.md` — read directly, this session.
- `https://github.com/stripe/openapi` (root README) — fetched via `WebFetch`, 2026-09-18.
- `https://github.com/stripe/openapi/releases` — fetched via `WebFetch`, 2026-09-18, to observe the
  tag-numbering scheme and release cadence.
- `https://docs.stripe.com/api/versioning` — **fetch blocked** (`EGRESS_BLOCKED`, `docs.stripe.com` not
  on this session's allowed domain list). Not corroborated from Stripe's own versioning-policy prose;
  flagged in `summary.md` Gaps. Subtopic 2 (cross-cutting semantics) may have separate web access that
  succeeds where this session's did not — worth checking its output before treating this as a hard
  blocker for the functional spec.
