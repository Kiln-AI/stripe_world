"""Subscription schedules (Phase 16): the scoped-down ``phases`` model.

A subscription schedule manages the lifecycle of a subscription by
predefining expected changes across phases.  This world models the
resource with a JSON ``phases`` array on the schedule row; each phase
carries items, start_date, end_date, proration_behavior,
collection_method, metadata, trial_end, default_tax_rates,
default_payment_method, description and discounts.

The schedule itself carries a five-value status machine:
``not_started`` -> ``active`` -> ``completed`` | ``canceled`` | ``released``
(and ``not_started`` -> ``canceled``).

Operations:
- ``POST /v1/subscription_schedules`` — create (hand-written)
- ``POST /v1/subscription_schedules/{schedule}`` — update (hand-written)
- ``POST /v1/subscription_schedules/{schedule}/cancel`` — cancel (hand-written)
- ``POST /v1/subscription_schedules/{schedule}/release`` — release (hand-written)
- ``GET /v1/subscription_schedules`` — list (engine-served)
- ``GET /v1/subscription_schedules/{schedule}`` — retrieve (engine-served)

Scope cuts (declared in the implementation plan and functional spec §3.2):
The ``phases`` array is scoped down — it models the fields the lifecycle
needs rather than full parity with the subscription schema. Connect
fields (``on_behalf_of``, ``transfer_data``, ``application_fee_percent``),
``automatic_tax``, ``add_invoice_items``, ``invoice_settings`` at the
phase level, and item-level ``discounts``/``price_data``/``billing_thresholds``
are omitted.
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import seahaven

from seahaven_stripe_world import _ids, _json, _seq, _time
from seahaven_stripe_world.billing import subscription_lifecycle
from seahaven_stripe_world.dispatch.params import Param, ParamSpec
from seahaven_stripe_world.dispatch.resource import (
    ListFilter,
    ResourceSpec,
    register,
)
from seahaven_stripe_world.resources import _lookup, events
from seahaven_stripe_world.serialize.fields import FieldMap, presence_sets, serializer_for
from seahaven_stripe_world.stripe_errors import invalid_request

if TYPE_CHECKING:
    from seahaven_stripe_world.dispatch.response import Request

__all__ = [
    "FIELDS",
    "SCHEDULE_CANCEL",
    "SCHEDULE_CREATE",
    "SCHEDULE_LIST",
    "SCHEDULE_RELEASE",
    "SCHEDULE_RETRIEVE",
    "SCHEDULE_UPDATE",
    "SPEC",
    "cancel",
    "create",
    "release",
    "serialize",
    "update",
]

CUS = ("cus_",)
PM = ("pm_",)
PRICE = ("price_",)
TXR = ("txr_",)
SUB = ("sub_",)
SCHED = ("sub_sched_",)

# --- Phase item shape --------------------------------------------------------

_PHASE_ITEM = Param(
    name="",
    kind="object",
    shape=(
        Param(name="price", kind="id", id_prefixes=PRICE, required=True),
        Param(name="quantity", kind="integer"),
        Param(
            name="tax_rates",
            kind="array",
            item=Param(name="", kind="id", id_prefixes=TXR),
        ),
    ),
)

_PHASE_SHAPE = Param(
    name="",
    kind="object",
    shape=(
        Param(
            name="collection_method",
            kind="literal",
            choices=("charge_automatically", "send_invoice"),
        ),
        Param(name="default_payment_method", kind="id", id_prefixes=PM),
        Param(
            name="default_tax_rates",
            kind="array",
            item=Param(name="", kind="id", id_prefixes=TXR),
        ),
        Param(name="description", kind="string", max_length=5_000),
        Param(name="end_date", kind="timestamp"),
        Param(name="items", kind="array", item=_PHASE_ITEM, required=True),
        Param(name="iterations", kind="integer"),
        Param(
            name="metadata",
            kind="object",
            shape=(),
        ),
        Param(
            name="proration_behavior",
            kind="literal",
            choices=("create_prorations", "always_invoice", "none"),
        ),
        Param(name="start_date", kind="timestamp"),
        Param(name="trial_end", kind="timestamp"),
    ),
)

_DEFAULT_SETTINGS_SHAPE = Param(
    name="default_settings",
    kind="object",
    shape=(
        Param(
            name="billing_cycle_anchor",
            kind="literal",
            choices=("automatic", "phase_start"),
        ),
        Param(
            name="collection_method",
            kind="literal",
            choices=("charge_automatically", "send_invoice"),
        ),
        Param(name="default_payment_method", kind="id", id_prefixes=PM),
        Param(name="description", kind="string", max_length=5_000),
    ),
)

# --- ParamSpecs --------------------------------------------------------------

SCHEDULE_CREATE = ParamSpec(
    op_id="PostSubscriptionSchedules",
    body=(
        Param(name="customer", kind="id", id_prefixes=CUS, required=True),
        _DEFAULT_SETTINGS_SHAPE,
        Param(
            name="end_behavior",
            kind="literal",
            choices=("cancel", "none", "release", "renew"),
        ),
        Param(name="from_subscription", kind="id", id_prefixes=SUB),
        Param(name="phases", kind="array", item=_PHASE_SHAPE),
        Param(name="start_date", kind="timestamp"),
    ),
    metadata=True,
)

SCHEDULE_UPDATE = ParamSpec(
    op_id="PostSubscriptionSchedulesSchedule",
    path=("schedule",),
    body=(
        _DEFAULT_SETTINGS_SHAPE,
        Param(
            name="end_behavior",
            kind="literal",
            choices=("cancel", "none", "release", "renew"),
        ),
        Param(name="phases", kind="array", item=_PHASE_SHAPE),
        Param(
            name="proration_behavior",
            kind="literal",
            choices=("create_prorations", "always_invoice", "none"),
        ),
    ),
    metadata=True,
)

SCHEDULE_CANCEL = ParamSpec(
    op_id="PostSubscriptionSchedulesScheduleCancel",
    path=("schedule",),
    body=(
        Param(name="invoice_now", kind="boolean"),
        Param(name="prorate", kind="boolean"),
    ),
)

SCHEDULE_RELEASE = ParamSpec(
    op_id="PostSubscriptionSchedulesScheduleRelease",
    path=("schedule",),
    body=(Param(name="preserve_cancel_date", kind="boolean"),),
)

SCHEDULE_LIST = ParamSpec(
    op_id="GetSubscriptionSchedules",
    paginated=True,
    body=(
        Param(name="canceled_at", kind="range"),
        Param(name="completed_at", kind="range"),
        Param(name="created", kind="range"),
        Param(name="customer", kind="id", id_prefixes=CUS),
        Param(name="released_at", kind="range"),
    ),
)

SCHEDULE_RETRIEVE = ParamSpec(
    op_id="GetSubscriptionSchedulesSchedule",
    path=("schedule",),
)

# --- serializer ---------------------------------------------------------------

always_present, omit_when_none = presence_sets("subscription_schedule")

_DEFAULT_INVOICE_SETTINGS: dict[str, Any] = {
    "account_tax_ids": None,
    "custom_fields": None,
    "days_until_due": None,
    "description": None,
    "footer": None,
    "issuer": {"type": "self"},
}

_DEFAULT_AUTOMATIC_TAX: dict[str, Any] = {
    "disabled_reason": None,
    "enabled": False,
    "liability": None,
}


def _billing_mode(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": row["billing_mode"],
        "flexible": None,
        "updated_at": _time.to_unix(row["created"]),
    }


def _default_settings_body(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
    """Serialize the ``default_settings`` column into the wire shape."""
    stored: object = _json.loads(row["default_settings"])
    settings = stored if isinstance(stored, dict) else {}
    return {
        "application_fee_percent": None,
        "automatic_tax": _DEFAULT_AUTOMATIC_TAX,
        "billing_cycle_anchor": settings.get("billing_cycle_anchor", "automatic"),
        "billing_thresholds": None,
        "collection_method": settings.get("collection_method"),
        "default_payment_method": settings.get("default_payment_method"),
        "description": settings.get("description"),
        "invoice_settings": _DEFAULT_INVOICE_SETTINGS,
        "on_behalf_of": None,
        "transfer_data": None,
    }


def _phases_body(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Serialize the ``phases`` column into the wire shape."""
    from seahaven_stripe_world.resources import prices as prices_mod

    stored: object = _json.loads(row["phases"])
    phases = stored if isinstance(stored, list) else []
    result: list[dict[str, Any]] = []
    for phase in phases:
        if not isinstance(phase, dict):
            continue
        # Serialize items: the price is always the full object (embedded ref)
        items_raw = phase.get("items", [])
        items_out: list[dict[str, Any]] = []
        for item in items_raw if isinstance(items_raw, list) else []:
            if not isinstance(item, dict):
                continue
            price_id = item.get("price")
            price_body: dict[str, Any] | str | None = price_id
            if isinstance(price_id, str):
                price_row = ctx.db.one("SELECT * FROM prices WHERE id = ?", price_id)
                if price_row is not None:
                    price_body = prices_mod._serialize(ctx, price_row)
            # Serialize tax_rates: array<ref:tax_rate> -> always full objects
            tax_rate_ids = item.get("tax_rates", [])
            tax_rate_bodies: list[dict[str, Any]] = []
            if isinstance(tax_rate_ids, list):
                from seahaven_stripe_world.resources import tax_rates as tax_rates_mod

                for tr_id in tax_rate_ids:
                    tr_row = ctx.db.one("SELECT * FROM tax_rates WHERE id = ?", str(tr_id))
                    if tr_row is not None:
                        tax_rate_bodies.append(tax_rates_mod._serialize(ctx, tr_row))
            items_out.append(
                {
                    "billing_thresholds": None,
                    "discounts": phase.get("discounts", []),
                    "metadata": item.get("metadata") or {},
                    "price": price_body,
                    "quantity": item.get("quantity", 1),
                    "tax_rates": tax_rate_bodies,
                }
            )
        # Serialize default_tax_rates: always full objects
        dtrs = phase.get("default_tax_rates", [])
        dtr_bodies: list[dict[str, Any]] = []
        if isinstance(dtrs, list):
            from seahaven_stripe_world.resources import tax_rates as tax_rates_mod

            for tr_id in dtrs:
                tr_row = ctx.db.one("SELECT * FROM tax_rates WHERE id = ?", str(tr_id))
                if tr_row is not None:
                    dtr_bodies.append(tax_rates_mod._serialize(ctx, tr_row))
        result.append(
            {
                "add_invoice_items": [],
                "application_fee_percent": None,
                "automatic_tax": _DEFAULT_AUTOMATIC_TAX,
                "billing_cycle_anchor": phase.get("billing_cycle_anchor"),
                "billing_thresholds": None,
                "collection_method": phase.get("collection_method"),
                "currency": phase.get("currency", "usd"),
                "default_payment_method": phase.get("default_payment_method"),
                "default_tax_rates": dtr_bodies if dtr_bodies else None,
                "description": phase.get("description"),
                "discounts": phase.get("discounts", []),
                "end_date": phase["end_date"],
                "invoice_settings": None,
                "items": items_out,
                "metadata": phase.get("metadata") or {},
                "on_behalf_of": None,
                "proration_behavior": phase.get("proration_behavior", "create_prorations"),
                "start_date": phase["start_date"],
                "transfer_data": None,
                "trial": False,
                "trial_end": phase.get("trial_end"),
            }
        )
    return result


