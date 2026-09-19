# Part 2 — Licensing and Naming

Scope: `project_overview.md` §5 Q6 and open question §12.2. This is research, not legal advice: below
are the license/policy texts quoted, and a plain statement of what they appear to permit on their
face. Anything flagged "needs a lawyer" is a genuine gray area, not me declining to look.

## 1. Exact licenses on the four sources

All four are **MIT**, all copyright Stripe, Inc. (or Stripe), confirmed by reading the `LICENSE`
files on disk plus (for `stripe/openapi`, which is not vendored on disk in full — only its
`spec3.json`/`spec3.sdk.json` payloads are — see `research/MANIFEST.md`) a direct fetch of the repo's
`LICENSE` file on GitHub.

| Repo | Local path | License | Copyright line (verbatim) |
|---|---|---|---|
| `stripe/openapi` | not vendored; fetched live via `github.com/stripe/openapi/blob/master/LICENSE` | MIT | `Copyright (c) 2011- Stripe, Inc. (https://stripe.com)` |
| `stripe/stripe-mock` | `research/repos/stripe-mock/LICENSE` | MIT | `Copyright (c) 2014- Stripe, Inc. (https://stripe.com)` |
| `stripe/stripe-python` | `research/repos/stripe-python/LICENSE` | MIT | `Copyright (c) 2010-2018 Stripe (http://stripe.com)` |
| `stripe/agent-toolkit` | `research/repos/agent-toolkit/LICENSE` | MIT | `Copyright (c) 2024–2025 Stripe` |

Full MIT text (identical boilerplate across all four, verbatim from
`research/repos/stripe-mock/LICENSE`):

> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

Note on scope, from directly reading the vendored copies: `stripe-mock`'s repo also vendors several
Go dependencies under `vendor/github.com/...`, each with its own separate MIT/BSD-style license file
(`davecgh/go-spew`, `pkg/errors`, and five `lestrrat-go/*` packages, all MIT except `pkg/errors`,
which is a 2-clause BSD-style "Redistributions..." license). None of those third-party dependency
licenses are relevant to this project since we are not vendoring `stripe-mock`'s Go source, only
reading it as prior art — flagged here only so nobody later assumes "stripe-mock is MIT" means "every
byte in that repo is MIT."

`agent-toolkit`'s repo also bundles two unrelated MIT-licensed benchmark fixtures under
`benchmarks/saas-starter-*/environment/LICENSE`, copyright **Vercel**, not Stripe — again, not
relevant unless this project ends up vendoring code from those specific benchmark directories.

## 2. What MIT permits this repository to redistribute

Reading the license text plainly (four clauses, all MIT-identical):

1. **Grant:** "use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies... and
   to permit persons to whom the Software is furnished to do so" — this is about as broad a grant as
   exists in OSS licensing. Committing `spec3.json` (or `stripe-mock`'s fixtures, or excerpts of the
   `agent-toolkit` source) into this project's own MIT-or-any-other-license repo is squarely permitted
   by this grant.
2. **The one condition:** "The above copyright notice and this permission notice shall be included in
   all copies or substantial portions of the Software." This is the operative constraint. Two
   practical readings for this project:
   - If `spec3.json` is committed **verbatim** (a full copy), it is unambiguously "a copy of the
     Software" and the copyright+permission notice must travel with it — in practice, adding a
     `THIRD_PARTY_LICENSES` (or similarly named) file that reproduces the MIT text and Stripe's
     copyright line, and referencing it from wherever `spec3.json` is committed, satisfies this. MIT
     does not require the notice be embedded inside the JSON file itself (impractical for a
     machine-generated data file); a co-located license file is the standard, widely-accepted
     practice for MIT-licensed data/asset files.
   - If a **derived subset** is committed instead — e.g., a script filters `spec3.json` down to only
     the ~15–20 in-scope object schemas this project needs, or re-serializes it into a different
     shape — MIT's "or substantial portions of" language most likely still applies (a filtered
     extract of Stripe's own schema is still substantially Stripe's expression, not an independent
     work), so the same attribution obligation almost certainly still applies. **This is the one
     genuinely gray corner worth a lawyer's five minutes**: MIT's case law/practice around exactly
     how much transformation turns "a substantial portion of the Software" into "not a copy of the
     Software at all" is not something I can resolve here, and it matters more for a schema (whose
     *content*, i.e. the field names/types/enums, is the valuable part, not any particular
     expression/wording of it) than for typical MIT-licensed source code. The conservative,
     zero-risk path — and the one I'd default to absent legal review — is to carry the attribution
     regardless of how much the spec is filtered/transformed, since doing so costs nothing and MIT
     explicitly permits redistribution either way.
