"""The customers slice — the first real one, replacing the dispatcher phase's
throwaway.

Everything here is pinned by recording or live probe at `2026-08-26.dahlia`
(cassette 02 and this phase's probes, 2026-09-19): the create/update body is
the dahlia request surface minus the scope-boundary cuts (`cash_balance`,
`tax`, `tax_id_data`, `test_clock`, `source`, and the half-modelled
`payment_method`-at-create convenience, which the payment-intents phase owns);
nested objects serialize with every key present and update per leaf — a
`shipping: {}` update is a no-op, probed; and a nonzero `balance` stamps
`currency` with the account default, probed (the sandbox account's default is
`cad`; this world's single account defaults to `usd` until the account object
lands — declared structural difference).
"""

import re
import string
from typing import TYPE_CHECKING

import seahaven

from seahaven_stripe_world import _json
from seahaven_stripe_world.dispatch.params import Param, ParamSpec
from seahaven_stripe_world.dispatch.resource import (
    DeleteSpec,
    ListFilter,
    ResourceSpec,
    register,
)
from seahaven_stripe_world.serialize.fields import OMIT, FieldMap, presence_sets, serializer_for
from seahaven_stripe_world.stripe_errors import invalid_request

if TYPE_CHECKING:
    from seahaven_stripe_world.dispatch.response import Request

__all__ = ["ACCOUNT_DEFAULT_CURRENCY", "FIELDS", "SPEC"]

CUS = ("cus_",)

PM = ("pm_",)

# --- canonical nested shapes (recorded: every key present, missing keys null)

_ADDRESS_KEYS = ("city", "country", "line1", "line2", "postal_code", "state")
_SHIPPING_KEYS = ("address", "name", "phone")  # carrier / tracking_number appear only when set
_INVOICE_SETTING_KEYS = ("custom_fields", "default_payment_method", "footer", "rendering_options")


def _canonical_address(value: dict | None) -> dict | None:
    if value is None:
        return None
    return {key: value.get(key) for key in _ADDRESS_KEYS}


_NULL_ADDRESS = {key: None for key in _ADDRESS_KEYS}


def _canonical_shipping(value: dict | None) -> dict | None:
    if value is None:
        return None
    # The pinned spec types shipping.address as non-nullable, and the
    # recording agrees: shipping, when present, always carries a full address
    # object (all-null when none of it was provided).
    address = value.get("address")
    canonical = {
        "address": _canonical_address(address) if address is not None else dict(_NULL_ADDRESS),
        "name": value.get("name"),
        "phone": value.get("phone"),
    }
    # Recorded: `carrier` and `tracking_number` are absent until a caller
    # sets them — the wire keeps unset keys out even though the schema marks
    # both nullable.
    for key in ("carrier", "tracking_number"):
        if value.get(key) is not None:
            canonical[key] = value[key]
    return canonical


def _canonical_invoice_settings(value: dict | None) -> dict:
    return {key: (value or {}).get(key) for key in _INVOICE_SETTING_KEYS}


def _merge_canonical(current_text: str | None, update: dict | None, canonical) -> str | None:
    """Stripe's nested-object update: present leaf means set, absent leaf
    means unchanged (`shipping: {}` is a no-op — probed). One level deep,
    plus the nested `address` when both sides carry one."""
    if update is None:
        return current_text
    loaded: object = _json.loads(current_text) if current_text is not None else None
    current: dict = loaded if isinstance(loaded, dict) else {}
    merged = {**current, **update}
    if isinstance(current.get("address"), dict) and isinstance(update.get("address"), dict):
        merged["address"] = {**current["address"], **update["address"]}
    return _json.dumps(canonical(merged))


# --- the create/update body (the dahlia request surface minus scope cuts)

_ADDRESS_BODY = tuple(Param(name=key, kind="string", max_length=5_000) for key in _ADDRESS_KEYS)

_CUSTOMER_BODY = (
    Param(name="address", kind="object", shape=_ADDRESS_BODY),
    Param(name="balance", kind="integer"),
    Param(name="description", kind="string", max_length=5_000, unset_with_empty_string=True),
    Param(name="email", kind="string", max_length=5_000),
    # The 1-12-uppercase-alphanumerics contract is enforced in the
    # normalizers, not here: its refusal is one bespoke message for every
    # violation (too short, too long, lowercase, punctuation — all probed),
    # which a `max_length` alone would shadow with the generic
    # exceeds-characters error.
    Param(name="invoice_prefix", kind="string"),
    Param(
        name="invoice_settings",
        kind="object",
        shape=(
            Param(name="default_payment_method", kind="id", id_prefixes=PM),
            Param(name="footer", kind="string", max_length=255, unset_with_empty_string=True),
        ),
    ),
    Param(name="name", kind="string", max_length=5_000),
    Param(name="next_invoice_sequence", kind="integer", minimum=1),
    Param(name="phone", kind="string", max_length=5_000),
    Param(
        name="preferred_locales",
        kind="array",
        item=Param(name="", kind="string", max_length=5_000),
    ),
    Param(
        name="shipping",
        kind="object",
        shape=(
            Param(name="address", kind="object", shape=_ADDRESS_BODY),
            Param(name="carrier", kind="string", max_length=5_000),
            Param(name="name", kind="string", max_length=5_000),
            Param(name="phone", kind="string", max_length=5_000),
            Param(name="tracking_number", kind="string", max_length=5_000),
        ),
    ),
    Param(name="tax_exempt", kind="literal", choices=("exempt", "none", "reverse")),
)

