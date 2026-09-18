# Gap Closure Pass — 2026-09-18

Closes the three items assigned to this lane, now that a Tavily MCP server (`mcp__Tavily__tavily_extract`)
can reach `docs.stripe.com` directly (confirmed working; `WebFetch` still cannot reach it in this
environment).

---

## 8. `docs.stripe.com/api/versioning` cross-check

**Question:** Cross-check this lane's versioning claims (sourced from `stripe/openapi` READMEs and
release tags, not Stripe's own prose) against Stripe's own versioning-policy page.

**Answer: Confirms** the lane's claims, and **resolves** the one explicit uncertainty
`versioning.md`/`api-versions-and-spec-diff.md` flagged (the sibling cross-cutting-semantics lane's
`versioning.md` noted: *"I did not find independent confirmation of exactly how codenames map to date
ranges"*).

**Verbatim quote** (`docs.stripe.com/api/versioning`, fetched directly in full):

> "Each major release, such as [Acacia](/changelog/acacia), includes changes that aren't
> [backward-compatible](/upgrades#what-changes-does-stripe-consider-to-be-backward-compatible) with
> previous releases. Upgrading to a new major release can require updates to existing code. Each monthly
> release includes only backward-compatible changes, and uses the same name as the last major release.
> You can safely upgrade to a new monthly release without breaking any existing code. The current version
> is 2026-08-26.dahlia. For information on all API versions, view our [API changelog](/changelog)."

This directly confirms the structural model this subtopic already inferred from `spec3.json`'s
`info.version` being a single pinned string and from `stripe-python`'s `_api_version.py` having both a
`CURRENT` (full dated string) and `CURRENT_MAJOR` (`"dahlia"`) constant: **the codename (`dahlia`) names
a family of monthly, backward-compatible releases that share one "major" identity; a new codename
(`Acacia` → `Basil` → `Clover` → `Dahlia`, per the changelog nav observed on the fetched page) marks a
breaking major release.** This is no longer an inference from field-naming — it's Stripe's own stated
policy.

The page also confirms, independently, this subtopic's core structural finding (no `Stripe-Version`
header parameter in the OpenAPI document — versioning is out-of-band): nothing on the versioning page
contradicts this, and the page explicitly routes header-based version control through Workbench/SDK
config rather than any OpenAPI-described mechanism, consistent with `api-versions-and-spec-diff.md`.

**Bonus, not one of the three assigned items but directly relevant to this lane's territory:** the same
fetch surfaced `docs.stripe.com/api/enums`, which states a fact this subtopic's resource-inventory work
depends on but hadn't sourced from Stripe's own prose — the **open vs. closed enum** distinction:

> "Some properties on API resources use a fixed set of possible string values, which are known as enums.
> Enums can be *closed* or *open*: **Closed**: The set of values for the enum is fixed and won't grow or
> change over time. **Open**: The set of values for the enum can grow or change over time. Stripe can add
> new values as a backward-compatible change without requiring an API version upgrade. **Each enum's
> reference entry indicates whether it is open.**"

This **confirms and formalizes** `summary.md`'s Key Findings note about `dispute.reason`,
`payout.status`, `refund.status`, etc. being bare `string` in the schema rather than `enum` — Stripe's own
docs call this pattern "open enums" by design, and (new, actionable detail) say each field's own
reference page states explicitly whether it's open or closed — i.e. the closed value sets
`resource-inventory.md` extracted from field `description` text are the right approach, but a fuller pass
could also check each field's reference page for an explicit open/closed marker rather than relying on
description prose alone. Not chased further (out of this pass's scope), but worth flagging for
`resource-inventory.md`'s future maintenance.

**URLs:** https://docs.stripe.com/api/versioning; https://docs.stripe.com/api/enums

**Disposition:** Versioning model **confirmed** (was inferred-from-schema, now stated-in-prose).
Codename-family mapping **resolved** (was an explicit unconfirmed hypothesis). No corrections — this
lane's prior claims all hold up. Update `api-versions-and-spec-diff.md` and `summary.md` to mark the
versioning cross-check as done and cite the direct prose source.

---

## 9. The closed `event.type` list

**Question:** `event.type` is a bare string in `spec3.json` by design (confirmed, not re-litigated here).
Get the closed list from `docs.stripe.com/api/events/types` and cross-check against `stripe-python`'s
webhook-type constants; write the full list to a file for downstream use.

**Answer: Done — with a real discrepancy between the two sources that's worth flagging.**

- `docs.stripe.com/api/events/types` (fetched directly, full page): **236 distinct event-type strings**,
  covering all `/v1` resource events. The page states its own scope explicitly:

  > "This is a list of all public snapshot events we currently send for /v1 resources, which is
  > continually evolving and expanding. Stripe events use the `resource.event` naming convention. Events
  > that occur on subresources like `customer.subscription.updated` don't trigger a corresponding event
  > for the parent resource (`customer.updated`)."

- `stripe-python`'s generated `enabled_events` parameter type (`stripe/params/_webhook_endpoint_create_params.py`,
  a `Literal[...]` union — this is a genuine SDK-enforced closed enum, generated from Stripe's own
  internal spec, unlike `event.type` on the wire which stays a bare string): **265 distinct event-type
  strings**.

**Diff:** 30 event types appear in `stripe-python`'s list but **not** on the fetched
`docs.stripe.com/api/events/types` page — all 30 are `treasury.*` events (`treasury.credit_reversal.*`,
`treasury.debit_reversal.*`, `treasury.financial_account.*`, `treasury.inbound_transfer.*`,
`treasury.outbound_payment.*`, `treasury.outbound_transfer.*`, `treasury.received_credit.*`,
`treasury.received_debit.*`). The docs page's own left-nav lists a "Treasury for Platforms" section, but
the fetched page content itself contains **zero** `treasury.*` entries and ends alphabetically right at
`transfer.updated` — i.e. exactly where `treasury.*` would sort next. This is most consistent with the
Treasury section being gated/collapsed content the extraction didn't render (Treasury is a
restricted-access Stripe product), not a real absence — since `stripe-python`'s independently-generated
enum has them and Treasury is itself a real, documented Stripe product family. One event
(`quote.will_expire`) appears on the docs page but not yet in this snapshot of `stripe-python`'s
generated Literal, plausibly just SDK-generation lag behind the API.

