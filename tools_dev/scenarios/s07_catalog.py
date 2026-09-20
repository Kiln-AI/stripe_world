"""Scenario 7: the catalog slice, end to end.

Every step is replayable by this world's own tools at the pinned version:
the product cycle (with `default_price_data` and the tombstone), one-time /
recurring / tiered prices with the lookup-key conflict and transfer, the
coupon cycle, promotion codes under the pinned version's `promotion`
nesting (minted and caller-supplied codes, restrictions, filters), and the
tax-rate cycle.

Isolation, and why every literal here is fixed rather than derived: the
replayer compares the script's declared params against the cassette
byte-for-byte, so a time-derived value would drift on every later replay.
The lookup key is freed by the scenario's own closing clear (an
empty-string update, probed); the coded promotion code and both tax rates
are deactivated at the end; coupons and products are deleted by CLEANUP. A
*crashed* recording leaves the key held — it cannot be freed except by
another transfer or clear — so clean up manually before re-recording. The
caller-supplied coupon id cannot appear here at all: ids are reserved
forever, tombstone included, so its create-and-duplicate pair lives in the
`probe_catalog_events` cassette instead, where no replay constraint exists.
"""

SCENARIO = "07_catalog"
DESCRIPTION = (
    "The catalog slice: products (with default_price_data, delete), prices "
    "(recurring, tiered, lookup_key conflict + transfer, product_data), "
    "coupons, promotion_codes under the promotion nesting, tax_rates, and "
    "the catalog 404s."
)

#: Fixed, because the replayer compares declared params byte-for-byte —
#: see the module docstring. Freed by the scenario's closing clear step.
_LOOKUP_KEY = "s07-key"