CUSTOMER_CREATE = ParamSpec(
    op_id="PostCustomers",
    body=_CUSTOMER_BODY,
    metadata=True,
)

CUSTOMER_UPDATE = ParamSpec(
    op_id="PostCustomersCustomer",
    path=("customer",),
    body=_CUSTOMER_BODY,
    metadata=True,
)

CUSTOMER_LIST = ParamSpec(
    op_id="GetCustomers",
    paginated=True,
)

CUSTOMER_RETRIEVE = ParamSpec(
    op_id="GetCustomersCustomer",
    path=("customer",),
)

CUSTOMER_DELETE = ParamSpec(
    op_id="DeleteCustomersCustomer",
    path=("customer",),
    expand=False,  # one of the nine stub DELETEs (components/dispatcher.md §2.4)
)

always_present, omit_when_none = presence_sets("customer")

FIELDS = FieldMap(
    object="customer",
    table="customers",
    columns={
        "id": "id",
        "address": "address",
        "balance": "balance",
        "created": "created",
        "currency": "currency",
        "delinquent": "delinquent",
        "description": "description",
        "discount": "discount",
        "email": "email",
        "invoice_prefix": "invoice_prefix",
        "invoice_settings": "invoice_settings",
        "metadata": "metadata",
        "name": "name",
        "next_invoice_sequence": "next_invoice_sequence",
        "phone": "phone",
        "preferred_locales": "preferred_locales",
        "shipping": "shipping",
        "tax_exempt": "tax_exempt",
    },
    timestamps=frozenset({"created"}),
    json_columns=frozenset(
        {"address", "discount", "invoice_settings", "metadata", "preferred_locales", "shipping"}
    ),
    booleans=frozenset({"delinquent"}),
    constants={
        "default_source": None,
        "test_clock": None,
        # Always emitted `null` on a fresh customer at the pinned version
        # (recorded Phase 5, scenario 02); no column exists until something
        # can set it.
        "customer_account": None,
        # Cut at the scope boundary and never emitted: the last three are
        # expand-only inline lists this world does not serve.
        "cash_balance": OMIT,
        "sources": OMIT,
        "subscriptions": OMIT,
        "tax": OMIT,
        "tax_ids": OMIT,
    },
    always_present=always_present,
    omit_when_none=omit_when_none,
)

_DEFAULT_INVOICE_SETTINGS = (
    '{"custom_fields":null,"default_payment_method":null,"footer":null,"rendering_options":null}'
)

#: The single account's default currency, stamped on a customer whose balance
#: is set nonzero (probed: `balance=500` at create answers `currency: "cad"`
#: on the recording account, `balance=0` answers null, and `currency` itself
#: is not a settable parameter). The real default is an account property;
#: this world has one static account and no account object until Phase 21, so
#: the constant is the fixture-level choice. Declared structural difference.
ACCOUNT_DEFAULT_CURRENCY = "usd"


def _stamp_balance_currency(cols: dict[str, object]) -> None:
    if isinstance(cols.get("balance"), int) and cols["balance"] != 0:
        cols["currency"] = ACCOUNT_DEFAULT_CURRENCY


#: `invoice_prefix`'s contract at the pinned version: `'a'`, `'abc'`, `'AB_1'` and 13 characters
#: all refuse with one bespoke message (recorded, `probe_invoice_prefix`); a 10-character prefix
#: is accepted (recorded). The 1-12 acceptance boundaries are unit-tested from the recorded
#: message's own range, not recorded — every accepted create permanently reserves its prefix on
#: the recording account.
_INVOICE_PREFIX = re.compile(r"^[A-Z0-9]{1,12}$")


def _check_invoice_prefix(cols: dict[str, object]) -> None:
    """`invoice_prefix` is 1 through 12 uppercase letters or numbers, and
    every violation of that answers one bespoke message, verbatim (probed at
    the pinned version: `'a'`, `'abc'`, 13 characters and `'AB_1'` all refuse
    identically, with no `code` and no `param`; the en dash is Stripe's).
    Evidence: cassette `probe_invoice_prefix`."""
    prefix = cols.get("invoice_prefix")
    if isinstance(prefix, str) and not _INVOICE_PREFIX.fullmatch(prefix):
        raise invalid_request(
            "Invoice number prefix must be 1–12 uppercase letters or numbers.",  # noqa: RUF001 — the en dash is Stripe's own, probed verbatim
            pre_execution=True,
        )


