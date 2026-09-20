"""Probe: the catalog's event shapes and the deleted-customer stub.

Why a probe and not a merge-gate cassette: `/v1/events` is unrouted until
Phase 17, so its responses cannot be replayed by this world's own tools —
the same precedent as `probe_customer_payment_method_events`. The cassette
commits the evidence the phase's engine and resource code cites:

- `product.updated` / `coupon.updated` carry `previous_attributes` with
  `metadata` diffed **per key** (`metadata: {"a": null}` for a newly-set
  key), not the whole prior map;
- `product.deleted`'s snapshot carries `active: false` and `coupon.deleted`'s
  carries `valid: false` — the delete flips a serialized column before the
  snapshot row is read;
- `price.updated` fires for the cleared **holder** of a transferred
  lookup key, with `previous_attributes: {lookup_key: …}`, before the
  transferee's own event (holder-first order is this world's ruling,
  inferred from the sequential event ids — the type-filtered lists share
  one `created` second and cannot show order); a `default_price_data`
  product emits `product.created` (snapshot already carrying
  `default_price`) and then the inline price's `price.created` (same
  inference);
- a deleted **customer** retrieves as the three-key stub — unlike products
  and coupons, which retrieve as 404 (that half is in cassette 07).
"""

SCENARIO = "probe_catalog_events"
DESCRIPTION = (
    "Catalog event shapes: per-key metadata previous_attributes, the flipped "
    "delete snapshots, the transfer's holder-first price.updated pair, "
    "default_price_data's event order, and the deleted-customer stub."
)

_EMAIL = "probe-events@conformance.stripeapi.invalid"


def record(r):
    from datetime import UTC, datetime

    stamp = f"{datetime.now(UTC):%y%m%d%H%M}"
    product = r.step(
        "POST",
        "/v1/products",
        {"name": "probe events", "metadata": {"seed": "1"}},
        binds_as="product",
    )
    r.step(
        "POST",
        "/v1/products/{id}",
        {"description": "d", "metadata": {"a": "1"}},
        path_refs={"id": product},
    )
    r.step("DELETE", "/v1/products/{id}", path_refs={"id": product})
    r.step("GET", "/v1/events", {"type": "product.created", "limit": 1})
    r.step("GET", "/v1/events", {"type": "product.updated", "limit": 1})
    r.step("GET", "/v1/events", {"type": "product.deleted", "limit": 1})

    priced = r.step(
        "POST",
        "/v1/products",
        {
            "name": "probe dpd",
            "default_price_data": {"currency": "usd", "unit_amount": 1500},
        },
        binds_as="priced",
    )
    r.step("GET", "/v1/events", {"type": "product.created", "limit": 1})
    r.step("GET", "/v1/events", {"type": "price.created", "limit": 1})

    r.step(
        "POST",
        "/v1/prices",
        {
            "currency": "usd",
            "unit_amount": 999,
            "product": priced,
            "lookup_key": f"probe{stamp}",
        },
        binds_as="holder",
    )
    r.step(
        "POST",
        "/v1/prices",
        {
            "currency": "usd",
            "unit_amount": 555,
            "product": priced,
            "lookup_key": f"probe{stamp}",
            "transfer_lookup_key": True,
        },
        binds_as="transferee",
    )
    r.step("GET", "/v1/events", {"type": "price.updated", "limit": 2})

    coupon = r.step(
        "POST",
        "/v1/coupons",
        {"percent_off": 10, "duration": "once"},
        binds_as="coupon",
    )
    # The caller-supplied coupon id and its duplicate refusal: reserved
    # forever once used (tombstone included), which is exactly why this
    # pair cannot live in a replayable scenario — only in a probe, whose
    # stamp-derived id is fresh per recording.
    r.step(
        "POST",
        "/v1/coupons",
        {"id": f"PROBE{stamp}", "percent_off": 10, "duration": "forever"},
        binds_as="custom",
    )
    r.step("POST", "/v1/coupons", {"id": f"PROBE{stamp}", "percent_off": 5, "duration": "once"})
    # The promotion code rides the coupon while it is still live — a
    # tombstoned coupon is as missing as an absent one on a request
    # parameter (cassette 06).
    promo = r.step(
        "POST",
        "/v1/promotion_codes",
        {"promotion": {"type": "coupon", "coupon": coupon}},
        binds_as="promo",
    )
    r.step(
        "POST",
        "/v1/promotion_codes/{promotion_code}",
        {"active": False},
        path_refs={"promotion_code": promo},
    )
    r.step("GET", "/v1/events", {"type": "promotion_code.updated", "limit": 1})
    r.step(
        "POST",
        "/v1/coupons/{coupon}",
        {"name": "renamed", "metadata": {"k": "v"}},
        path_refs={"coupon": coupon},
    )
    r.step("DELETE", "/v1/coupons/{coupon}", path_refs={"coupon": coupon})
    r.step("GET", "/v1/events", {"type": "coupon.updated", "limit": 1})
    r.step("GET", "/v1/events", {"type": "coupon.deleted", "limit": 1})

    tax_rate = r.step(
        "POST",
        "/v1/tax_rates",
        {"display_name": "probe", "inclusive": False, "percentage": 10},
        binds_as="tax_rate",
    )
    r.step(
        "POST",
        "/v1/tax_rates/{tax_rate}",
        {"active": False, "description": "archived"},
        path_refs={"tax_rate": tax_rate},
    )
    r.step("GET", "/v1/events", {"type": "tax_rate.updated", "limit": 1})

    # The deleted-customer stub: a 200 three-key retrieve, unlike the
    # product/coupon 404s cassette 07 records.
    customer = r.step(
        "POST",
        "/v1/customers",
        {"email": _EMAIL},
        binds_as="customer",
    )
    r.step("DELETE", "/v1/customers/{customer}", path_refs={"customer": customer})
    r.step("GET", "/v1/customers/{customer}", path_refs={"customer": customer})
    r.step("GET", "/v1/events", {"type": "customer.deleted", "limit": 1})


CLEANUP = {"customer": "/v1/customers", "product": "/v1/products", "coupon": "/v1/coupons"}