3. **No copyleft, no source-disclosure, no "same license" requirement.** Unlike GPL/AGPL, MIT does
   not require this project's own code to be released under any particular license, does not require
   disclosing this project's own source, and does not restrict commercial use.
4. **Disclaimer of warranty** carries over too — the "AS IS" language should be preserved alongside
   the copyright notice, both because MIT says to include "this permission notice" (which is the
   whole block, warranty disclaimer included) and because it is exactly the disclaimer this project
   would want anyway for data ingested from a spec that can and does change over time.

## 3. Does it need a NOTICE?

**Not in the Apache-2.0 sense — MIT has no NOTICE-file mechanism at all.** Apache 2.0 §4(d) is what
creates the formal "NOTICE file" concept (a file of attribution notices that must be propagated).
MIT's text says nothing about a NOTICE file; its only textual requirement is that "the above copyright
notice and this permission notice" travel with the copy. So, strictly, no NOTICE file is *legally*
mandated by any of these four licenses.

That said, common practice for a project vendoring multiple MIT-licensed third-party sources (as this
project will, across `stripe/openapi`, possibly `stripe-mock` fixtures, and anything reused from
`agent-toolkit`) is to keep a single `THIRD_PARTY_LICENSES` or `NOTICE` file cataloguing each vendored
source, its license, and its copyright line — not because MIT requires the filename or format, but
because it is the practical way to "include" four different copyright notices without scattering
license blocks through data files, and it gives a reviewer (or a future contributor auditing the
repo) one place to check. **Recommendation: yes, keep such a file, as a matter of hygiene and
defensibility, not because MIT compels the specific mechanism.**

## 4. Stripe's trademark policy and API-terms clauses bearing on a compatible mock