FIELDS = FieldMap(
    object="subscription_schedule",
    table="subscription_schedules",
    columns={
        "id": "id",
        "created": "created",
        "canceled_at": "canceled_at",
        "completed_at": "completed_at",
        "current_phase": "current_phase",
        "customer": "customer",
        "end_behavior": "end_behavior",
        "metadata": "metadata",
        "released_at": "released_at",
        "released_subscription": "released_subscription",
        "status": "status",
        "subscription": "subscription",
    },
    timestamps=frozenset({"created", "canceled_at", "completed_at", "released_at"}),
    json_columns=frozenset({"current_phase", "metadata"}),
    derived={
        "billing_mode": _billing_mode,
        "default_settings": _default_settings_body,
        "phases": _phases_body,
    },
    constants={
        "application": None,
        "customer_account": None,
        "test_clock": None,
    },
    always_present=always_present,
    omit_when_none=omit_when_none,
)

_serialize = serializer_for(FIELDS)


def serialize(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
    return _serialize(ctx, row)


SPEC = register(
    ResourceSpec(
        object="subscription_schedule",
        table="subscription_schedules",
        id_prefix="sub_sched_",
        collection_url="/v1/subscription_schedules",
        serializer=serialize,
        columns=tuple(FIELDS.columns),
        missing_path_param="schedule",
        error_name="subscription_schedule",
        list_filters=(
            ListFilter(name="canceled_at", column="canceled_at", kind="range"),
            ListFilter(name="completed_at", column="completed_at", kind="range"),
            ListFilter(name="created", column="created", kind="range"),
            ListFilter(name="customer", column="customer", kind="exact", id_prefixes=CUS),
            ListFilter(name="released_at", column="released_at", kind="range"),
        ),
        metadata=True,
    )
)

# --- helpers ----------------------------------------------------------------


def _load_dict(text: str | None) -> dict[str, Any]:
    loaded: object = _json.loads(text)
    return loaded if isinstance(loaded, dict) else {}


def _load_list(text: str | None) -> list[Any]:
    loaded: object = _json.loads(text)
    return loaded if isinstance(loaded, list) else []


def _require_schedule(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    return _lookup.require_row(
        ctx,
        "subscription_schedules",
        "subscription_schedule",
        req.path_params["schedule"],
        param="schedule",
    )


def _insert_row(ctx: seahaven.Ctx, table: str, cols: dict[str, Any]) -> None:
    columns = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    ctx.db.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", *cols.values())


def _build_phases(
    ctx: seahaven.Ctx,
    phases_param: list[dict[str, Any]],
    *,
    now: str,
    start_date: str | None = None,
    currency: str | None = None,
) -> list[dict[str, Any]]:
    """Validate and build the internal phases array from the parameter.

    Each phase carries: items (with price ids), start_date, end_date,
    proration_behavior, collection_method, metadata, default_tax_rates,
    default_payment_method, description, trial_end, discounts, currency.
    Timestamps inside the stored phases are Unix seconds (data_model
    section 3.10).
    """
    built: list[dict[str, Any]] = []
    prev_end: str | None = start_date or now
    for index, phase in enumerate(phases_param):
        # Resolve items
        items_param = phase.get("items", [])
        if not items_param:
            raise invalid_request(
                f"The phases[{index}][items] parameter is required.",
                param=f"phases[{index}][items]",
            )
        resolved_items: list[dict[str, Any]] = []
        phase_currency = currency
        for item_index, item in enumerate(items_param):
            price_row = _lookup.require_live_row(
                ctx,
                "prices",
                "price",
                item["price"],
                param=f"phases[{index}][items][{item_index}][price]",
            )
            if phase_currency is None:
                phase_currency = price_row["currency"]
            elif price_row["currency"] != phase_currency:
                raise invalid_request(
                    f"All prices in a phase must use the same currency. "
                    f"Expected `{phase_currency}` but got `{price_row['currency']}`.",
                    param=f"phases[{index}][items][{item_index}][price]",
                )
            resolved_items.append(
                {
                    "price": price_row["id"],
                    "quantity": item.get("quantity", 1),
                    "tax_rates": list(item.get("tax_rates", [])),
                    "metadata": item.get("metadata") or {},
                }
            )
        # Resolve tax rates
        dtr_ids = list(phase.get("default_tax_rates", []))
        for tr_index, tr_id in enumerate(dtr_ids):
            _lookup.require_live_row(
                ctx,
                "tax_rates",
                "tax_rate",
                str(tr_id),
                param=f"phases[{index}][default_tax_rates][{tr_index}]",
            )
        if phase.get("default_payment_method") is not None:
            _lookup.require_live_row(
                ctx,
                "payment_methods",
                "PaymentMethod",
                phase["default_payment_method"],
                param=f"phases[{index}][default_payment_method]",
            )
        from seahaven_stripe_world.billing.invoicing import add_interval

        phase_start_unix = _time.to_unix(phase.get("start_date") or prev_end or now)
        if phase.get("end_date") is not None:
            phase_end_unix = _time.to_unix(phase["end_date"])
        elif phase.get("iterations") is not None:
            # Compute end from iterations x interval
            first_price = _lookup.require_row(
                ctx,
                "prices",
                "price",
                resolved_items[0]["price"],
                param=f"phases[{index}][items][0][price]",
            )
            recurring = _load_dict(first_price["recurring"])
            interval = recurring.get("interval", "month")
            interval_count = int(recurring.get("interval_count", 1))
            accumulated = _time.from_unix(phase_start_unix)
            for _ in range(int(phase["iterations"])):
                accumulated = add_interval(
                    accumulated, interval=interval, interval_count=interval_count
                )
            phase_end_unix = _time.to_unix(accumulated)
        else:
            # Default: one interval from start
            first_price = _lookup.require_row(
                ctx,
                "prices",
                "price",
                resolved_items[0]["price"],
                param=f"phases[{index}][items][0][price]",
            )
            recurring = _load_dict(first_price["recurring"])
            interval = recurring.get("interval", "month")
            interval_count = int(recurring.get("interval_count", 1))
            end_iso = add_interval(
                _time.from_unix(phase_start_unix),
                interval=interval,
                interval_count=interval_count,
            )
            phase_end_unix = _time.to_unix(end_iso)

        built.append(
            {
                "start_date": phase_start_unix,
                "end_date": phase_end_unix,
                "items": resolved_items,
                "proration_behavior": phase.get("proration_behavior", "create_prorations"),
                "collection_method": phase.get("collection_method"),
                "default_payment_method": phase.get("default_payment_method"),
                "default_tax_rates": dtr_ids,
                "description": phase.get("description"),
                "trial_end": (
                    _time.to_unix(phase["trial_end"])
                    if phase.get("trial_end") is not None
                    else None
                ),
                "metadata": phase.get("metadata") or {},
                "discounts": [],
                "currency": phase_currency or "usd",
            }
        )
        prev_end = _time.from_unix(phase_end_unix)
    return built


def _current_phase_for(phases: list[dict[str, Any]], now_unix: int) -> dict[str, Any] | None:
    """Find the current phase based on the instant."""
    for phase in phases:
        if phase["start_date"] <= now_unix < phase["end_date"]:
            return {"start_date": phase["start_date"], "end_date": phase["end_date"]}
    return None


# --- handlers ----------------------------------------------------------------


def create(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/subscription_schedules`: create a new schedule."""
    params = dict(req.params)
    now = ctx.clock.iso()
    now_unix = _time.to_unix(now)

    customer_id = params["customer"]
    _lookup.require_live_row(ctx, "customers", "customer", customer_id, param="customer")

    from_sub = params.get("from_subscription")
    metadata = (
        dict(req.metadata.apply({})) if req.metadata is not None else params.get("metadata", {})
    )

    sched_id = _ids.stripe_id(ctx, "sub_sched_")
    end_behavior = params.get("end_behavior", "release")

    default_settings = params.get("default_settings") or {}
    if isinstance(default_settings, str):
        default_settings = _load_dict(default_settings)
    # Validate default_payment_method if provided
    if default_settings.get("default_payment_method") is not None:
        _lookup.require_live_row(
            ctx,
            "payment_methods",
            "PaymentMethod",
            default_settings["default_payment_method"],
            param="default_settings[default_payment_method]",
        )
    stored_settings = {
        "billing_cycle_anchor": default_settings.get("billing_cycle_anchor", "automatic"),
        "collection_method": default_settings.get("collection_method"),
        "default_payment_method": default_settings.get("default_payment_method"),
        "description": default_settings.get("description"),
    }

    subscription_id: str | None = None
    status = "not_started"
    built_phases: list[dict[str, Any]] = []
    current_phase: dict[str, Any] | None = None

    if from_sub is not None:
        # Attach to an existing subscription
        sub_row = _lookup.require_row(
            ctx, "subscriptions", "subscription", from_sub, param="from_subscription"
        )
        if sub_row["status"] in subscription_lifecycle.TERMINAL_STATUSES:
            raise invalid_request(
                f"The subscription `{from_sub}` cannot be scheduled because it has already ended.",
                param="from_subscription",
            )
        if sub_row["schedule"] is not None:
            raise invalid_request(
                f"The subscription `{from_sub}` is already associated with a "
                f"subscription schedule `{sub_row['schedule']}`.",
                param="from_subscription",
            )
        customer_id = sub_row["customer"]
        subscription_id = sub_row["id"]
        status = "active"
        # Build a single phase from the current subscription state
        items = ctx.db.rows(
            "SELECT * FROM subscription_items WHERE subscription = ? ORDER BY x_seq ASC",
            sub_row["id"],
        )
        phase_items = [
            {
                "price": item["price"],
                "quantity": item["quantity"] if item["quantity"] is not None else 1,
                "tax_rates": _load_list(item["tax_rates"]),
                "metadata": _load_dict(item["metadata"]),
            }
            for item in items
        ]
        # Use the subscription's period for the phase window. Since the
        # period lives on the items (not the subscription at this API
        # version), derive it the same way the lifecycle module does.
        period_start = min((item["current_period_start"] for item in items), default=now)
        period_end = min((item["current_period_end"] for item in items), default=now)
        built_phases = [
            {
                "start_date": _time.to_unix(period_start),
                "end_date": _time.to_unix(period_end),
                "items": phase_items,
                "proration_behavior": "create_prorations",
                "collection_method": sub_row["collection_method"],
                "default_payment_method": sub_row["default_payment_method"],
                "default_tax_rates": _load_list(sub_row["default_tax_rates"]),
                "description": sub_row["description"],
                "trial_end": (
                    _time.to_unix(sub_row["trial_end"])
                    if sub_row["trial_end"] is not None
                    else None
                ),
                "metadata": _load_dict(sub_row["metadata"]),
                "discounts": [],
                "currency": sub_row["currency"],
            }
        ]
        current_phase = _current_phase_for(built_phases, now_unix)
    elif params.get("phases"):
        # Build phases from the parameter
        start_date = params.get("start_date") or now
        built_phases = _build_phases(ctx, params["phases"], now=now, start_date=start_date)
        if built_phases:
            first_start = built_phases[0]["start_date"]
            if first_start <= now_unix:
                # The first phase starts now or in the past: create the
                # subscription immediately and become active.
                status = "active"
                subscription_id = _create_subscription_from_phase(ctx, customer_id, built_phases[0])
                current_phase = _current_phase_for(built_phases, now_unix)
            else:
                status = "not_started"

    cols: dict[str, Any] = {
        "id": sched_id,
        "x_seq": _seq.next_seq(ctx, "subscription_schedules"),
        "created": now,
        "billing_mode": "classic",
        "customer": customer_id,
        "default_settings": _json.dumps(stored_settings),
        "end_behavior": end_behavior,
        "metadata": _json.dumps(metadata or {}),
        "phases": _json.dumps(built_phases),
        "status": status,
        "subscription": subscription_id,
        "current_phase": _json.dumps(current_phase) if current_phase else None,
    }
    _insert_row(ctx, "subscription_schedules", cols)

    # Back-link: now that the schedule row exists, set subscriptions.schedule.
    # This must happen after the INSERT to satisfy the FK constraint
    # (subscriptions.schedule REFERENCES subscription_schedules).
    if subscription_id is not None:
        ctx.db.execute(
            "UPDATE subscriptions SET schedule = ? WHERE id = ?",
            sched_id,
            subscription_id,
        )

    body = serialize(ctx, _re_read(ctx, sched_id))
    events.emit_event(ctx, type="subscription_schedule.created", obj=body)
    return body


def update(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/subscription_schedules/{schedule}`: update."""
    row = _require_schedule(ctx, req)
    if row["status"] in ("canceled", "completed", "released"):
        raise invalid_request(
            f"You cannot update a subscription schedule with status `{row['status']}`.",
            param="schedule",
        )

    now = ctx.clock.iso()
    now_unix = _time.to_unix(now)
    sets: dict[str, Any] = {}

    if req.metadata is not None:
        current_meta = _load_dict(row["metadata"])
        sets["metadata"] = _json.dumps(dict(req.metadata.apply(current_meta)))

    params = dict(req.params)
    if "end_behavior" in params:
        sets["end_behavior"] = params["end_behavior"]

    if "default_settings" in params:
        ds = params["default_settings"]
        if isinstance(ds, str):
            ds = _load_dict(ds)
        current_ds = _load_dict(row["default_settings"])
        if ds.get("default_payment_method") is not None:
            _lookup.require_live_row(
                ctx,
                "payment_methods",
                "PaymentMethod",
                ds["default_payment_method"],
                param="default_settings[default_payment_method]",
            )
        merged = {**current_ds, **{k: v for k, v in ds.items() if v is not None}}
        sets["default_settings"] = _json.dumps(merged)

    if "phases" in params:
        existing_phases = _load_list(row["phases"])
        # Determine the currency from the existing subscription or phases
        currency: str | None = None
        if row["subscription"] is not None:
            sub = ctx.db.one("SELECT currency FROM subscriptions WHERE id = ?", row["subscription"])
            if sub is not None:
                currency = sub["currency"]
        if currency is None and existing_phases:
            currency = existing_phases[0].get("currency")
        built = _build_phases(ctx, params["phases"], now=now, currency=currency)
        sets["phases"] = _json.dumps(built)
        # Recompute current_phase
        cp = _current_phase_for(built, now_unix)
        sets["current_phase"] = _json.dumps(cp) if cp else None
        # If the schedule is not_started and the first phase starts now,
        # create the subscription and go active.
        if row["status"] == "not_started" and built and built[0]["start_date"] <= now_unix:
            subscription_id = _create_subscription_from_phase(ctx, row["customer"], built[0])
            sets["subscription"] = subscription_id
            sets["status"] = "active"
            # Back-link the subscription to this schedule
            ctx.db.execute(
                "UPDATE subscriptions SET schedule = ? WHERE id = ?",
                row["id"],
                subscription_id,
            )
        elif row["status"] == "active" and row["subscription"]:
            # Apply the current phase's configuration to the subscription
            if cp is not None:
                for phase in built:
                    if phase["start_date"] == cp["start_date"]:
                        _apply_phase_to_subscription(ctx, row["subscription"], phase)
                        break

    if sets:
        assignments = ", ".join(f"{column} = ?" for column in sets)
        ctx.db.execute(
            f"UPDATE subscription_schedules SET {assignments} WHERE id = ?",
            *sets.values(),
            row["id"],
        )

    body = serialize(ctx, _re_read(ctx, row["id"]))
    events.emit_event(ctx, type="subscription_schedule.updated", obj=body)
    return body


def cancel(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/subscription_schedules/{schedule}/cancel`."""
    row = _require_schedule(ctx, req)
    if row["status"] in ("canceled", "completed", "released"):
        raise invalid_request(
            f"You cannot cancel a subscription schedule with status `{row['status']}`.",
            param="schedule",
        )
    now = ctx.clock.iso()

    invoice_now = req.params.get("invoice_now") is True
    prorate = req.params.get("prorate") is True

    # If the schedule has an active subscription, cancel it too.
    if row["subscription"] is not None:
        sub = ctx.db.one("SELECT * FROM subscriptions WHERE id = ?", row["subscription"])
        if sub is not None and sub["status"] not in subscription_lifecycle.TERMINAL_STATUSES:
            subscription_lifecycle.cancel_subscription(
                ctx,
                sub["id"],
                prorate=prorate,
                invoice_now=invoice_now,
            )
        # Unlink
        ctx.db.execute(
            "UPDATE subscriptions SET schedule = NULL WHERE id = ?",
            row["subscription"],
        )

    ctx.db.execute(
        "UPDATE subscription_schedules SET status = 'canceled', canceled_at = ?,"
        " current_phase = NULL WHERE id = ?",
        now,
        row["id"],
    )

    body = serialize(ctx, _re_read(ctx, row["id"]))
    events.emit_event(ctx, type="subscription_schedule.canceled", obj=body)
    return body


def release(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/subscription_schedules/{schedule}/release`."""
    row = _require_schedule(ctx, req)
    if row["status"] != "active":
        raise invalid_request(
            "You can only release a subscription schedule with status `active`.",
            param="schedule",
        )
    now = ctx.clock.iso()

    preserve_cancel = req.params.get("preserve_cancel_date") is True

    released_sub: str | None = row["subscription"]

    # Unlink the subscription from the schedule
    if released_sub is not None:
        update_sql = "UPDATE subscriptions SET schedule = NULL"
        if not preserve_cancel:
            update_sql += ", cancel_at = NULL, cancel_at_period_end = 0"
        update_sql += " WHERE id = ?"
        ctx.db.execute(update_sql, released_sub)

    ctx.db.execute(
        "UPDATE subscription_schedules SET status = 'released', released_at = ?,"
        " released_subscription = ?, subscription = NULL, current_phase = NULL"
        " WHERE id = ?",
        now,
        released_sub,
        row["id"],
    )

    body = serialize(ctx, _re_read(ctx, row["id"]))
    events.emit_event(ctx, type="subscription_schedule.released", obj=body)
    return body


def _re_read(ctx: seahaven.Ctx, id_: str) -> dict[str, Any]:
    return _lookup.require_row(
        ctx, "subscription_schedules", "subscription_schedule", id_, param="schedule"
    )


def _create_subscription_from_phase(
    ctx: seahaven.Ctx,
    customer_id: str,
    phase: dict[str, Any],
) -> str:
    """Create a subscription from a schedule phase."""
    items_param = [
        {"price": item["price"], "quantity": item.get("quantity", 1)}
        for item in phase.get("items", [])
    ]
    sub_params: dict[str, Any] = {
        "customer": customer_id,
        "items": items_param,
        "metadata": {},
    }
    if phase.get("collection_method") is not None:
        sub_params["collection_method"] = phase["collection_method"]
    if phase.get("default_payment_method") is not None:
        sub_params["default_payment_method"] = phase["default_payment_method"]
    if phase.get("default_tax_rates"):
        sub_params["default_tax_rates"] = phase["default_tax_rates"]
    if phase.get("description") is not None:
        sub_params["description"] = phase["description"]
    body = subscription_lifecycle.create_subscription(ctx, sub_params)
    return body["id"]


def _apply_phase_to_subscription(
    ctx: seahaven.Ctx,
    sub_id: str,
    phase: dict[str, Any],
) -> None:
    """Apply a phase's configuration to the linked subscription."""
    update_params: dict[str, Any] = {}
    if phase.get("collection_method") is not None:
        update_params["collection_method"] = phase["collection_method"]
    if phase.get("default_payment_method") is not None:
        update_params["default_payment_method"] = phase["default_payment_method"]
    if phase.get("description") is not None:
        update_params["description"] = phase["description"]
    if update_params:
        subscription_lifecycle.apply_update(ctx, sub_id, update_params)