def _reject_taken_prefix(ctx: seahaven.Ctx, prefix: str, *, own_id: str | None = None) -> None:
    """Prefix uniqueness, refused the way the probed API refuses it rather
    than letting the UNIQUE index surface as an engine error: 400
    `invalid_request_error`, `param: invoice_prefix`, message verbatim, no
    `code` (recorded in `probe_invoice_prefix`, at the pinned version, for
    both create and update). Raised before any write, so raise is correct.
    `own_id` exempts a customer's own prefix — re-sending it is not a
    conflict."""
    holder = ctx.db.one("SELECT id FROM customers WHERE invoice_prefix = ?", prefix)
    if holder is not None and holder["id"] != own_id:
        raise invalid_request(
            f"This invoice number prefix is taken by customer: {holder['id']}. "
            "Please enter a different prefix.",
            param="invoice_prefix",
            pre_execution=True,
        )


def _mint_untaken_prefix(ctx: seahaven.Ctx) -> str:
    """A minted prefix that is genuinely free. The 36^8 space makes a second
    draw astronomically rare, but the UNIQUE index makes a collision a crash
    without this loop; the bound turns a pathological stream into a loud
    authoring error rather than a hang."""
    for _ in range(100):
        prefix = _mint_prefix(ctx)
        if ctx.db.one("SELECT id FROM customers WHERE invoice_prefix = ?", prefix) is None:
            return prefix
    raise seahaven.WorldBug("100 minted invoice prefixes all collided; the id stream is suspect")


def _before_create(ctx: seahaven.Ctx, req: Request, cols: dict[str, object]) -> dict[str, object]:
    """The probed defaults of a freshly created customer, and the nested
    objects in their canonical full-key shapes."""
    _check_invoice_prefix(cols)
    if "invoice_prefix" in cols:
        _reject_taken_prefix(ctx, str(cols["invoice_prefix"]))
    else:
        cols["invoice_prefix"] = _mint_untaken_prefix(ctx)
    # `tax_exempt` and `delinquent` are nullable on the wire but default to
    # `none` / `false` in the response, so the row carries the defaults rather
    # than NULL.
    cols.setdefault("tax_exempt", "none")
    cols.setdefault("delinquent", 0)
    cols.setdefault("invoice_settings", _DEFAULT_INVOICE_SETTINGS)
    if "address" in cols:
        cols["address"] = _json.dumps(_canonical_address(req.params.get("address")))
    if "shipping" in cols:
        cols["shipping"] = _json.dumps(_canonical_shipping(req.params.get("shipping")))
    _stamp_balance_currency(cols)
    return cols


def _before_update(ctx: seahaven.Ctx, req: Request, sets: dict[str, object]) -> dict[str, object]:
    """Leaf-wise merge for the nested objects, against the stored row.

    The normalizer contract allows a `SELECT` and nothing more
    (`components/dispatcher.md` §ResourceSpec), so the engine's before-row is
    re-read here rather than widening the contract.
    """
    row = ctx.db.one("SELECT * FROM customers WHERE id = ?", req.path_params["customer"])
    if row is None:  # the engine looked this row up moments ago
        raise seahaven.WorldBug("customers.before_update: row vanished under the engine")
    _check_invoice_prefix(sets)
    if "invoice_prefix" in sets:
        _reject_taken_prefix(ctx, str(sets["invoice_prefix"]), own_id=req.path_params["customer"])
    if "address" in sets:
        sets["address"] = _merge_canonical(
            row["address"], req.params.get("address"), _canonical_address
        )
    if "shipping" in sets:
        sets["shipping"] = _merge_canonical(
            row["shipping"], req.params.get("shipping"), _canonical_shipping
        )
    if "invoice_settings" in sets:
        sets["invoice_settings"] = _merge_canonical(
            row["invoice_settings"], req.params.get("invoice_settings"), _canonical_invoice_settings
        )
    _stamp_balance_currency(sets)
    return sets


def _mint_prefix(ctx: seahaven.Ctx) -> str:
    """`invoice_prefix`: 8 uppercase alphanumerics from the seeded stream
    (`components/data_model.md` §3.13) — the same shape as a minted coupon id."""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(ctx.ids.random.choice(alphabet) for _ in range(8))


SPEC = register(
    ResourceSpec(
        object="customer",
        table="customers",
        id_prefix="cus_",
        collection_url="/v1/customers",
        serializer=serializer_for(FIELDS),
        columns=tuple(FIELDS.columns),
        # Probed on all three top-level routes (recorded Phase 5, scenario 02):
        # a missing customer id names `param: "id"`, not the `{customer}`
        # placeholder — while the nested `balance_transactions` path keeps the
        # placeholder (`ResourceSpec.missing_path_param`).
        missing_path_param="id",
        list_filters=(
            ListFilter(name="email", column="email", kind="exact"),
            ListFilter(name="created", column="created", kind="range"),
        ),
        creatable=CUSTOMER_CREATE,
        updatable=CUSTOMER_UPDATE,
        delete=DeleteSpec(mode="soft"),
        metadata=True,
        before_create=_before_create,
        before_update=_before_update,
        created_event="customer.created",
        updated_event="customer.updated",
        deleted_event="customer.deleted",
    )
)
