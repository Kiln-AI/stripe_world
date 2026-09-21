"""The balance ledger's read surface: the account-wide list and retrieve.

Both routes are engine-served; the interesting parts are the filters (the
polymorphic `source`, whose dispute-shaped values the live API never matches
— probed, Phase 11 — and `payout`, which reads the world-internal `x_payout`
sweep column) and the retrieve spellings: `No such balance transaction:
'txn_…'` under `param: "id"` (probed), the space-spelled error name.

The rows themselves are written by `billing/ledger.py` and the money path;
nothing here creates, updates or deletes — the ledger is append-only, which
is what makes the balance a pure function of it.
"""

from collections.abc import Mapping
from typing import Any

import seahaven

from stripeapi.dispatch.params import ParamSpec
from stripeapi.dispatch.resource import ListFilter, ResourceSpec, register
from stripeapi.serialize.fields import FieldMap, presence_sets, serializer_for

__all__ = ["FIELDS", "SPEC", "serialize"]

PO = ("po_",)

# --- the ParamSpecs -----------------------------------------------------------------

BT_LIST = ParamSpec(
    op_id="GetBalanceTransactions",
    paginated=True,
)

BT_RETRIEVE = ParamSpec(
    op_id="GetBalanceTransactionsId",
    path=("id",),
)

# --- the serializer -----------------------------------------------------------------

always_present, omit_when_none = presence_sets("balance_transaction")

FIELDS = FieldMap(
    object="balance_transaction",
    table="balance_transactions",
    columns={
        "id": "id",
        "created": "created",
        "amount": "amount",
        "available_on": "available_on",
        "balance_type": "balance_type",
        "currency": "currency",
        "description": "description",
        "exchange_rate": "exchange_rate",
        "fee": "fee",
        "fee_details": "fee_details",
        "net": "net",
        "reporting_category": "reporting_category",
        "source": "source",
        "status": "status",
        "type": "type",
    },
    timestamps=frozenset({"created", "available_on"}),
    json_columns=frozenset({"fee_details"}),
    decimals=frozenset({"exchange_rate"}),
    constants={
        # `balance_transaction` is one of the four objects with no `livemode`
        # at the pinned version (data_model §8): emitting it would fail
        # schema conformance.
    },
    always_present=always_present,
    omit_when_none=omit_when_none,
)


def serialize(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
    return serializer_for(FIELDS)(ctx, row)


# --- the engine-served rest ----------------------------------------------------------

SPEC = register(
    ResourceSpec(
        object="balance_transaction",
        table="balance_transactions",
        id_prefix="txn_",
        collection_url="/v1/balance_transactions",
        serializer=serialize,
        columns=tuple(FIELDS.columns),
        # Probed (Phase 11): `No such balance transaction: 'txn_nope'`, with
        # the path placeholder spelled `id` and a space in the object name.
        missing_path_param="id",
        error_name="balance transaction",
        list_filters=(
            ListFilter(name="created", column="created", kind="range"),
            ListFilter(name="currency", column="currency", kind="exact"),
            # The sweep column behind the documented queryable behavior
            # (spec on payout.reconciliation_status: "you can use the Balance
            # Transactions API to list all balance transactions that are paid
            # out in this payout"). Recorded (cassette 11): the filter
            # validates the payout's existence — `No such payout: 'po_nope'`
            # at 400 — so it is a referencing filter, unlike `source`.
            ListFilter(
                name="payout",
                column="x_payout",
                kind="exact",
                id_prefixes=PO,
                references="payout",
            ),
            # Probed (Phase 11): the filter matches charge and refund sources
            # and never a dispute's, whatever the field itself carries; an
            # unknown value of any shape answers an empty page, not an error.
            ListFilter(name="source", column="source", kind="exact", never_prefixes=("du_",)),
            # Deliberately a plain string, not a literal filter: a bogus type
            # answers `[]` at 200 (probed), which a choices filter would 400.
            ListFilter(name="type", column="type", kind="exact"),
        ),
        delete=None,
        metadata=False,
    )
)