**Access constraint:** this environment's `WebFetch` could not reach `stripe.com` or
`docs.stripe.com` at all — every attempt returned `EGRESS_BLOCKED` (confirmed via the proxy's status
endpoint, which logs explicit `403`/`connect_rejected` entries for `stripe.com:443`,
`docs.stripe.com:443`, and `api.stripe.com:443`, alongside almost every other non-GitHub domain
tried). This looks like a deliberate network policy for this session/environment (plausibly to
prevent this project from ever making live calls to Stripe's real API, intentionally or not), but it
also blocks reading Stripe's own legal pages directly. Everything in this section is therefore
**relayed through `WebSearch`'s result summaries, not independently fetched and read** — flagged
per-item below, and called out again in the summary's gaps section. A follow-up with working access
to `stripe.com` should re-verify the exact clause wording before this is relied on for anything
consequential.

**Stripe's Mark Usage Terms** (`stripe.com/legal/marks`, via WebSearch summary): governs use of
Stripe's name, logo, and other identifying "Marks." Reported provisions:
- Marks may not be used "to show Stripe or its goods or services in any disparaging or derogatory
  light, or in any way that may be damaging to the brand or Stripe's interests in the Marks."
- Users "may not use a ™ or ® symbol in conjunction with Stripe's Marks."
- Using the wordmark to imply endorsement or partnership without a formal agreement is called out as
  a violation.
- Any use beyond what the terms and Stripe's brand guidelines specifically permit requires written
  permission — contact given as `trademarks@stripe.com`.
- This governs the **Stripe name/logo/trade dress**, not the API's field names, object names, or
  error codes — those are not the kind of thing trademark law protects (trademark protects source
  identifiers like brand names and logos, not functional API vocabulary), which is directly relevant
  to §5 below.

**Stripe Services Agreement (SSA)** (`stripe.com/legal/ssa`, `/legal/ssa/gi`, via WebSearch summary):
reported to include a reverse-engineering prohibition — paraphrased by the search summary as
prohibiting a party from "decompil[ing], reverse engineer[ing], disassembl[ing], attempt[ing] to
derive the source code of, decrypt[ing], tamper[ing], translat[ing], modify[ing], or creat[ing]
derivative works of all or any part of the Stripe Technology or any services provided by Stripe," and
similarly barring deriving "source code, underlying ideas, algorithms, structure or organizational
form from the Marketplace or the Stripe API." **I could not fetch the SSA's primary text to confirm
this wording or its exact section number — treat the quoted phrases above as a secondary paraphrase,
not verified primary-source text.**

**The important nuance, stated plainly rather than resolved:** the SSA is a contract that binds
parties who use Stripe's live Services (i.e., have a Stripe account and have accepted the SSA) — its
reverse-engineering clause is naturally read as protecting Stripe's actual running systems/Technology
from being probed, decompiled, or copied by an account holder. `stripe/openapi`'s MIT license is a
**separate, affirmative act by Stripe**: Stripe itself chose to publish its API's shape under a
maximally permissive OSS license, explicitly (per its own README, read via `github.com`) so that
third parties can generate SDKs and tools from it. A project that builds a mock strictly from the
published, MIT-licensed `spec3.json` and from public documentation — without an account holder
reverse-engineering Stripe's live systems to extract undocumented behavior — has a reasonable-on-its-
face argument that it is operating entirely inside what Stripe's own MIT grant permits, separate from
whatever the SSA says about probing the live Services. **This is exactly the kind of interaction
between two different Stripe-authored documents (an OSS license vs. a click-through Services
contract) that deserves an actual lawyer's read, not my inference** — I'm flagging the tension and a
plausible resolution, not resolving it.

## 5. Two/three real examples of non-affiliation language, quoted verbatim

Task asked for real, comparable open-source API-compatible projects' README disclaimer wording. All
three below were fetched directly (via `raw.githubusercontent.com`, one of the few reachable domains
in this environment) and are genuine verbatim quotes, not paraphrases.

**1. `adrienverge/localstripe` (a "fake but stateful Stripe server... for testing purposes")** — the
closest direct comparable to this project (a third-party Stripe-shape mock server), notable mainly
for what it does **not** say: reading its README directly, there is **no explicit trademark or
non-affiliation disclaimer anywhere in it** — only a scope disclaimer ("no Stripe Connect support:
localstripe currently only supports Stripe Payments"). It is GPLv3-licensed (a different license
family from anything Stripe ships), which is itself worth noting as a contrast — this project should
not assume "third-party Stripe-compatible mock" implies any particular license or disclaimer
convention; `localstripe` shows a real project in this exact space that shipped without one.

**2. `dsasante1/paybox`** — "Local payment-infrastructure emulator for Paystack, Stripe, Flutterwave
and Kora," with partial implementations across seven provider adapters including "Stripe (92
endpoints)." This is the closest comparable in *shape* to what this project is building (a
stateful, multi-endpoint local emulator of real payment providers' APIs), and it carries an explicit,
strong disclaimer, quoted verbatim from its README:

> Not affiliated with, endorsed by, or connected to Paystack, Stripe, Flutterwave, Kora or Quid
> Payments. Provider names are used only to describe API compatibility.

and, separately, a safety guarantee worth modeling regardless of the trademark question:

> No real money can move through this process. It has no code path that reaches a payment network,
> and it refuses live API keys.

MIT-licensed.

**3. `ranaroussi/yfinance`** — an unofficial wrapper around Yahoo Finance's API (not a mock server,
but the same "comparable open-source project building against a real commercial API it doesn't own"
pattern, and one of the most widely used examples of this kind of disclaimer in the Python ecosystem).
Quoted verbatim, from an `[!IMPORTANT]` callout near the top of the README and repeated in a "Legal
Stuff" section at the bottom:

