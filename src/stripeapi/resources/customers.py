"""The customers slice — **the dispatcher phase's throwaway**, replaced by the
real slice when the customers-and-payment-methods phase lands.

What is provisional here: the `ResourceSpec`'s create/update allowlists (a
fraction of `POST /v1/customers`' 23 spec properties), and the wiring of only
the five core routes. What is permanent: the table (the DDL
`components/data_model.md` §3 gives verbatim), the field map, and the
serializer rules — `x_seq` assignment, Unix-second `created`, the null-versus-
absent set derived from the pinned spec, and the created-customer defaults
this phase probed live (`tax_exempt: "none"`, `delinquent: false`,
`invoice_settings` with four null keys, an 8-character uppercase
`invoice_prefix`).
"""

import string

import seahaven

from stripeapi.dispatch.params import Param, ParamSpec
from stripeapi.dispatch.resource import (
    DeleteSpec,
    ListFilter,
    ResourceSpec,
    register,
)
from stripeapi.serialize.fields import OMIT, FieldMap, presence_sets, serializer_for

__all__ = ["FIELDS", "SPEC"]

CUS = ("cus_",)

# The create/update allowlists are the throwaway part: a real slice accepts
# more of the spec's own parameters. Everything here round-trips a real column.
_CUSTOMER_BODY = (
    Param(name="email", kind="string", max_length=5_000),
    Param(name="name", kind="string", max_length=5_000),
    Param(name="description", kind="string", max_length=5_000, unset_with_empty_string=True),
    Param(name="phone", kind="string", max_length=5_000),
    Param(name="tax_exempt", kind="literal", choices=("exempt", "none", "reverse")),
    Param(name="invoice_prefix", kind="string", max_length=12),
    Param(name="next_invoice_sequence", kind="integer", minimum=1),
    Param(
        name="invoice_settings",
        kind="object",
        shape=(
            Param(name="default_payment_method", kind="string", max_length=5_000),
            Param(name="footer", kind="string", max_length=255, unset_with_empty_string=True),
        ),
    ),
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
        "livemode": False,
        "default_source": None,
        "test_clock": None,
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


def _before_create(ctx: seahaven.Ctx, req: object, cols: dict[str, object]) -> dict[str, object]:
    """The probed defaults of a freshly created customer."""
    cols.setdefault("invoice_prefix", _mint_prefix(ctx))
    # `tax_exempt` and `delinquent` are nullable on the wire but default to
    # `none` / `false` in the response, so the row carries the defaults rather
    # than NULL.
    cols.setdefault("tax_exempt", "none")
    cols.setdefault("delinquent", 0)
    cols.setdefault("invoice_settings", _DEFAULT_INVOICE_SETTINGS)
    return cols


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
        list_filters=(
            ListFilter(name="email", column="email", kind="exact"),
            ListFilter(name="created", column="created", kind="range"),
        ),
        creatable=CUSTOMER_CREATE,
        updatable=CUSTOMER_UPDATE,
        delete=DeleteSpec(mode="soft"),
        metadata=True,
        before_create=_before_create,
        created_event="customer.created",
        updated_event="customer.updated",
        deleted_event="customer.deleted",
    )
)