def record(r):
    # --- products -------------------------------------------------------------
    widget = r.step("POST", "/v1/products", {"name": "Widget"}, binds_as="widget")
    r.step("GET", "/v1/products/{id}", path_refs={"id": widget})
    r.step(
        "POST",
        "/v1/products/{id}",
        {"description": "updated", "metadata": {"a": "1"}},
        path_refs={"id": widget},
    )
    full = r.step(
        "POST",
        "/v1/products",
        {
            "name": "Full",
            "shippable": True,
            "statement_descriptor": "P7 DESC",
            "unit_label": "widget",
            "url": "https://example.test/full",
            "tax_code": "txcd_10103000",
            "description": "d",
            "images": ["https://example.test/i.png"],
            "marketing_features": [{"name": "feature one"}],
            "package_dimensions": {"height": 1, "length": 2, "weight": 3, "width": 4},
        },
        binds_as="full",
    )
    r.step("GET", "/v1/products/{id}", path_refs={"id": full})
    priced = r.step(
        "POST",
        "/v1/products",
        {
            "name": "Priced",
            "default_price_data": {
                "currency": "usd",
                "unit_amount": 1500,
                "recurring": {"interval": "month"},
            },
        },
        binds_as="priced",
    )
    r.step("GET", "/v1/products/{id}", path_refs={"id": priced})
    r.step("DELETE", "/v1/products/{id}", path_refs={"id": widget})
    # A deleted product is a 404 on retrieve, naming `id` (probed).
    r.step("GET", "/v1/products/{id}", path_refs={"id": widget})
    r.step("GET", "/v1/products/prod_nope")
    # An unknown id in `ids` is an empty page, not an error (probed).
    r.step("GET", "/v1/products", {"ids": [widget, "prod_nope"]})
    # The standard missing-parameter refusal, and an unknown default_price.
    r.step("POST", "/v1/products", {})
    r.step("POST", "/v1/products/{id}", {"default_price": "price_nope"}, path_refs={"id": priced})

    # --- prices ---------------------------------------------------------------
    # `currency_options` rides on this step so the cassette carries the
    # evidence that no response body ever includes it (it cannot ride the
    # decimal price below: `unit_amount_decimal` and a per-currency amount
    # are mutually exclusive, probed).
    r.step(
        "POST",
        "/v1/prices",
        {
            "currency": "usd",
            "unit_amount": 2000,
            "product": priced,
            "nickname": "one time",
            "currency_options": {"eur": {"unit_amount": 90}},
        },
        binds_as="one_time",
    )
    holder = r.step(
        "POST",
        "/v1/prices",
        {
            "currency": "usd",
            "unit_amount": 999,
            "product": priced,
            "recurring": {"interval": "month"},
            "lookup_key": _LOOKUP_KEY,
        },
        binds_as="holder",
    )
    # The conflict names the holding price's id; the allow-list entry is
    # predicated on the message's whole variable content being that id.
    r.step(
        "POST",
        "/v1/prices",
        {"currency": "usd", "unit_amount": 500, "product": priced, "lookup_key": _LOOKUP_KEY},
    )
    transferee = r.step(
        "POST",
        "/v1/prices",
        {
            "currency": "usd",
            "unit_amount": 555,
            "product": priced,
            "lookup_key": _LOOKUP_KEY,
            "transfer_lookup_key": True,
        },
        binds_as="transferee",
    )
    # The holder keeps active, its key cleared (probed).
    r.step("GET", "/v1/prices/{price}", path_refs={"price": holder})
    r.step(
        "POST",
        "/v1/prices",
        {
            "currency": "usd",
            "product": priced,
            "billing_scheme": "tiered",
            "tiers_mode": "graduated",
            "recurring": {"interval": "month"},
            "tiers": [{"up_to": 5, "unit_amount": 500}, {"up_to": "inf", "unit_amount": 400}],
        },
        binds_as="tiered",
    )
    decimal = r.step(
        "POST",
        "/v1/prices",
        {"currency": "usd", "product": priced, "unit_amount_decimal": "123.45"},
        binds_as="decimal",
    )
    # The custom-unit-amount shape: `{maximum, minimum, preset}` echoed, the
    # request's `enabled` switch not surviving onto the wire.
    r.step(
        "POST",
        "/v1/prices",
        {
            "currency": "usd",
            "product": priced,
            "custom_unit_amount": {
                "enabled": True,
                "maximum": 10000,
                "minimum": 100,
                "preset": 500,
            },
        },
        binds_as="custom",
    )
    r.step("GET", "/v1/prices/{price}", path_refs={"price": decimal})
    # product_data creates the inline product.
    r.step(
        "POST",
        "/v1/prices",
        {"currency": "eur", "unit_amount": 1000, "product_data": {"name": "inline prod"}},
    )
    # The recorded refusals with no id-shaped content.
    r.step("POST", "/v1/prices", {"currency": "usd", "unit_amount": 1000})
    r.step("POST", "/v1/prices", {"currency": "usd", "product": priced})
    r.step(
        "POST",
        "/v1/prices",
        {"currency": "usd", "unit_amount": 1000, "product": priced, "product_data": {"name": "x"}},
    )
    r.step(
        "POST",
        "/v1/prices",
        {
            "currency": "usd",
            "product": priced,
            "billing_scheme": "tiered",
            "tiers_mode": "volume",
            "tiers": [{"up_to": "inf", "unit_amount": 500}],
        },
    )
    r.step(
        "POST",
        "/v1/prices",
        {
            "currency": "usd",
            "product": priced,
            "unit_amount": 5,
            "recurring": {"interval": "month", "usage_type": "metered"},
        },
    )
    r.step("POST", "/v1/prices", {"currency": "usd", "product": "prod_nope", "unit_amount": 100})
    r.step("GET", "/v1/prices", {"product": priced, "type": "recurring", "limit": 3})
    r.step("GET", "/v1/prices", {"lookup_keys": [_LOOKUP_KEY]})
    # The conflict fires for an inactive holder too (probed) — the design's
    # live-only uniqueness reading was wrong.
    r.step("POST", "/v1/prices/{price}", {"active": False}, path_refs={"price": transferee})
    r.step(
        "POST",
        "/v1/prices",
        {"currency": "usd", "unit_amount": 777, "product": priced, "lookup_key": _LOOKUP_KEY},
    )
    r.step("POST", "/v1/prices/{price}", {"active": True}, path_refs={"price": transferee})
    # Isolation: an empty-string update clears the key (probed), freeing it
    # for a re-record — deactivating alone does not, as the block above
    # just demonstrated.
    r.step("POST", "/v1/prices/{price}", {"lookup_key": ""}, path_refs={"price": transferee})
    r.step(
        "GET",
        "/v1/prices",
        {"product": priced, "recurring": {"interval": "month"}, "limit": 3},
    )
    r.step("GET", "/v1/prices/price_nope")
    # A verb this path does not carry answers the same 404 as an unknown
    # path — recorded here so the router's correction carries a committed
    # artifact, message suffix and all.
    r.step("DELETE", "/v1/prices/{price}", path_refs={"price": decimal})
    r.step("GET", "/v1/widgets")
    # A product with attached prices refuses its delete (message verbatim).
    victim = r.step("POST", "/v1/products", {"name": "victim"}, binds_as="victim")
    r.step(
        "POST",
        "/v1/prices",
        {"currency": "usd", "unit_amount": 100, "product": victim},
    )
    r.step("DELETE", "/v1/products/{id}", path_refs={"id": victim})

    # --- coupons --------------------------------------------------------------
    percent = r.step(
        "POST", "/v1/coupons", {"percent_off": 22.5, "duration": "once"}, binds_as="percent"
    )
    # `applies_to` and `currency_options` are set here so the cassette
    # carries the evidence that no response body ever includes either.
    amount = r.step(
        "POST",
        "/v1/coupons",
        {
            "amount_off": 500,
            "currency": "usd",
            "duration": "repeating",
            "duration_in_months": 3,
            "name": "five off",
            "max_redemptions": 10,
            "applies_to": {"products": [priced]},
            "currency_options": {"eur": {"amount_off": 400}},
        },
        binds_as="amount",
    )
    r.step("GET", "/v1/coupons/{coupon}", path_refs={"coupon": percent})
    r.step(
        "POST",
        "/v1/coupons/{coupon}",
        {"name": "renamed", "metadata": {"k": "v"}},
        path_refs={"coupon": percent},
    )
    r.step("DELETE", "/v1/coupons/{coupon}", path_refs={"coupon": percent})
    r.step("GET", "/v1/coupons/{coupon}", path_refs={"coupon": percent})
    r.step("GET", "/v1/coupons", {"limit": 3})
    r.step("GET", "/v1/coupons/NOPE123")
    # The cross-field refusals, verbatim.
    r.step("POST", "/v1/coupons", {"duration": "once"})
    r.step(
        "POST",
        "/v1/coupons",
        {"percent_off": 10, "amount_off": 500, "currency": "usd", "duration": "once"},
    )
    r.step("POST", "/v1/coupons", {"amount_off": 500, "duration": "once"})
    r.step("POST", "/v1/coupons", {"percent_off": 10, "duration": "repeating"})
    r.step("POST", "/v1/coupons", {"percent_off": 0, "duration": "once"})
    r.step("POST", "/v1/coupons", {"percent_off": 10, "duration": "once", "redeem_by": 1000})
    r.step(
        "POST",
        "/v1/coupons",
        {"percent_off": 5, "duration": "once", "currency_options": {"eur": {"amount_off": 4}}},
    )
    # The map parameter's own refusals: a scalar, and a supported code in
    # the wrong case. (The caller-supplied coupon id and its duplicate
    # refusal live in `probe_catalog_events` — ids are reserved forever,
    # so no replayable scenario can create one twice.)
    r.step(
        "POST",
        "/v1/coupons",
        {"amount_off": 500, "currency": "usd", "duration": "once", "currency_options": 5},
    )
    r.step(
        "POST",
        "/v1/coupons",
        {
            "amount_off": 500,
            "currency": "usd",
            "duration": "once",
            "currency_options": {"EUR": {"amount_off": 4}},
        },
    )

    # --- promotion codes --------------------------------------------------------
    minted = r.step(
        "POST",
        "/v1/promotion_codes",
        {"promotion": {"type": "coupon", "coupon": amount}},
        binds_as="minted",
    )
    coded = r.step(
        "POST",
        "/v1/promotion_codes",
        {
            "promotion": {"type": "coupon", "coupon": amount},
            "code": "s07-promo",
            "restrictions": {
                "first_time_transaction": True,
                "minimum_amount": 1000,
                "minimum_amount_currency": "usd",
            },
        },
        binds_as="coded",
    )
    r.step(
        "GET",
        "/v1/promotion_codes/{promotion_code}",
        {"expand": ["promotion.coupon"]},
        path_refs={"promotion_code": coded},
    )
    r.step("GET", "/v1/promotion_codes", {"coupon": amount, "limit": 5})
    r.step("GET", "/v1/promotion_codes", {"code": "s07-promo", "active": True})
    r.step(
        "POST",
        "/v1/promotion_codes/{promotion_code}",
        {"active": False},
        path_refs={"promotion_code": minted},
    )
    r.step("GET", "/v1/promotion_codes", {"active": True, "coupon": amount, "limit": 5})
    r.step(
        "POST",
        "/v1/promotion_codes",
        {"promotion": {"type": "coupon", "coupon": "NOPE1234"}},
    )
    r.step(
        "POST",
        "/v1/promotion_codes",
        {"promotion": {"type": "coupon", "coupon": amount}, "code": "s07-promo"},
    )
    # The remaining recorded refusals.
    r.step(
        "POST",
        "/v1/promotion_codes",
        {"promotion": {"type": "coupon", "coupon": amount}, "code": "has spaces!"},
    )
    r.step("POST", "/v1/promotion_codes", {"promotion": {"type": "coupon"}})
    r.step(
        "POST",
        "/v1/promotion_codes",
        {"promotion": {"type": "coupon", "coupon": amount}, "customer": "cus_nope"},
    )
    r.step(
        "POST",
        "/v1/promotion_codes",
        {"promotion": {"type": "coupon", "coupon": amount}, "expires_at": 2000},
    )
    r.step(
        "POST",
        "/v1/promotion_codes",
        {"promotion": {"type": "coupon", "coupon": amount}, "expires_at": 4102444800},
    )
    # At the pinned version an update's restrictions carry only
    # currency_options; the other keys answer parameter_unknown.
    r.step(
        "POST",
        "/v1/promotion_codes/{promotion_code}",
        {"restrictions": {"first_time_transaction": True}},
        path_refs={"promotion_code": minted},
    )
    r.step("GET", "/v1/promotion_codes/promo_nope")

    # --- tax rates ---------------------------------------------------------------
    ca = r.step(
        "POST",
        "/v1/tax_rates",
        {
            "display_name": "CA sales tax",
            "jurisdiction": "US-CA",
            "percentage": 8.875,
            "inclusive": False,
            "country": "US",
            "state": "CA",
        },
        binds_as="ca",
    )
    vat = r.step(
        "POST",
        "/v1/tax_rates",
        {
            "display_name": "VAT",
            "percentage": 20,
            "inclusive": True,
            "tax_type": "vat",
            "jurisdiction": "DE",
        },
        binds_as="vat",
    )
    r.step(
        "POST",
        "/v1/tax_rates/{tax_rate}",
        {"active": False, "description": "archived"},
        path_refs={"tax_rate": ca},
    )
    r.step("GET", "/v1/tax_rates", {"inclusive": True, "active": True, "limit": 5})
    r.step("GET", "/v1/tax_rates", {"active": True, "limit": 5})
    r.step("GET", "/v1/tax_rates/txr_nope")
    # Isolation: a re-record's active/inclusive lists must see only that
    # run's live rates, and tax rates cannot be deleted — only deactivated.
    r.step("POST", "/v1/tax_rates/{tax_rate}", {"active": False}, path_refs={"tax_rate": vat})
    # Isolation: promotion codes cannot be deleted either; deactivating the
    # coded one lets a same-day re-record create it again.
    r.step(
        "POST",
        "/v1/promotion_codes/{promotion_code}",
        {"active": False},
        path_refs={"promotion_code": coded},
    )


CLEANUP = {
    "product": "/v1/products",
    "coupon": "/v1/coupons",
}