> yfinance is not affiliated, endorsed, or vetted by Yahoo, Inc. It's an open-source tool that uses
> Yahoo's publicly available APIs, and is intended for research and educational purposes. Remember -
> the Yahoo! finance API is intended for personal use only.

and a trademark-specific line in the same section:

> Yahoo!, Y!Finance, and Yahoo! finance are registered trademarks of Yahoo, Inc.

**Honorable mention / template pattern:** an auto-generated SDK (`voxgig-sdk/mixpanel-gdpr-sdk`, MIT)
uses boilerplate that reads like a reusable template rather than bespoke wording, also verbatim:

> This is an unofficial SDK for the GDPR public API, generated by Voxgig with `@voxgig/sdkgen`. It is
> not affiliated with, endorsed by, or sponsored by the upstream API provider.

**What I looked for and didn't find:** I checked several other well-known API-compatible/emulator
projects' top-level READMEs directly (`localstack/localstack` — an AWS cloud emulator,
`spulec/moto` — an AWS mocking library, `minio/minio` — S3-API-compatible object storage,
`mudler/LocalAI` — an OpenAI-API-compatible local inference server, `fsouza/fake-gcs-server` — a
Google Cloud Storage emulator) and found **no explicit non-affiliation disclaimer text** in any of
their top-level README content as fetched — several of these are widely known/large projects, so it's
plausible such language exists in a separate legal/docs page this environment couldn't reach, or
lower in a very long README than the fetch surfaced. Listed here so this isn't mistaken for "these
projects don't need one" — it's "I didn't find one in what I could read," a meaningfully different
claim.

## 6. Recommendation (§12.2): real name or neutral name?

**Recommend a neutral name for the shipped world, while still citing Stripe by name in research and
documentation where accurate (e.g. "modeled on the Stripe API, version 2026-08-26.dahlia") and
reusing real, MIT-licensed field names/error codes/object shapes from `spec3.json` with attribution.**

The reasoning, laid out honestly rather than as a foregone conclusion:

- **What's clearly fine:** using the actual field names, object types, enum values, and error codes
  from `spec3.json` is a *content/API-compatibility* question, governed by the MIT license on that
  spec (§2) — Stripe published that spec specifically so third parties could build against its shape.
  Field/parameter names are functional API vocabulary, not the kind of thing trademark law reaches
  (§4). So the object graph, the field names, the `cus_`/`in_`/`pi_`-style ID prefixes, and the error
  taxonomy can be reused faithfully with attribution and no real license or trademark barrier on their
  face.
- **What's the actual open question:** whether the *package/world is named and marketed* as "Stripe"
  — e.g. calling it `stripe-world`, using Stripe's wordmark/logo, or otherwise presenting it in a way
  a user could mistake for Stripe's own product. That is squarely what the Mark Usage Terms in §4
  govern, and "email trademarks@stripe.com" is the stated path for anything beyond the specifically
  enumerated permitted uses (e.g. "noting that you are a Stripe User," which this project is not).
  Given (a) this project cannot confirm the SSA/trademark text's exact current wording from here
  (§4's access constraint), (b) the comparable projects in §5 that *do* carry disclaimers use
  studiedly neutral or explicitly-hedged names (`localstripe`, `paybox` — note `paybox` doesn't even
  put "Stripe" in its own project name despite covering 92 Stripe endpoints), and (c) the downside of
  getting this wrong (a trademark complaint, a forced rename after ecosystem adoption) is asymmetric
  compared to the upside (a catchier name), the safer default is: **ship under a neutral world name,
  document loudly and accurately that it models the real Stripe API by version/spec, carry the MIT
  attribution for the spec content per §2–3, and add a `paybox`-style non-affiliation disclaimer
  regardless of the name chosen** — something in the shape of paybox's own wording, adapted:
  *"[World name] is not affiliated with, endorsed by, or connected to Stripe, Inc. 'Stripe' is used
  only to describe API compatibility."*
- This is a judgment call, not a legal conclusion — flagged explicitly as one for the project owner
  to make with the above evidence, and one where actually reading the SSA and Mark Usage Terms
  primary text (blocked in this environment, §4) before finalizing would be worth doing.
