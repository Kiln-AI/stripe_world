"""The products slice: generated CRUD plus one hand-written create seam.

`default_price_data` writes a second row (the price) before the product's own
`product.created` snapshot is taken — the recorded snapshot carries
`default_price` already set (Phase 7 probe, 2026-09-20) — which is a
second-table write the engine's normalizer contract forbids, so the create is
hand-written around the same column derivation. Everything else (update, the
tombstone delete, the list filters) is the engine's.

The live body carries `attributes`, `type: "service"` and `tax_details`,
which the pinned spec does not declare on `product`; this world emits the
spec's property set and the conformance allow-list carries the difference —
the same ruling `shared_payment_granted_token` settled in Phase 6.
"""

from typing import TYPE_CHECKING, Any

import seahaven

from seahaven_stripe_world import _ids, _json, _seq
from seahaven_stripe_world.dispatch.params import Param, ParamSpec
from seahaven_stripe_world.dispatch.resource import (
    DeleteSpec,
    ListFilter,
    ResourceSpec,
    register,
)
from seahaven_stripe_world.resources import _lookup, events, prices
from seahaven_stripe_world.serialize.fields import FieldMap, presence_sets, serializer_for
from seahaven_stripe_world.stripe_errors import invalid_request

if TYPE_CHECKING:
    from seahaven_stripe_world.dispatch.response import Request

__all__ = ["FIELDS", "SPEC", "create", "insert_inline"]

PRICE = ("price_",)

_PACKAGE_DIMENSIONS = Param(
    name="package_dimensions",
    kind="object",
    shape=tuple(
        Param(name=key, kind="number", required=True)
        for key in ("height", "length", "weight", "width")
    ),
)

_MARKETING_FEATURES = Param(
    name="marketing_features",
    kind="array",
    item=Param(name="", kind="object", shape=(Param(name="name", kind="string", required=True),)),
)

#: The inline price a `default_price_data` describes: the amount half of the
#: prices body plus the currency its own request makes required.
_DEFAULT_PRICE_DATA = Param(
    name="default_price_data",
    kind="object",
    shape=(
        Param(name="currency", kind="string", max_length=5_000, required=True),
        *prices.PRICE_AMOUNT_BODY,
    ),
)

PRODUCT_CREATE = ParamSpec(
    op_id="PostProducts",
    body=(
        Param(name="name", kind="string", max_length=5_000, required=True),
        Param(name="active", kind="boolean"),
        Param(name="id", kind="string", max_length=5_000),
        _DEFAULT_PRICE_DATA,
        Param(name="description", kind="string", max_length=5_000, unset_with_empty_string=True),
        Param(name="images", kind="array", item=Param(name="", kind="string", max_length=5_000)),
        _MARKETING_FEATURES,
        Param(name="package_dimensions", kind="object", shape=_PACKAGE_DIMENSIONS.shape),
        Param(name="shippable", kind="boolean"),
        Param(name="statement_descriptor", kind="string", max_length=22),
        Param(name="tax_code", kind="string", max_length=5_000, unset_with_empty_string=True),
        Param(name="unit_label", kind="string", max_length=12, unset_with_empty_string=True),
        Param(name="url", kind="string", max_length=5_000, unset_with_empty_string=True),
    ),
    metadata=True,
)

PRODUCT_UPDATE = ParamSpec(
    op_id="PostProductsId",
    path=("id",),
    body=(
        Param(name="active", kind="boolean"),
        Param(name="default_price", kind="id", id_prefixes=PRICE),
        Param(name="description", kind="string", max_length=5_000, unset_with_empty_string=True),
        Param(name="images", kind="array", item=Param(name="", kind="string", max_length=5_000)),
        _MARKETING_FEATURES,
        Param(name="name", kind="string", max_length=5_000),
        Param(name="package_dimensions", kind="object", shape=_PACKAGE_DIMENSIONS.shape),
        Param(name="shippable", kind="boolean"),
        Param(name="statement_descriptor", kind="string", max_length=22),
        Param(name="tax_code", kind="string", max_length=5_000, unset_with_empty_string=True),
        Param(name="unit_label", kind="string", max_length=12, unset_with_empty_string=True),
        Param(name="url", kind="string", max_length=5_000, unset_with_empty_string=True),
    ),
    metadata=True,
)

PRODUCT_LIST = ParamSpec(op_id="GetProducts", paginated=True)

PRODUCT_RETRIEVE = ParamSpec(op_id="GetProductsId", path=("id",))

PRODUCT_DELETE = ParamSpec(op_id="DeleteProductsId", path=("id",), expand=False)

always_present, omit_when_none = presence_sets("product")

FIELDS = FieldMap(
    object="product",
    table="products",
    columns={
        "id": "id",
        "created": "created",
        "updated": "updated",
        "active": "active",
        "default_price": "default_price",
        "description": "description",
        "images": "images",
        "marketing_features": "marketing_features",
        "metadata": "metadata",
        "name": "name",
        "package_dimensions": "package_dimensions",
        "shippable": "shippable",
        "statement_descriptor": "statement_descriptor",
        "tax_code": "tax_code",
        "unit_label": "unit_label",
        "url": "url",
    },
    timestamps=frozenset({"created", "updated"}),
    json_columns=frozenset({"images", "marketing_features", "metadata", "package_dimensions"}),
    booleans=frozenset({"active", "shippable"}),
    constants={"livemode": False},
    always_present=always_present,
    omit_when_none=omit_when_none,
)

_serialize = serializer_for(FIELDS)


