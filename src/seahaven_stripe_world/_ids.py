"""The only place a Stripe id is minted.

Stripe ids are `<prefix><suffix>` — `cus_…`, `pi_…`, `ch_…` — with the suffix
drawn from the instance's seeded stream (`ctx.ids.random`), so the same fixture
and seed mint the same ids. `ctx.ids.uuid()` is fixed-format UUIDv4 text and is
unusable here; nothing in this package calls it, and a bare UUID where Stripe
expects a prefix is the hazard `SEAHAVEN_FINDINGS.md` Entry 2 records.

Two measured formats (id-shapes.md, probed 2026-09-22):

  Format A   prefix + 14 random chars from [A-Za-z0-9]         cus_, prod_, si_
  Format B   prefix + V(1) + T(5) + A(10) + R(8) = 24 chars    everything else
               V  version digit (1 = direct create, 3 = side-effect)
               T  base62 timestamp group — equal for objects created in the same second
               A  the account fragment: the last 10 chars of the account id's suffix
               R  8 random chars from [A-Za-z0-9]
"""

import string

import seahaven

from seahaven_stripe_world._time import to_unix

__all__ = [
    "FORMAT_A_PREFIXES",
    "ID_ALPHABET",
    "STRIPE_ID_PREFIXES",
    "STUB_ID_PREFIXES",
    "coupon_id",
    "stripe_id",
]

#: 62 characters, Stripe's own id shape.
ID_ALPHABET = string.ascii_letters + string.digits

# Base62 alphabet for timestamp encoding: digits first, then uppercase, then
# lowercase — standard base62 ordering so the encoded value sorts correctly.
_BASE62 = string.digits + string.ascii_uppercase + string.ascii_lowercase

#: Prefixes whose ids use Format A (14 random chars). Measured from real Stripe
#: sandbox (id-shapes.md): `cus_`, `prod_`, `si_` all have 14-char suffixes.
FORMAT_A_PREFIXES: frozenset[str] = frozenset({"cus_", "prod_", "si_"})

_FORMAT_A_SUFFIX_LEN = 14
_FORMAT_B_SUFFIX_LEN = 24
_TIMESTAMP_CHARS = 5
_ACCOUNT_FRAGMENT_LEN = 10
_RANDOM_CHARS_B = 8

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


def _base62_encode(value: int, width: int) -> str:
    """Encode a non-negative integer into a fixed-width base62 string."""
    if value < 0:
        raise ValueError(f"negative value: {value}")
    chars: list[str] = []
    for _ in range(width):
        value, remainder = divmod(value, 62)
        chars.append(_BASE62[remainder])
    # Reverse so the most-significant digit is first.
    return "".join(reversed(chars))


def _account_fragment(ctx: seahaven.Ctx) -> str:
    """The last 10 chars of the account id's suffix after `acct_`."""
    account = ctx.state.get("account")
    if account is None:
        raise seahaven.WorldBug("_ids: ctx.state['account'] is not set — startup has not run")
    acct_id: str = account["id"]
    # Account id is `acct_` + 16 chars. The fragment is the last 10 of the suffix.
    suffix = acct_id.removeprefix("acct_")
    if len(suffix) < _ACCOUNT_FRAGMENT_LEN:
        raise seahaven.WorldBug(
            f"_ids: account id suffix too short ({len(suffix)} chars): {acct_id!r}"
        )
    return suffix[-_ACCOUNT_FRAGMENT_LEN:]


def _base62_timestamp(iso_timestamp: str) -> str:
    """Encode a timestamp as a 5-character base62 group.

    Objects created in the same second share the same group — which is also
    true of real ids created in the same second (id-shapes.md).
    """
    unix = to_unix(iso_timestamp)
    return _base62_encode(unix, _TIMESTAMP_CHARS)


def stripe_id(
    ctx: seahaven.Ctx,
    prefix: str,
    *,
    timestamp: str | None = None,
    version_digit: str = "1",
) -> str:
    """A Stripe-shaped id drawn from the instance's seeded stream.

    Two formats (id-shapes.md):

    - **Format A** (cus_, prod_, si_): `prefix` + 14 random alphanumerics.
    - **Format B** (everything else): `prefix` + version_digit(1) +
      base62_timestamp(5) + account_fragment(10) + 8 random alphanumerics.

    `timestamp` is the ISO creation time being stamped on the row. Under a
    frozen clock all ids in a rollout share a timestamp group, which is also
    true of real ids created in the same second. If None, reads `ctx.clock`.

    `version_digit` defaults to `"1"` (direct create). Callers that mint
    side-effect ids (charges from PI confirmation, balance transactions,
    refunds) pass `"3"`.

    Raises `WorldBug` if `prefix` is unknown or if startup has not run
    (Format B needs the account fragment).
    """
    if prefix not in _KNOWN_PREFIXES:
        raise seahaven.WorldBug(
            f"no Stripe id prefix {prefix!r}: not in _ids.STRIPE_ID_PREFIXES, "
            f"STUB_ID_PREFIXES or the request prefix"
        )

    if prefix in FORMAT_A_PREFIXES:
        token = "".join(ctx.ids.random.choice(ID_ALPHABET) for _ in range(_FORMAT_A_SUFFIX_LEN))
        return f"{prefix}{token}"

    # Format B: structured suffix
    ts = timestamp if timestamp is not None else ctx.clock.iso()
    fragment = _account_fragment(ctx)
    t_group = _base62_timestamp(ts)
    random_part = "".join(ctx.ids.random.choice(ID_ALPHABET) for _ in range(_RANDOM_CHARS_B))
    token = f"{version_digit}{t_group}{fragment}{random_part}"
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
