# Research Source Manifest

Primary sources downloaded for the Stripe world research phase. The payloads themselves are
git-ignored (~108 MB); this file is the committed record of what was fetched, from where, and when,
so any result derived from them is reproducible.

All fetched **2026-09-18**.

## Stripe OpenAPI specification

Source: <https://github.com/stripe/openapi> (`master`)

| File | Local path | URL | Size | sha256 |
|---|---|---|---|---|
| `spec3.json` | `research/stripe-openapi/spec3.json` | https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.json | 8,028,700 B | `f0e0fc8fffbffda45bf5f3df59846443c1d47a3cfcbfae232eedf4743124ebee` |
| `spec3.sdk.json` | `research/stripe-openapi/spec3.sdk.json` | https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.sdk.json | 10,475,877 B | `610a902a16d767bef822a36ae357325ae723f8f4a4dc8f009e9b250409728465` |

- `info.version` (the Stripe API version this spec describes): **2026-08-26.dahlia**
- `paths`: 419 · `components.schemas`: 1454
- `master` blob etag at fetch time: `9e5daf02bb9412c2152c7888bb13b30c6154ece4dd6540d35efaca758d60d1ec`
- Note: the repo has no top-level `VERSION` file (a request for one returns 404). The API version is
  read from `info.version` inside the spec.

## Repositories (shallow clones, `--depth 1`)

| Repo | Local path | Commit | Commit date |
|---|---|---|---|
| `stripe/stripe-mock` | `research/repos/stripe-mock` | `3ad6722b7fe414f70356440adf0ab4846f0fe33f` | 2026-08-27 |
| `stripe/agent-toolkit` | `research/repos/agent-toolkit` | `da4991b0a0b9299d423ae2d5856e6d7e2b31b031` | 2026-09-17 |
| `stripe/stripe-python` | `research/repos/stripe-python` | `329c70233d07db9176d36b2792cb7c432a7ffb7b` | 2026-09-17 |

## Re-fetch

```bash
mkdir -p research/stripe-openapi research/repos
curl -sSL -o research/stripe-openapi/spec3.json \
  https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.json
curl -sSL -o research/stripe-openapi/spec3.sdk.json \
  https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.sdk.json
for r in stripe-mock agent-toolkit stripe-python; do
  git clone --depth 1 "https://github.com/stripe/$r.git" "research/repos/$r"
done
```

Prose documentation (docs.stripe.com) is not mirrored here — it is fetched per-page during research
and cited by URL with the access date, since those pages are not versioned artifacts.

## Licensing

Licence terms for each of these sources, and what this repository may redistribute, are a research
question in their own right — see the `agent-surfaces-and-licensing` subtopic under
`specs/projects/stripe_world/research/stripe-billing-and-payments/`. Nothing from these sources is
vendored into the shipped package until that question is answered.