def _product_columns(ctx: seahaven.Ctx, params: dict[str, Any]) -> dict[str, Any]:
    """The INSERT column dict shared by the route handler and the inline
    product a price's `product_data` describes."""
    cols: dict[str, Any] = {
        "id": params.get("id") or _ids.stripe_id(ctx, "prod_"),
        "x_seq": _seq.next_seq(ctx, "products"),
        "created": ctx.clock.iso(),
        "updated": ctx.clock.iso(),
        "name": params["name"],
        "active": int(params.get("active", True)),
        "metadata": "{}",
    }
    for key in ("description", "statement_descriptor", "tax_code", "unit_label", "url"):
        if params.get(key) is not None:
            cols[key] = params[key]
    if params.get("shippable") is not None:
        cols["shippable"] = int(params["shippable"])
    if params.get("images") is not None:
        cols["images"] = _json.dumps(params["images"])
    if params.get("marketing_features") is not None:
        cols["marketing_features"] = _json.dumps(params["marketing_features"])
    if params.get("package_dimensions") is not None:
        cols["package_dimensions"] = _json.dumps(params["package_dimensions"])
    return cols


def _insert(ctx: seahaven.Ctx, cols: dict[str, Any]) -> dict[str, Any]:
    columns = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    ctx.db.execute(f"INSERT INTO products ({columns}) VALUES ({placeholders})", *cols.values())
    return _lookup.require_row(ctx, "products", "product", cols["id"], param="id")


def _emit_created(ctx: seahaven.Ctx, row: dict[str, Any]) -> None:
    events.emit_event(ctx, type="product.created", obj=_serialize(ctx, row))


def create(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/products` — hand-written for `default_price_data`, whose
    price row must exist before the product's `created` snapshot is taken
    (recorded: the snapshot carries `default_price` set). Write order:
    product with a NULL default_price, price, UPDATE — non-deferred FKs are
    satisfied at every step, exactly as `components/data_model.md` §3 drew
    it. Event order — `product.created` first, then the inline price's
    `price.created` — is this world's ruling, inferred from the probe's
    sequential event ids and asserted only in-world: the cassette's two
    type-filtered event lists share one `created` second, so they cannot
    show order, and nothing routed observes it until Phase 17."""
    cols = _product_columns(ctx, dict(req.params))
    if req.metadata is not None:
        # The engine's metadata handling, repeated here because this create
        # is hand-written: a merge onto the (freshly empty) map.
        cols["metadata"] = _json.dumps(dict(req.metadata.apply({})))
    row = _insert(ctx, cols)
    default_price_data = req.params.get("default_price_data")
    if default_price_data is not None:
        price_row = prices.create_inline(ctx, default_price_data, product_id=cols["id"])
        ctx.db.execute(
            "UPDATE products SET default_price = ? WHERE id = ?", price_row["id"], cols["id"]
        )
        row = _lookup.require_row(ctx, "products", "product", cols["id"], param="id")
        _emit_created(ctx, row)
        events.emit_event(ctx, type="price.created", obj=prices.SPEC.serializer(ctx, price_row))
    else:
        _emit_created(ctx, row)
    return _serialize(ctx, row)


def insert_inline(ctx: seahaven.Ctx, product_data: dict[str, Any]) -> str:
    """The product a price's `product_data` describes: same columns, same
    `product.created` event, no default_price surface."""
    cols = _product_columns(ctx, product_data)
    row = _insert(ctx, cols)
    _emit_created(ctx, row)
    return cols["id"]


def _before_update(ctx: seahaven.Ctx, req: Request, sets: dict[str, Any]) -> dict[str, Any]:
    """`updated` is stamped on every write; `default_price` must name a live
    price (a bare FK refusal would surface as an internal error, not
    Stripe's 400 — `architecture.md` §7)."""
    if "default_price" in sets and sets["default_price"] is not None:
        _lookup.require_live_row(
            ctx, "prices", "price", sets["default_price"], param="default_price"
        )
    sets["updated"] = ctx.clock.iso()
    return sets


def _delete_guard(ctx: seahaven.Ctx, row) -> None:
    """A product with attached prices refuses its delete (recorded in
    cassette 07: 400 `invalid_request_error`, the message verbatim, no
    `code`, no `param`)."""
    if ctx.db.one("SELECT 1 FROM prices WHERE product = ?", row["id"]) is not None:
        raise invalid_request(
            "This product cannot be deleted because it has one or more user-created prices.",
            pre_execution=True,
        )


SPEC = register(
    ResourceSpec(
        object="product",
        table="products",
        id_prefix="prod_",
        collection_url="/v1/products",
        serializer=_serialize,
        columns=tuple(FIELDS.columns),
        # Probed: a missing product path id names `param: "id"` (`No such
        # product: 'prod_nope'`), the customers spelling — unlike prices,
        # coupons, promotion codes and tax rates, which keep the placeholder.
        missing_path_param="id",
        list_filters=(
            ListFilter(name="active", column="active", kind="boolean"),
            ListFilter(name="created", column="created", kind="range"),
            ListFilter(name="ids", column="id", kind="in", id_prefixes=("prod_",)),
            ListFilter(name="shippable", column="shippable", kind="boolean"),
            ListFilter(name="url", column="url", kind="exact"),
        ),
        creatable=PRODUCT_CREATE,
        updatable=PRODUCT_UPDATE,
        # Probed (Phase 7): the soft delete also zeroes `active` (the
        # `product.deleted` snapshot carries `active: false`), and a later
        # retrieve of the tombstone is a 404, not the customers' stub.
        delete=DeleteSpec(
            mode="soft",
            zero_columns=("active",),
            deleted_retrieve="missing",
            guard=_delete_guard,
        ),
        metadata=True,
        before_update=_before_update,
        created_event="product.created",
        updated_event="product.updated",
        deleted_event="product.deleted",
    )
)
