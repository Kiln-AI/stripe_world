"""Customer balance transactions (Phase 15): the customer-scoped
adjustment API and the serializer for balance rows the billing engine
writes internally.

Four routes, all scoped to ``/v1/customers/{customer}/balance_transactions``:
- list (generated via ``ResourceSpec`` + ``Scope``)
- retrieve (generated)
- create (hand-written: mints an ``adjustment`` row and updates
  ``customer.balance``)
- update (hand-written: ``description`` and ``metadata`` only)

The billing engine (``invoicing._write_settlement_cbt``) writes the
``applied_to_invoice``, ``invoice_too_small``, ``invoice_overpaid``, and
``credit_note`` types.  This module's ``create`` handler writes
``adjustment`` only — the type an API caller can produce.

There is no top-level list path; the only list is scoped to one customer
(data_model §3.11, dispatcher §Routes).
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import seahaven

from seahaven_stripe_world import _ids, _json, _seq
from seahaven_stripe_world.dispatch.params import Param, ParamSpec
from seahaven_stripe_world.dispatch.resource import (
    ResourceSpec,
    Scope,
    register,
)
from seahaven_stripe_world.resources import _lookup, customers
from seahaven_stripe_world.serialize.fields import FieldMap, presence_sets, serializer_for

if TYPE_CHECKING:
    from seahaven_stripe_world.dispatch.response import Request

__all__ = [
    "CBT_CREATE",
    "CBT_LIST",
    "CBT_RETRIEVE",
    "CBT_UPDATE",
    "FIELDS",
    "SCOPE",
    "SPEC",
    "create",
    "serialize",
    "update",
]

CUS = ("cus_",)
CBTXN = ("cbtxn_",)

# --- ParamSpecs -----------------------------------------------------------------

CBT_LIST = ParamSpec(
    op_id="GetCustomersCustomerBalanceTransactions",
    path=("customer",),
    paginated=True,
)

CBT_RETRIEVE = ParamSpec(
    op_id="GetCustomersCustomerBalanceTransactionsTransaction",
    path=("customer", "transaction"),
)

CBT_CREATE = ParamSpec(
    op_id="PostCustomersCustomerBalanceTransactions",
    path=("customer",),
    body=(
        Param(name="amount", kind="integer", required=True),
        Param(name="currency", kind="currency", required=True),
        Param(name="description", kind="string", max_length=350),
    ),
    metadata=True,
)

CBT_UPDATE = ParamSpec(
    op_id="PostCustomersCustomerBalanceTransactionsTransaction",
    path=("customer", "transaction"),
    body=(Param(name="description", kind="string", max_length=350),),
    metadata=True,
)

# --- the serializer -----------------------------------------------------------------

always_present, omit_when_none = presence_sets("customer_balance_transaction")

FIELDS = FieldMap(
    object="customer_balance_transaction",
    table="customer_balance_transactions",
    columns={
        "id": "id",
        "created": "created",
        "amount": "amount",
        "credit_note": "credit_note",
        "currency": "currency",
        "customer": "customer",
        "description": "description",
        "ending_balance": "ending_balance",
        "invoice": "invoice",
        "metadata": "metadata",
        "type": "type",
    },
    timestamps=frozenset({"created"}),
    json_columns=frozenset({"metadata"}),
    constants={
        "livemode": False,
        "checkout_session": None,
        "customer_account": None,
    },
    always_present=always_present,
    omit_when_none=omit_when_none,
)

_serialize = serializer_for(FIELDS)


def serialize(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
    return _serialize(ctx, row)


SCOPE = Scope(
    path_param="customer",
    column="customer",
    parent=customers.SPEC,
)

SPEC = register(
    ResourceSpec(
        object="customer_balance_transaction",
        table="customer_balance_transactions",
        id_prefix="cbtxn_",
        collection_url="/v1/customers/{customer}/balance_transactions",
        serializer=serialize,
        columns=tuple(FIELDS.columns),
        missing_path_param="transaction",
        error_name="balance transaction",
        metadata=True,
    )
)

# --- handlers ----------------------------------------------------------------------


def create(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """``POST /v1/customers/{customer}/balance_transactions``:
    create an ``adjustment`` balance transaction.

    Adds the amount to the customer's balance and records the row.
    Sign convention: positive ``amount`` = the customer owes more;
    negative = credit to the customer.
    """
    customer_id = req.path_params["customer"]
    customer = _lookup.require_row(ctx, "customers", "customer", customer_id, param="customer")
    params = req.params
    amount = params["amount"]
    currency = params["currency"]

    ending_balance = customer["balance"] + amount
    metadata_text = "{}"
    if req.metadata is not None:
        metadata_text = _json.dumps(dict(req.metadata.apply({})))

    cbt_id = _ids.stripe_id(ctx, "cbtxn_")
    now = ctx.clock.iso()
    ctx.db.execute(
        "INSERT INTO customer_balance_transactions"
        " (id, x_seq, created, amount, currency, customer, description,"
        " ending_balance, metadata, type)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        cbt_id,
        _seq.next_seq(ctx, "customer_balance_transactions"),
        now,
        amount,
        currency,
        customer_id,
        params.get("description"),
        ending_balance,
        metadata_text,
        "adjustment",
    )

    # Update the customer's balance and stamp currency
    ctx.db.execute(
        "UPDATE customers SET balance = ?, currency = COALESCE(currency, ?) WHERE id = ?",
        ending_balance,
        currency,
        customer_id,
    )

    row = _lookup.require_row(
        ctx, "customer_balance_transactions", "balance transaction", cbt_id, param="id"
    )
    return serialize(ctx, row)


def update(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """``POST /v1/customers/{customer}/balance_transactions/{transaction}``:
    update ``description`` and/or ``metadata``."""
    customer_id = req.path_params["customer"]
    _lookup.require_row(ctx, "customers", "customer", customer_id, param="customer")
    txn_id = req.path_params["transaction"]
    row = _lookup.require_row(
        ctx,
        "customer_balance_transactions",
        "balance transaction",
        txn_id,
        param="transaction",
    )
    if row["customer"] != customer_id:
        from seahaven_stripe_world.stripe_errors import resource_missing

        raise resource_missing("balance transaction", txn_id, param="transaction")

    if "description" in req.params:
        ctx.db.execute(
            "UPDATE customer_balance_transactions SET description = ? WHERE id = ?",
            req.params["description"],
            txn_id,
        )
    if req.metadata is not None:
        current_row = _lookup.require_row(
            ctx,
            "customer_balance_transactions",
            "balance transaction",
            txn_id,
            param="transaction",
        )
        current_meta = _json.loads(current_row["metadata"]) if current_row["metadata"] else {}
        new_meta = _json.dumps(dict(req.metadata.apply(current_meta)))
        ctx.db.execute(
            "UPDATE customer_balance_transactions SET metadata = ? WHERE id = ?",
            new_meta,
            txn_id,
        )

    fresh = _lookup.require_row(
        ctx,
        "customer_balance_transactions",
        "balance transaction",
        txn_id,
        param="transaction",
    )
    return serialize(ctx, fresh)
