"""The only place a Stripe id is minted.

Stripe ids are `<prefix><suffix>` — `cus_…`, `pi_…`, `ch_…` — with the suffix
drawn from the instance's seeded stream (`ctx.ids.random`), so the same fixture
and seed mint the same ids. `ctx.ids.uuid()` is fixed-format UUIDv4 text and is
unusable here; nothing in this package calls it, and a bare UUID where Stripe
expects a prefix is the hazard `SEAHAVEN_FINDINGS.md` Entry 2 records.

The suffix is 24 characters from `[A-Za-z0-9]`: real Stripe suffixes vary in
length and encode nothing, so a fixed 24 is faithful in shape and simpler to
assert (`components/data_model.md` §2).
"""

import string

import seahaven

__all__ = [
    "ID_ALPHABET",
    "STRIPE_ID_PREFIXES",
    "STUB_ID_PREFIXES",
    "coupon_id",
    "stripe_id",
]

#: 62 characters, Stripe's own id shape.
ID_ALPHABET = string.ascii_letters + string.digits

#: object name -> id prefix, for every object this world stores. Sourced from
#: stripe-mock's fixtures (see `components/data_model.md` §2): `spec3.json`
#: carries no examples, so the prefixes cannot come from it.
STRIPE_ID_PREFIXES: dict[str, str] = {
    "customer": "cus_",
    "payment_method": "pm_",
    "product": "prod_",
    "price": "price_",
    # Coupons are the one unprefixed id; see `coupon_id`.
    "coupon": "",
    "promotion_code": "promo_",
    "tax_rate": "txr_",
    "payment_intent": "pi_",
    "charge": "ch_",
    "refund": "re_",
    # Probed 2026-09-20 at the pinned version: live disputes mint `du_…`
    # (Phase 9 cassette 05), not stripe-mock's older `dp_` fixtures.
    "dispute": "du_",
    "setup_intent": "seti_",
    "balance_transaction": "txn_",
    "payout": "po_",
    # Not `txn_` — the customer ledger and the balance ledger do not share one.
    "customer_balance_transaction": "cbtxn_",
    "subscription": "sub_",
    "subscription_item": "si_",
    "subscription_schedule": "sub_sched_",
    "invoice": "in_",
    "invoiceitem": "ii_",
    "line_item": "il_",
    "credit_note": "cn_",
    "credit_note_line_item": "cnli_",
    "discount": "di_",
    "event": "evt_",
}

#: Prefixes for ids this world synthesises but never resolves — stub references
#: at the scope boundary (`scope-boundary-edges.md`).
STUB_ID_PREFIXES: frozenset[str] = frozenset(
    {
        "ba_",  # external account: payout.destination
        "card_",  # legacy card external account
        "mandate_",  # setup_intent.mandate, single_use_mandate
        "setatt_",  # setup_intent.latest_attempt
    }
)

#: The request id echoed on `event.request.id`. Not an object; confirmed in
#: stripe-python's test fixtures rather than `spec3.json`, which lacks it.
REQUEST_ID_PREFIX = "req_"

# The empty prefix (coupon) is excluded: a bare 24-character token is not an id
# this world mints — coupons go through `coupon_id` — and admitting "" here
# would make `stripe_id(ctx, "")` silently legal.
_KNOWN_PREFIXES = (
    frozenset(prefix for prefix in STRIPE_ID_PREFIXES.values() if prefix)
    | STUB_ID_PREFIXES
    | {REQUEST_ID_PREFIX}
)


def stripe_id(ctx: seahaven.Ctx, prefix: str) -> str:
    """A Stripe-shaped id drawn from the instance's seeded stream.

    Returns `f"{prefix}{token}"` where token is 24 characters from
    `ID_ALPHABET`, each drawn from `ctx.ids.random`. Raises `WorldBug` if
    `prefix` is not one this world mints — an unknown prefix is an authoring
    mistake (a typo, or an id shape Stripe does not have), and it must not
    surface as a malformed id an agent could be blamed for.
    """
    if prefix not in _KNOWN_PREFIXES:
        raise seahaven.WorldBug(
            f"no Stripe id prefix {prefix!r}: not in _ids.STRIPE_ID_PREFIXES, "
            f"STUB_ID_PREFIXES or the request prefix"
        )
    token = "".join(ctx.ids.random.choice(ID_ALPHABET) for _ in range(24))
    return f"{prefix}{token}"


def coupon_id(ctx: seahaven.Ctx, supplied: str | None) -> str:
    """Coupons are the one resource whose id is caller-suppliable and unprefixed.

    Returns `supplied` if given, else an 8-character alphanumeric token —
    mixed case: the recording shows `hbzb1NEf`, `dj30FOe5` (Phase 7 probe,
    2026-09-20), not the uppercase-only shape `components/data_model.md` §2
    guessed. The recording wins.
    """
    if supplied is not None:
        return supplied
    return "".join(ctx.ids.random.choice(string.ascii_letters + string.digits) for _ in range(8))