**Resolution:** took the **union** of both sources as the closed set — **266 distinct event-type
strings** — on the grounds that (a) the docs page's own scope statement plus the exact alphabetical
cutoff point strongly suggest incomplete rendering rather than a real gap in Stripe's event catalog, and
(b) `stripe-python`'s generated enum is itself sourced from Stripe's internal spec, making it an
independently authoritative cross-check, not a fallback. The merged list is written to
[`event-types-closed-set.txt`](./event-types-closed-set.txt) in this directory (one type per line, sorted,
266 entries) — this is the file a downstream functional spec should treat as the closed `event.type` set
for a faithful emulation, until a live-API listing (or an un-gated Treasury docs fetch) can settle whether
Treasury events belong in scope for this project at all (Treasury is not named in project_overview.md
§4's scope list per `scope-boundary-edges.md`, so most of the 30 disputed entries are likely out of scope
regardless — flagging the count discrepancy for completeness rather than because Treasury needs to be
built).

**URLs:** https://docs.stripe.com/api/events/types;
`research/repos/stripe-python/stripe/params/_webhook_endpoint_create_params.py` (local file, read
directly).

**Disposition:** **Closed**, with the closed-set file delivered. This was previously an explicit "not
derivable from `spec3.json`, needs docs.stripe.com or stripe-python" gap in `summary.md` — now resolved
via both named sources, merged, with the one real discrepancy (Treasury) documented rather than silently
picked one way.

---

## 10. `smor_resource_managed_payments` — identified (low-stakes, timeboxed as instructed)

**Question:** Can the docs now explain this field, found on `payment_intent.managed_payments` /
`setup_intent.managed_payments`?

**Answer: Identified.** "SMOR" = **Stripe Merchant Of Record**. The field ties to Stripe's **Managed
Payments** product, where Stripe itself (via its acquiring affiliates) becomes the merchant of record for
a seller's transaction — handling global sales-tax/VAT/GST compliance, and used with Checkout or Payment
Links to sell digital goods (SaaS, software, digital content) without the seller operating local business
entities per country.

**Verbatim quote** (`docs.stripe.com/payments/managed-payments/how-it-works`, found via targeted search
and confirmed on the page):

> "Use Managed Payments with Stripe Checkout or Payment Links to sell digital products such as SaaS,
> software, and digital content or downloads, without operating local business entities in each country
> where you sell your products. Managed Payments is the Stripe merchant of record solution that handles
> [sales tax, VAT, and GST compliance in more than 80 countries, among other things]... When you use
> Managed Payments, either Stripe Payments Company or Stripe Technology Europe, Limited (Stripe acquiring
> affiliates) acquires your transactions."

`docs.stripe.com/api/payment_intents/object` and `docs.stripe.com/api/setup_intents/object` both list
`managed_payments` as a `nullable object` field but neither gives a field-level property breakdown in the
reference page's visible attribute list (it's grouped under a collapsed "More parameters"-style section
with no expanded sub-schema shown in the fetched content) — so the *shape* of the object remains
unconfirmed, only its *product meaning*.

**URLs:** https://docs.stripe.com/payments/managed-payments/how-it-works;
https://docs.stripe.com/api/payment_intents/object; https://docs.stripe.com/api/setup_intents/object

**Disposition:** `scope-boundary-edges.md`'s characterization as "an unidentifiable preview feature" is
**corrected** — it is now identified (Managed Payments / merchant-of-record). The scope **ruling itself
is unchanged and still correct**: Managed Payments is not named in project_overview.md §4's in-scope
product list, it's a distinct seller-facing product surface (tax-compliance delegation), and nulling
`managed_payments` still costs nothing for this project's eval scenarios. Update
`scope-boundary-edges.md`'s wording from "unidentifiable" to "identified as Managed Payments /
merchant-of-record, out of scope by the same reasoning as before" — the ruling (null) stands, only the
"can't identify it" framing is corrected.

---

## Sources used this pass

- `mcp__Tavily__tavily_extract` (direct, full-page reads): `docs.stripe.com/api/versioning`,
  `docs.stripe.com/api/enums`, `docs.stripe.com/api/events/types`,
  `docs.stripe.com/payments/managed-payments/how-it-works`, `docs.stripe.com/api/payment_intents/object`,
  `docs.stripe.com/api/setup_intents/object`.
- `mcp__Tavily__tavily_search`: targeted queries for the SMOR/managed_payments identification.
- `research/repos/stripe-python/stripe/params/_webhook_endpoint_create_params.py` — local file, read
  directly, for the independent `enabled_events` closed-enum cross-check.
