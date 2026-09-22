"""Per-resource field allowlists for the seven search endpoints.

Each ``SearchSpec`` maps a field name the query language accepts to:
- its type (``token``, ``string``, ``numeric``), which gates which operators
  are legal;
- the SQL column (or expression) it reads from;
- whether the value needs a type coercion before SQL comparison.

The allowlists are taken verbatim from Stripe's documentation at
https://docs.stripe.com/search#supported-query-fields-for-each-resource
(pinned to 2026-08-26.dahlia, verified against the recording account).

Fields marked ``token`` support only ``:`` (case-insensitive exact match).
Fields marked ``string`` support ``:`` (phrase match) and ``~`` (substring).
Fields marked ``numeric`` support ``:``, ``>``, ``<``, ``>=``, ``<=``.
``metadata`` is handled generically on every resource and is always ``token``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

__all__ = [
    "SEARCH_SPECS",
    "SearchField",
    "SearchSpec",
]

FieldType = Literal["token", "string", "numeric"]


@dataclass(frozen=True, slots=True)
class SearchField:
    """One queryable field on a searchable resource."""

    name: str  # the field name in the query language
    type: FieldType
    column: str  # the SQL column or json_extract expression
    # When True the column stores a timestamp as ISO text and the query value
    # arrives as Unix seconds — coerce before comparison.
    is_timestamp: bool = False
    # When True the column stores a boolean as 0/1 but the query value is
    # the string "true"/"false".
    is_boolean: bool = False
    # When the column is a JSON column and we need json_extract.
    json_path: str | None = None


@dataclass(frozen=True, slots=True)
class SearchSpec:
    """The search allowlist for one resource."""

    resource: str  # "charge", "customer", ...
    table: str
    object_name: str  # the Stripe object discriminator
    url: str  # the search endpoint URL, for the response envelope
    fields: tuple[SearchField, ...]
    has_metadata: bool = True
    # The FTS5 virtual table name, when the resource has ``string``-typed
    # search fields.  Tables whose search fields are all ``token`` /
    # ``numeric`` (charges, payment_intents, subscriptions) have no FTS5
    # table and resolve entirely in plain SQL.
    fts_table: str | None = None


# --- Per-resource specs -------------------------------------------------------

_CHARGES_FIELDS: Final = (
    SearchField("amount", "numeric", "amount"),
    SearchField("created", "numeric", "created", is_timestamp=True),
    SearchField("currency", "token", "currency"),
    SearchField("customer", "token", "customer"),
    SearchField("disputed", "token", "disputed", is_boolean=True),
    SearchField("refunded", "token", "refunded", is_boolean=True),
    SearchField("status", "token", "status"),
)

_CUSTOMERS_FIELDS: Final = (
    SearchField("created", "numeric", "created", is_timestamp=True),
    SearchField("email", "string", "email"),
    SearchField("name", "string", "name"),
    SearchField("phone", "string", "phone"),
)

_INVOICES_FIELDS: Final = (
    SearchField("created", "numeric", "created", is_timestamp=True),
    SearchField("currency", "token", "currency"),
    SearchField("customer", "token", "customer"),
    SearchField("number", "string", "number"),
    # Stripe documents both as ``string`` (verified 2026-09-22). The values
    # are single-token (a closed enum and an id), so FTS5 phrase matching
    # reduces to a simple term match, which is correct and costs nothing
    # extra beyond what the index already carries. Typed ``string`` for
    # fidelity: Stripe allows the ``~`` (substring) operator on both.
    SearchField("status", "string", "status"),
    SearchField("subscription", "string", "parent_subscription"),
    SearchField("total", "numeric", "total"),
)

_PAYMENT_INTENTS_FIELDS: Final = (
    SearchField("amount", "numeric", "amount"),
    SearchField("created", "numeric", "created", is_timestamp=True),
    SearchField("currency", "token", "currency"),
    SearchField("customer", "token", "customer"),
    SearchField("status", "token", "status"),
)

_PRICES_FIELDS: Final = (
    SearchField("active", "token", "active", is_boolean=True),
    SearchField("currency", "token", "currency"),
    SearchField("lookup_key", "string", "lookup_key"),
    SearchField("product", "string", "product"),
    SearchField("type", "token", "type"),
)

_PRODUCTS_FIELDS: Final = (
    SearchField("active", "token", "active", is_boolean=True),
    SearchField("description", "string", "description"),
    SearchField("name", "string", "name"),
    SearchField("shippable", "token", "shippable", is_boolean=True),
    SearchField("url", "string", "url"),
)

_SUBSCRIPTIONS_FIELDS: Final = (
    SearchField("created", "numeric", "created", is_timestamp=True),
    SearchField("status", "token", "status"),
)

SEARCH_SPECS: Final[dict[str, SearchSpec]] = {
    "charges": SearchSpec(
        resource="charges",
        table="charges",
        object_name="charge",
        url="/v1/charges/search",
        fields=_CHARGES_FIELDS,
    ),
    "customers": SearchSpec(
        resource="customers",
        table="customers",
        object_name="customer",
        url="/v1/customers/search",
        fields=_CUSTOMERS_FIELDS,
        fts_table="customers_fts",
    ),
    "invoices": SearchSpec(
        resource="invoices",
        table="invoices",
        object_name="invoice",
        url="/v1/invoices/search",
        fields=_INVOICES_FIELDS,
        fts_table="invoices_fts",
    ),
    "payment_intents": SearchSpec(
        resource="payment_intents",
        table="payment_intents",
        object_name="payment_intent",
        url="/v1/payment_intents/search",
        fields=_PAYMENT_INTENTS_FIELDS,
    ),
    "prices": SearchSpec(
        resource="prices",
        table="prices",
        object_name="price",
        url="/v1/prices/search",
        fields=_PRICES_FIELDS,
        fts_table="prices_fts",
    ),
    "products": SearchSpec(
        resource="products",
        table="products",
        object_name="product",
        url="/v1/products/search",
        fields=_PRODUCTS_FIELDS,
        fts_table="products_fts",
    ),
    "subscriptions": SearchSpec(
        resource="subscriptions",
        table="subscriptions",
        object_name="subscription",
        url="/v1/subscriptions/search",
        fields=_SUBSCRIPTIONS_FIELDS,
    ),
}
