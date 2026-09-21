"""The eight-status machine: creation branches, trial end, pause/resume,
cancel, recovery, and the unrouted boundary walkers
(`components/billing_engine.md` §1, Phase 12's reachable surface).

Every refusal spelling here is a live probe at `2026-08-26.dahlia`
(2026-09-20/21) except where a comment says otherwise. Where a recording
corrected the design doc — send-invoice creation leaves the invoice
**draft**, resume *parks* a pending update behind a SetupIntent rather than
activating, `trial_end=now` without a payment method refuses rather than
going `past_due`, and a trial-end cancel stamps reason
`cancellation_requested` — the recording wins and the doc is corrected in
the same phase.

Proration side effects are Phase 14's (the behavior spanning this phase and
Phase 13): item create/update/delete and `proration_behavior` parameters
are accepted and validated, and no proration lines are generated yet — a
declared gap, closed by the proration phase.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Any

import seahaven

from stripeapi import _ids, _json, _seq, _time
from stripeapi.billing import invoicing
from stripeapi.billing._money import round_half_up
from stripeapi.billing.invoicing import DiscountSpec, TaxRateSpec
from stripeapi.resources import _lookup, events, payment_intents
from stripeapi.stripe_errors import StripeApiError, invalid_request, resource_missing

__all__ = [
    "TERMINAL_STATUSES",
    "TRANSITIONS",
    "Guard",
    "Transition",
    "advance_cycle",
    "apply_pending_update_on_seti_success",
    "apply_update",
    "cancel_subscription",
    "create_subscription",
    "expire_incomplete",
    "on_invoice_paid",
    "resume_subscription",
    "transition",
    "trial_end_now",
]

TERMINAL_STATUSES = ("canceled", "incomplete_expired")

#: The guard column of `TRANSITIONS`: the §1 table's predicate, named —
#: evaluated at the call site, never here (the lookup is pure data).
Guard = str


@dataclass(frozen=True)
class Transition:
    """One row of the §1 state machine: the source status, the trigger, the
    guard's name, and the target. The side effects and events live in the
    transition functions themselves; this table is the map they answer to."""

    source: str
    trigger: str
    guard: Guard
    target: str


#: `billing_engine.md` §1 as data. The source `"(new)"` marks a creation
#: row (§1's creation table). The dunning-driven rows out of `past_due`
#: (`retry_failed`, `schedule_exhausted`) are declared ahead of their
#: walkers — Phase 14's dunning module drives them; §1 is the authority for
#: the whole table, not only the implemented slice.
TRANSITIONS: frozenset[Transition] = frozenset(
    {
        # creation (§1 "Creation")
        Transition("(new)", "create", "trial", "trialing"),
        Transition("(new)", "create", "first_invoice_total_le_0", "active"),
        Transition("(new)", "create", "send_invoice", "active"),
        Transition("(new)", "create", "charge_succeeds", "active"),
        Transition("(new)", "create", "allow_incomplete+charge_fails", "incomplete"),
        Transition(
            "(new)", "create", "default_incomplete+payment_required_unconfirmed", "incomplete"
        ),
        # out of incomplete
        Transition("incomplete", "invoice_paid", "latest_invoice_paid", "active"),
        Transition("incomplete", "incomplete_expiry", "23h_elapsed", "incomplete_expired"),
        # out of trialing
        Transition("trialing", "trial_end", "payment_method_present", "active"),
        # §1's "active (or past_due if the post-trial charge fails)"
        Transition("trialing", "trial_end", "payment_method_present+charge_fails", "past_due"),
        Transition("trialing", "trial_end", "missing_payment_method=pause", "paused"),
        Transition("trialing", "trial_end", "missing_payment_method=cancel", "canceled"),
        Transition("trialing", "trial_will_end", "fixture_only", "trialing"),
        # out of paused
        Transition("paused", "resume_confirmed", "parked_update", "active"),
        # out of active
        Transition("active", "cycle_boundary", "", "active"),
        Transition("active", "cycle_boundary", "cancel_at_period_end", "canceled"),
        # §1's status-preserving update rows
        Transition("active", "update", "proration_behavior=create_prorations", "active"),
        Transition("active", "update", "proration_behavior=always_invoice", "active"),
        Transition("active", "update", "proration_behavior=none", "active"),
        Transition("active", "update", "billing_cycle_anchor=now", "active"),
        Transition("active", "update", "cancel_at_period_end", "active"),
        # The conversion edge (probed, Phase 12 CR round 3/4's trail; §1's
        # correction below)
        Transition("active", "update", "trial_end_future", "trialing"),
        Transition("active", "cycle_invoice_failed", "charge_automatically", "past_due"),
        Transition("active", "due_date_passed", "send_invoice", "past_due"),
        Transition("active", "cancel", "immediate", "canceled"),
        # out of past_due (dunning's walkers: Phase 14)
        Transition("past_due", "invoice_paid", "latest_invoice_paid", "active"),
        Transition("past_due", "retry_failed", "attempt_count_lt_max", "past_due"),
        Transition("past_due", "schedule_exhausted", "end_behavior=cancel", "canceled"),
        Transition("past_due", "schedule_exhausted", "end_behavior=mark_unpaid", "unpaid"),
        Transition("past_due", "schedule_exhausted", "end_behavior=leave_past_due", "past_due"),
        # out of unpaid
        Transition("unpaid", "invoice_paid", "latest_invoice_paid", "active"),
        Transition("unpaid", "cycle_boundary", "", "unpaid"),
    }
)


def transition(state: str, trigger: str, guard: Guard) -> Transition | None:
    """The transition table as a pure lookup (`billing_engine.md` §1 is this
    function's data). None when no such row exists — including anything out
    of a terminal state, which the terminal-edge test asserts row by row."""
    for row in TRANSITIONS:
        if (row.source, row.trigger, row.guard) == (state, trigger, guard):
            return row
    return None


#: Recorded verbatim (Phase 12 probe): the no-payment-method refusal that
#: gates charge-automatically creation and the default trial-end path. 400,
#: `resource_missing`, no `param`.
_NO_PAYMENT_METHOD = (
    "This customer has no attached payment source or default payment method. "
    "Please consider adding a default payment method. For more information, visit "
    "https://stripe.com/docs/billing/subscriptions/payment-methods-setting"
    "#payment-method-priority."
)

_CANCELED_FIELDS = "A canceled subscription can only update its cancellation_details and metadata."

_TRIAL_FROM_PLAN_CONFLICT = (
    "You cannot set `trial_end` or `trial_period_days` when `trial_from_plan=true`."
)

_DEFAULT_TRIAL_SETTINGS: dict[str, Any] = {
    "end_behavior": {"missing_payment_method": "create_invoice"}
}

_DEFAULT_INVOICE_SETTINGS: dict[str, Any] = {
    "account_tax_ids": None,
    "custom_fields": None,
    "description": None,
    "footer": None,
    "issuer": {"type": "self"},
}


def _load_dict(text: str | None) -> dict[str, Any]:
    loaded: object = _json.loads(text)
    return loaded if isinstance(loaded, dict) else {}


def _load_list(text: str | None) -> list[Any]:
    loaded: object = _json.loads(text)
    return loaded if isinstance(loaded, list) else []


_DEFAULT_PAYMENT_SETTINGS: dict[str, Any] = {
    "payment_method_options": None,
    "payment_method_types": None,
    "save_default_payment_method": "off",
}


def _re_read(ctx: seahaven.Ctx, id_: str) -> dict[str, Any]:
    return _lookup.require_row(ctx, "subscriptions", "subscription", id_, param="id")


def _serialize(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
    # Function-level: resources.subscriptions imports this module for its
    # handlers, so the serializer can only be reached from inside a call.
    from stripeapi.resources import subscriptions

    return subscriptions.serialize(ctx, row)


def _items_of(ctx: seahaven.Ctx, sub_id: str) -> list[dict[str, Any]]:
    return ctx.db.rows(
        "SELECT * FROM subscription_items WHERE subscription = ? ORDER BY x_seq ASC", sub_id
    )


def _period_start(ctx: seahaven.Ctx, sub_id: str) -> str | None:
    """The earliest item period start, or None on the spec-blessed itemless
    subscription (`spec3.json` on the item delete: "Removing a subscription
    item from a subscription will not cancel the subscription") — routed
    callers must answer a Stripe-shaped outcome, never an INTERNAL, so the
    None is theirs to handle gracefully."""
    row = ctx.db.one(
        "SELECT MIN(current_period_start) AS start FROM subscription_items WHERE subscription = ?",
        sub_id,
    )
    return None if row is None else row["start"]


def _period_end(ctx: seahaven.Ctx, sub_id: str) -> str | None:
    """The earliest item period end — None on an itemless subscription (see
    `_period_start`). The unrouted walkers refuse the None explicitly
    instead (an authoring fault, not stored state a caller can reach)."""
    row = ctx.db.one(
        "SELECT MIN(current_period_end) AS end FROM subscription_items WHERE subscription = ?",
        sub_id,
    )
    return None if row is None else row["end"]


def _price_of(ctx: seahaven.Ctx, price_id: str) -> dict[str, Any]:
    return _lookup.require_live_row(ctx, "prices", "price", price_id, param="price")


def _interval_of(price_row: Mapping[str, Any]) -> tuple[str, int]:
    recurring = _load_dict(price_row["recurring"])
    return str(recurring.get("interval", "month")), int(recurring.get("interval_count", 1))


def _resolve_pm(
    ctx: seahaven.Ctx, sub_row: Mapping[str, Any] | None, customer_id: str
) -> dict[str, Any] | None:
    pm_id = sub_row["default_payment_method"] if sub_row is not None else None
    if pm_id is None:
        customer = _lookup.require_row(ctx, "customers", "customer", customer_id, param="customer")
        settings = _load_dict(customer["invoice_settings"])
        pm_id = settings.get("default_payment_method")
    if pm_id is None:
        return None
    return _lookup.require_live_row(
        ctx, "payment_methods", "PaymentMethod", pm_id, param="payment_method"
    )


def _refuse_no_payment_method() -> None:
    raise invalid_request(_NO_PAYMENT_METHOD, code="resource_missing", status=400)


# --- creation -----------------------------------------------------------------------


def _resolve_discounts(
    ctx: seahaven.Ctx, discounts_param: list[dict[str, str]], *, park_once: bool = False
) -> tuple[list[DiscountSpec], list[dict[str, Any]]]:
    """The create-time `discounts[]` parameter -> (invoice-scope specs,
    persisted subscription-level discount objects).

    A `duration=once` coupon applies to the first PAID invoice: on a
    no-trial create that is the create invoice itself, so nothing persists
    and `subscription.discounts` reads `[]` (recorded, Phase 12); on a
    trialing create the $0 trial invoice pays nothing, so the once-coupon
    parks on the row and is consumed at the trial-end invoice
    (probed, Phase 12 CR round 4's trail). `forever`/`repeating` persist
    either way. The stored object is the full inline discount
    (`data_model.md` §6 — no discounts table), emitted as its `di_` id."""
    now = ctx.clock.iso()
    specs: list[DiscountSpec] = []
    persisted: list[dict[str, Any]] = []
    for index, entry in enumerate(discounts_param):
        coupon = _lookup.require_live_row(
            ctx, "coupons", "coupon", entry["coupon"], param=f"discounts[{index}][coupon]"
        )
        if coupon["redeem_by"] is not None and coupon["redeem_by"] <= now:
            # Code from the billing engine's error table; the exact message
            # spelling is unrecorded (Phase 12 did not probe it).
            raise invalid_request(f"Coupon {coupon['id']} is expired.", code="coupon_expired")
        discount_id = _ids.stripe_id(ctx, "di_")
        percent = Fraction(coupon["percent_off"]) if coupon["percent_off"] else None
        specs.append(
            DiscountSpec(
                discount_id=discount_id,
                coupon_id=coupon["id"],
                amount_off=coupon["amount_off"],
                percent_off=percent,
            )
        )
        if coupon["duration"] != "once" or park_once:
            persisted.append(
                {
                    "id": discount_id,
                    "object": "discount",
                    "source": {"type": "coupon", "coupon": coupon["id"]},
                    "start": _time.to_unix(now),
                }
            )
    return specs, persisted


def _tax_specs(ctx: seahaven.Ctx, tax_rate_ids: Sequence[str]) -> list[TaxRateSpec]:
    specs = []
    for index, tax_rate_id in enumerate(tax_rate_ids):
        row = _lookup.require_live_row(
            ctx, "tax_rates", "tax_rate", tax_rate_id, param=f"default_tax_rates[{index}]"
        )
        specs.append(
            TaxRateSpec(
                tax_rate_id=row["id"],
                percentage=Fraction(row["percentage"]),
                inclusive=bool(row["inclusive"]),
            )
        )
    return specs


def create_subscription(ctx: seahaven.Ctx, params: dict[str, Any]) -> dict[str, Any]:
    """`POST /v1/subscriptions`: the probed creation branches. Raises leave
    no rows (Seahaven's per-call transaction rolls the whole attempt back),
    which is exactly the recorded `error_if_incomplete` behavior."""
    now = ctx.clock.iso()
    # Recorded (Phase 12): an unknown customer is the 404 a path id earns,
    # not the 400 a request parameter usually gets.
    customer = _lookup.require_live_row(
        ctx, "customers", "customer", params["customer"], param="customer", status=404
    )
    payment_behavior = params.get("payment_behavior", "allow_incomplete")
    if payment_behavior == "pending_if_incomplete":
        # Recorded verbatim: update-only value, refused at create.
        raise invalid_request(
            "Setting `payment_behavior` to `pending_if_incomplete` has no effect "
            "when creating a subscription.",
            param="payment_behavior",
        )

    items_param = params["items"]
    price_rows: list[dict[str, Any]] = []
    for index, item in enumerate(items_param):
        price_rows.append(
            _lookup.require_live_row(
                ctx, "prices", "price", item["price"], param=f"items[{index}][price]"
            )
        )
    seen: list[str] = []
    for price_row in price_rows:
        if price_row["id"] in seen:
            # Recorded verbatim, including the legacy `param: "plan"`.
            raise invalid_request(
                "Cannot create a Subscription with multiple Subscription Items with "
                f"the same Price: {price_row['id']}",
                param="plan",
            )
        seen.append(price_row["id"])
        for other_index, other in enumerate(price_rows):
            if other["currency"] != price_row["currency"]:
                # Probed verbatim (fresh customer, mixed items): the
                # message names the OTHER item's currency and the refusal
                # points at that item's `items[N][price]`.
                raise invalid_request(
                    _CREATE_MIXED_CURRENCY.format(
                        price_currency=other["currency"],
                        subscription_currency=price_row["currency"],
                    ),
                    param=f"items[{other_index}][price]",
                )

    collection_method = params.get("collection_method", "charge_automatically")
    days_until_due = params.get("days_until_due")
    if days_until_due is not None and collection_method != "send_invoice":
        raise invalid_request(
            "You can only specify 'days_until_due' if invoice collection method is 'send_invoice'."
        )
    if collection_method == "send_invoice" and days_until_due is None:
        raise invalid_request(
            # Probed verbatim (Phase 12, uncommitted probe trail).
            "If invoice collection method is 'send_invoice', you must specify 'days_until_due'."
        )

    trial_end = _resolve_trial_end(ctx, params, price_rows, now)
    currency = price_rows[0]["currency"]

    # The payment-method gate: charge_automatically with no trial demands a
    # resolvable method before anything is written (recorded 400). Only an
    # explicit `default_payment_method` parameter is ever STORED on the row
    # (recorded: the customer's own default resolves the charge but the
    # subscription's field stays null).
    default_pm = params.get("default_payment_method")
    if default_pm is not None:
        _lookup.require_live_row(
            ctx, "payment_methods", "PaymentMethod", default_pm, param="default_payment_method"
        )
    needs_pm = (
        collection_method == "charge_automatically"
        and trial_end is None
        and default_pm is None
        and _resolve_pm(ctx, None, customer["id"]) is None
    )
    if needs_pm:
        _refuse_no_payment_method()

    discount_specs, persisted_discounts = _resolve_discounts(
        ctx, params.get("discounts", []), park_once=trial_end is not None
    )
    tax_specs = _tax_specs(ctx, params.get("default_tax_rates", []))

    # The row, with a provisional status the invoice outcome settles.
    sub_id = _ids.stripe_id(ctx, "sub_")
    cols: dict[str, Any] = {
        "id": sub_id,
        "x_seq": _seq.next_seq(ctx, "subscriptions"),
        "created": now,
        "billing_cycle_anchor": trial_end or now,
        "billing_schedules": "[]",
        "cancel_at": params.get("cancel_at"),
        "cancel_at_period_end": int(params.get("cancel_at_period_end", False)),
        "cancellation_details": _json.dumps(
            {"comment": None, "feedback": None, "feedback_option": None, "reason": None}
        ),
        "collection_method": collection_method,
        "currency": currency,
        "customer": customer["id"],
        "days_until_due": days_until_due,
        "default_payment_method": default_pm,
        "default_tax_rates": _json.dumps(list(params.get("default_tax_rates", []))),
        "description": params.get("description"),
        "discounts": _json.dumps(persisted_discounts),
        "billing_thresholds": (
            None
            if params.get("billing_thresholds") is None
            else _json.dumps(dict(params["billing_thresholds"]))
        ),
        "invoice_settings": _json.dumps(_DEFAULT_INVOICE_SETTINGS),
        "metadata": _json.dumps(dict(params.get("metadata") or {})),
        "payment_settings": _json.dumps(
            {**_DEFAULT_PAYMENT_SETTINGS, **dict(params.get("payment_settings") or {})}
        ),
        "start_date": now,
        "status": "active",
        "trial_settings": _json.dumps(_canonical_trial_settings(params.get("trial_settings"))),
        "trial_end": trial_end,
        # both-or-neither, per the DDL's CHECK: a trial's start is now
        "trial_start": now if trial_end is not None else None,
    }
    _insert_row(ctx, "subscriptions", cols)

    for index, item in enumerate(items_param):
        price_row = price_rows[index]
        interval, interval_count = _interval_of(price_row)
        period_start = now
        period_end = (
            trial_end
            if trial_end is not None
            else invoicing.add_interval(now, interval=interval, interval_count=interval_count)
        )
        _insert_row(
            ctx,
            "subscription_items",
            {
                "id": _ids.stripe_id(ctx, "si_"),
                "x_seq": _seq.next_seq(ctx, "subscription_items"),
                "created": now,
                "current_period_start": period_start,
                "current_period_end": period_end,
                "price": price_row["id"],
                "quantity": _default_quantity(item.get("quantity"), price_row),
                "subscription": sub_id,
                "tax_rates": _json.dumps(list(item.get("tax_rates", []))),
            },
        )

    sub_row = _re_read(ctx, sub_id)
    if trial_end is not None:
        # Recorded (Phase 12 probe): the trialing subscription's first
        # invoice exists — a paid $0 `subscription_create` invoice whose
        # line is `Free trial for 1 x <product>` over `[start, trial_end)`.
        # The $0 trial invoice bills nothing to discount: a parked
        # once-coupon waits for the first PAID invoice (probed, round 4's
        # trail), so no specs are applied — not even the row's own
        # persisted ones. The stored SHAPE still carries the parked
        # discount (probed, round 5's trail invoice): the `discounts`
        # column lists it and `total_discount_amounts` marks it at 0.
        invoice = _cycle_invoice(
            ctx,
            sub_row,
            billing_reason="subscription_create",
            auto_advance=False,
            finalize=True,
            trial=True,
            discount_specs=[],
            applied_discount_objects=[
                {
                    "id": spec.discount_id,
                    "object": "discount",
                    "source": {"type": "coupon", "coupon": spec.coupon_id},
                    "start": _time.to_unix(now),
                }
                for spec in discount_specs
            ],
            zero_total_discounts=[
                {"amount": 0, "discount": spec.discount_id} for spec in discount_specs
            ],
        )
        invoicing.pay_invoice(ctx, invoice["id"], sub_row=sub_row)
        _set_latest_invoice(ctx, sub_id, invoice["id"])
        _set_status(ctx, sub_id, "trialing")
        if _resolve_pm(ctx, sub_row, customer["id"]) is None:
            # Recorded (Phase 12): a trial the customer has no method to pay
            # mints the add-a-payment-method SetupIntent immediately — the
            # pause and resume bodies expose this same id.
            seti_id = _mint_resume_setup_intent(ctx, sub_row)
            ctx.db.execute(
                "UPDATE subscriptions SET pending_setup_intent = ? WHERE id = ?",
                seti_id,
                sub_id,
            )
    elif collection_method == "send_invoice":
        # Recorded: the first send_invoice invoice stays DRAFT with the
        # auto-advance window ahead of it — activation does not wait on it.
        invoice = _cycle_invoice(
            ctx,
            sub_row,
            billing_reason="subscription_create",
            auto_advance=True,
            finalize=False,
            discount_specs=discount_specs,
            tax_specs=tax_specs,
        )
        _set_latest_invoice(ctx, sub_id, invoice["id"])
        _set_status(ctx, sub_id, "active")
    else:
        _charge_first_invoice(
            ctx,
            sub_row,
            payment_behavior=payment_behavior,
            discount_specs=discount_specs,
            tax_specs=tax_specs,
        )

    body = _serialize(ctx, _re_read(ctx, sub_id))
    # Created last, after every row the change wrote (the emission rule) —
    # the snapshot carries `latest_invoice` set.
    events.emit_event(ctx, type="customer.subscription.created", obj=body)
    return body


def _resolve_trial_end(
    ctx: seahaven.Ctx,
    params: Mapping[str, Any],
    price_rows: list[dict[str, Any]],
    now: str,
) -> str | None:
    """`trial_end` / `trial_period_days` / `trial_from_plan` -> the trial's
    end instant, with the two recorded refusals (the past timestamp; the
    from-plan conflict)."""
    trial_from_plan = params.get("trial_from_plan") is True
    if ("trial_end" in params or "trial_period_days" in params) and trial_from_plan:
        # Recorded verbatim (Phase 12): the message alone, no `param`.
        raise invalid_request(_TRIAL_FROM_PLAN_CONFLICT)
    if "trial_end" in params:
        value = params["trial_end"]
        if value == "now":
            # A zero-length trial collapses to no trial (probed, Phase 12
            # CR round 3: 200, `status: active`, null trial stamps, anchor
            # = created, item period `[now, +1 interval)`). Returning `now`
            # instead would build a zero-length item period the schema's
            # CHECK forbids — an Internal on a legal parameter.
            return None
        end = _time.from_unix(int(value))
        if end <= now:
            raise invalid_request(
                "The parameter `trial_end` expects a unix timestamp representing a "
                f"date and time in the future. You specified the value `{value}` which "
                "is in the past.",
                param="trial_end",
            )
        return end
    days: int | None = None
    if trial_from_plan:
        for price_row in price_rows:
            recurring = _load_dict(price_row["recurring"])
            if recurring.get("trial_period_days"):
                days = int(recurring["trial_period_days"])
                break
    elif "trial_period_days" in params:
        days = int(params["trial_period_days"])
    if not days:
        return None
    return _time.from_unix(_time.to_unix(now) + days * 86_400)


def _canonical_trial_settings(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not value:
        return _DEFAULT_TRIAL_SETTINGS
    end_behavior = dict(value.get("end_behavior") or {})
    return {
        "end_behavior": {
            "missing_payment_method": end_behavior.get("missing_payment_method", "create_invoice")
        }
    }


#: Probed verbatim (Phase 12 CR probes, 2026-09-21 — an uncommitted probe
#: trail; the next cassette re-record binds these steps properly): the refusal an item
#: addition earns when the subscription already bills that price — the same
#: message on /v1/subscription_items and on a sub-update `items[]` entry,
#: `param: "plan"`, both ids interpolated.
_ITEM_DUPLICATE_PRICE = (
    "A new item with Price {price} can't be added to this Subscription because an "
    "existing Subscription Item {item} is already using that Price. If you want to "
    "update the existing item (e.g., to adjust the quantity), pass the existing "
    "Subscription Item's `id` in your update request: "
    "https://stripe.com/docs/api/subscriptions/update#update_subscription-items-id"
)

#: Probed verbatim (fresh customer, items of two currencies): the create-time
#: currency guard names the offending items[N][price].
_CREATE_MIXED_CURRENCY = (
    "This `price` has `currency={price_currency}`, but other items use "
    "`currency={subscription_currency}`. All items must have pricing in the same "
    "currency. When using multi-currency prices, you can specify `currency` at the "
    "top level, to be used by all items."
)

#: Probed verbatim (/v1/subscription_items against a subscription's own
#: currency). The sub-update `items[]` path reuses the sentence under the
#: bracketed param it spelled — an unrecorded adaptation, declared here.
_ITEM_MIXED_CURRENCY = (
    "The price specified only supports `{price_currency}`. This doesn't match the "
    "expected currency: `{subscription_currency}`."
)


def _guard_item_addition(
    ctx: seahaven.Ctx,
    sub_id: str,
    price_row: Mapping[str, Any],
    *,
    price_param: str,
) -> None:
    """The two guards every item addition passes: no duplicate price (the
    recorded add refusal) and the price's currency matches the
    subscription's (the recorded currency refusal)."""
    existing = ctx.db.one(
        "SELECT id FROM subscription_items WHERE subscription = ? AND price = ?",
        sub_id,
        price_row["id"],
    )
    if existing is not None:
        raise invalid_request(
            _ITEM_DUPLICATE_PRICE.format(price=price_row["id"], item=existing["id"]),
            param="plan",
        )
    sub = _re_read(ctx, sub_id)
    if price_row["currency"] != sub["currency"]:
        raise invalid_request(
            _ITEM_MIXED_CURRENCY.format(
                price_currency=price_row["currency"], subscription_currency=sub["currency"]
            ),
            param=price_param,
        )


def _default_quantity(quantity: int | None, price_row: Mapping[str, Any]) -> int | None:
    """Recorded (Phase 12): an omitted quantity answers 1 on a licensed
    price; a metered price keeps null (usage is counted, not declared)."""
    if quantity is not None:
        return quantity
    recurring = _load_dict(price_row["recurring"])
    return None if recurring.get("usage_type") == "metered" else 1


def _insert_row(ctx: seahaven.Ctx, table: str, cols: dict[str, Any]) -> None:
    columns = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    ctx.db.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", *cols.values())


def _set_status(ctx: seahaven.Ctx, sub_id: str, status: str) -> None:
    ctx.db.execute("UPDATE subscriptions SET status = ? WHERE id = ?", status, sub_id)


def _set_latest_invoice(ctx: seahaven.Ctx, sub_id: str, invoice_id: str) -> None:
    ctx.db.execute("UPDATE subscriptions SET latest_invoice = ? WHERE id = ?", invoice_id, sub_id)


def _charge_first_invoice(
    ctx: seahaven.Ctx,
    sub_row: Mapping[str, Any],
    *,
    payment_behavior: str,
    discount_specs: list[DiscountSpec],
    tax_specs: list[TaxRateSpec],
) -> None:
    """The charge_automatically creation path: create → finalize → attempt.
    `error_if_incomplete` raises (and the whole call's rows roll back); the
    incomplete behaviors keep the open invoice and set `incomplete`."""
    invoice = _cycle_invoice(
        ctx,
        sub_row,
        billing_reason="subscription_create",
        auto_advance=False,
        finalize=True,
        discount_specs=discount_specs,
        tax_specs=tax_specs,
    )
    _set_latest_invoice(ctx, sub_row["id"], invoice["id"])
    outcome = invoicing.pay_invoice(ctx, invoice["id"], sub_row=sub_row)
    if outcome["outcome"] == "paid":
        return
    if payment_behavior == "error_if_incomplete":
        if outcome["outcome"] == "requires_action":
            # Recorded verbatim (the 402 `card_error` the 3DS card earns).
            raise StripeApiError(
                402,
                "card_error",
                "Payment for this subscription requires additional user action, but "
                "the requested payment behavior doesn't save the invoice or "
                "PaymentIntent. Retry the request with "
                "`payment_behavior=allow_incomplete` or "
                "`payment_behavior=default_incomplete`, then complete the "
                "PaymentIntent on the subscription's latest invoice. Additional "
                "information is available here: "
                "https://stripe.com/docs/billing/subscriptions/overview"
                "#requires-action",
                code="subscription_payment_intent_requires_action",
            )
        # The decline flavor is unrecordable at the pinned version (no
        # attachable decline token exists; Phase 12's structural
        # declaration): the spec's 402 `card_declined` outcome, raised so
        # nothing survives.
        error = outcome["error"]["error"]
        raise StripeApiError(
            402,
            "card_error",
            str(error["message"]),
            code=error.get("code"),
            decline_code=error.get("decline_code"),
            charge=error.get("charge"),
        )
    _set_latest_invoice(ctx, sub_row["id"], invoice["id"])
    _set_status(ctx, sub_row["id"], "incomplete")


def _persisted_discount_specs(ctx: seahaven.Ctx, sub_row: Mapping[str, Any]) -> list[DiscountSpec]:
    """The subscription's persisted `forever`/`repeating` discounts,
    re-resolved from their coupon rows (a `repeating` window may have
    lapsed since it was stored — the coupon row is the authority).

    The coupon lookup is deliberately tolerant of a soft-deleted row:
    Stripe documents that deleting a coupon does not affect discounts
    already applied, so stored state must keep billing — a tombstoned
    coupon never 400s a call whose caller never passed `discounts`
    (`_consume_once_discounts` reads it the same tolerant way)."""
    specs: list[DiscountSpec] = []
    for discount in _load_list(sub_row["discounts"]):
        if not isinstance(discount, dict):
            continue
        source = discount.get("source")
        source = source if isinstance(source, dict) else _load_dict(source)
        coupon_id = source.get("coupon")
        if coupon_id is None:
            continue
        coupon = _lookup.require_row(ctx, "coupons", "coupon", str(coupon_id), param="discounts")
        specs.append(
            DiscountSpec(
                discount_id=str(discount["id"]),
                coupon_id=coupon["id"],
                amount_off=coupon["amount_off"],
                percent_off=Fraction(coupon["percent_off"]) if coupon["percent_off"] else None,
            )
        )
    return specs


def _consume_once_discounts(ctx: seahaven.Ctx, sub_id: str) -> None:
    """Drop the row's `duration=once` discounts — the invoice that just
    paid consumed them (probed, Phase 12 CR round 4's trail: a once-coupon
    parked on a trialing row applies to the first paid invoice at trial
    end and the row reads `[]` after). `forever`/`repeating` discounts
    stay; the coupon rows are the duration authority."""
    kept = []
    for discount in _load_list(_re_read(ctx, sub_id)["discounts"]):
        if not isinstance(discount, dict):
            continue
        source = discount.get("source")
        source = source if isinstance(source, dict) else _load_dict(source)
        coupon_id = source.get("coupon")
        coupon = (
            _lookup.require_row(ctx, "coupons", "coupon", str(coupon_id), param="discounts")
            if coupon_id is not None
            else None
        )
        if coupon is not None and coupon["duration"] == "once":
            continue
        kept.append(discount)
    ctx.db.execute("UPDATE subscriptions SET discounts = ? WHERE id = ?", _json.dumps(kept), sub_id)


def _persisted_tax_specs(ctx: seahaven.Ctx, sub_row: Mapping[str, Any]) -> list[TaxRateSpec]:
    specs = []
    for id_ in _load_list(sub_row["default_tax_rates"]):
        tax_row = _lookup.require_row(
            ctx, "tax_rates", "tax rate", str(id_), param="default_tax_rates"
        )
        specs.append(
            TaxRateSpec(
                tax_rate_id=tax_row["id"],
                percentage=Fraction(tax_row["percentage"]),
                inclusive=bool(tax_row["inclusive"]),
            )
        )
    return specs


def _cycle_invoice(
    ctx: seahaven.Ctx,
    sub_row: Mapping[str, Any],
    *,
    billing_reason: str,
    auto_advance: bool,
    finalize: bool,
    discount_specs: list[DiscountSpec] | None = None,
    tax_specs: list[TaxRateSpec] | None = None,
    days_until_due: int | None = None,
    trial: bool = False,
    period_start: str | None = None,
    period_end: str | None = None,
    applied_discount_objects: list[dict[str, Any]] | None = None,
    zero_total_discounts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create (and optionally finalize) one invoice over the subscription's
    current item periods, with the coupon and tax arithmetic applied. The
    invoice's own `period_*` columns default to the creation instant (the
    recorded `subscription_create` shape); a renewal passes the boundary
    interval it bills.

    Callers that resolve discounts themselves (creation, which applies its
    once-coupons alongside the persisted ones) pass `discount_specs`; every
    other flow passes none and the row's persisted state is applied — a
    `forever`/`repeating` coupon and the `default_tax_rates` ids bill on
    every invoice this mints, so no renewal ever claims tax rates its
    totals never applied."""
    applied_discounts = discount_specs
    if applied_discounts is None:
        applied_discounts = _persisted_discount_specs(ctx, sub_row)
    applied_tax = tax_specs
    if applied_tax is None:
        applied_tax = _persisted_tax_specs(ctx, sub_row)
    items = _items_of(ctx, sub_row["id"])
    line_inputs = invoicing.build_item_lines(ctx, items, currency=sub_row["currency"], trial=trial)
    totals = invoicing.compute_totals(
        line_inputs,
        currency=sub_row["currency"],
        invoice_discounts=applied_discounts,
        tax_rates=applied_tax,
    )
    if zero_total_discounts is not None:
        totals.total_discount_amounts.extend(zero_total_discounts)
    now = ctx.clock.iso()
    invoice = invoicing.create_invoice(
        ctx,
        customer_id=sub_row["customer"],
        currency=sub_row["currency"],
        collection_method=sub_row["collection_method"],
        billing_reason=billing_reason,
        totals=totals,
        subscription_id=sub_row["id"],
        auto_advance=auto_advance,
        days_until_due=days_until_due if days_until_due is not None else sub_row["days_until_due"],
        period_start=period_start if period_start is not None else now,
        period_end=period_end if period_end is not None else now,
        # The invoice's own `discounts` column carries the discount objects
        # its totals applied — persisted and once-coupons alike — or the
        # parked-shape objects a caller passes for a $0 invoice.
        discounts=(
            applied_discount_objects
            if applied_discount_objects is not None
            else [
                {
                    "id": spec.discount_id,
                    "object": "discount",
                    "source": {"type": "coupon", "coupon": spec.coupon_id},
                    "start": _time.to_unix(now),
                }
                for spec in applied_discounts
            ]
        ),
        tax_rate_ids=_load_list(sub_row["default_tax_rates"]),
    )
    if finalize:
        return invoicing.finalize_invoice(ctx, invoice["id"])
    return invoice


def _conversion_invoice(
    ctx: seahaven.Ctx,
    row: Mapping[str, Any],
    *,
    now: str,
    trial_end: str,
    abandoned_ends: Mapping[str, str],
) -> None:
    """The invoice an active subscription's conversion to a trial mints
    (probed, Phase 12 CR rounds 3-5, uncommitted probe trail): one `Unused
    time on <product> after <date>` credit line over the abandoned period's
    remainder — full price under this world's frozen clock, where `now` is
    the period's start — plus the $0 `Free trial` line over
    `[now, trial_end)`, `billing_reason: subscription_update`, paid with no
    charge and no counted attempt.

    The credit is proration-shaped — never discountable, `proration: true`
    under its parent, no `unit_amount_decimal` — but the row's coupon and
    tax state still reaches it (probed, round 5, forever 10% + 5% GST): a
    percent coupon is NETTED INTO the credit's amount with a `(with
    10.0% off)` description suffix, the discount appears as amount-0
    entries rather than apportioned shares, and the tax computes on the
    netted negative base (-1800 -> -90 -> total -1890). An `amount_off`
    coupon nets analogously (declared; only the percent shape is probed).

    The live credit also carries two back-links this world defers: an
    `invoice_item` (`ii_…`) naming the proration item behind it, and
    `proration_details.credited_items = {invoice, invoice_line_items}`
    pointing at the original debit. Both need the Phase 13/14
    `invoiceitems` machinery; declared here rather than half-built."""
    from datetime import datetime

    items = _items_of(ctx, row["id"])
    if not items:
        # An itemless subscription (spec-blessed) converts with nothing to
        # credit and nothing to bill: no invoice, the trial-end branches'
        # ruling applied.
        return
    discount_specs = _persisted_discount_specs(ctx, row)
    lines: list[invoicing.ItemLine] = []
    after = datetime.strptime(now, "%Y-%m-%dT%H:%M:%S.%fZ").strftime("%d %b %Y")
    for item in items:
        price_row = _price_of(ctx, item["price"])
        product = _lookup.require_row(
            ctx, "products", "product", price_row["product"], param="product"
        )
        quantity = item["quantity"] if item["quantity"] is not None else 1
        unit = price_row["unit_amount"] or 0
        gross = -unit
        with_off = ""
        for discount in discount_specs:
            # Net into the credit (probed, round 5): a percent coupon
            # scales it, an amount-off coupon subtracts — clamped to the
            # magnitude.
            if discount.percent_off is not None:
                # The credit is negative; the coupon reduces its magnitude
                gross += round_half_up(Fraction(unit * discount.percent_off, 100))
                with_off += f" (with {float(discount.percent_off):.1f}% off)"
            elif discount.amount_off is not None:
                gross += min(discount.amount_off, unit)
        lines.append(
            invoicing.ItemLine(
                subscription_item_id=item["id"],
                subscription_id=row["id"],
                price_id=price_row["id"],
                product_id=price_row["product"],
                unit_amount=gross,
                quantity=quantity,
                period_start=now,
                period_end=abandoned_ends[item["id"]],
                description=f"Unused time on {product['name']}{with_off} after {after}",
                proration=True,
            )
        )
    for item in items:
        price_row = _price_of(ctx, item["price"])
        product = _lookup.require_row(
            ctx, "products", "product", price_row["product"], param="product"
        )
        quantity = item["quantity"] if item["quantity"] is not None else 1
        lines.append(
            invoicing.ItemLine(
                subscription_item_id=item["id"],
                subscription_id=row["id"],
                price_id=price_row["id"],
                product_id=price_row["product"],
                unit_amount=0,
                quantity=quantity,
                period_start=now,
                period_end=trial_end,
                description=f"Free trial for {quantity} \u00d7 {product['name']}",
            )
        )
    totals = invoicing.compute_totals(
        lines, currency=row["currency"], tax_rates=_persisted_tax_specs(ctx, row)
    )
    # The netted coupon shows as amount-0 entries (probed, round 5): the
    # credit's amount already carries it, so the per-line and invoice-level
    # discount records mark the discount without subtracting again.
    zero_entries = [{"amount": 0, "discount": spec.discount_id} for spec in discount_specs]
    totals.total_discount_amounts.extend(zero_entries)
    for line in totals.lines:
        if line["description"].startswith("Unused time on"):
            line["discount_amounts"].extend(zero_entries)
            line["discounts"] = sorted(spec.discount_id for spec in discount_specs)
    invoice = invoicing.create_invoice(
        ctx,
        customer_id=row["customer"],
        currency=row["currency"],
        collection_method=row["collection_method"],
        billing_reason="subscription_update",
        totals=totals,
        subscription_id=row["id"],
        auto_advance=False,
        days_until_due=row["days_until_due"],
        period_start=now,
        period_end=now,
        discounts=[
            {
                "id": spec.discount_id,
                "object": "discount",
                "source": {"type": "coupon", "coupon": spec.coupon_id},
                "start": _time.to_unix(now),
            }
            for spec in discount_specs
        ],
        tax_rate_ids=_load_list(row["default_tax_rates"]),
    )
    if row["collection_method"] == "send_invoice":
        # A send_invoice conversion is never charged (the create's own
        # draft ruling applied to the credit; unprobed, declared): the
        # invoice stays draft with its auto-advance window.
        _set_latest_invoice(ctx, row["id"], invoice["id"])
        return
    invoicing.finalize_invoice(ctx, invoice["id"])
    # Latest before pay: the once-coupon netted into the credit is consumed
    # by the paid hook, which keys on the subscription's latest invoice.
    _set_latest_invoice(ctx, row["id"], invoice["id"])
    invoicing.pay_invoice(ctx, invoice["id"], sub_row=row)


# --- the trial-end path ---------------------------------------------------------------


def trial_end_now(
    ctx: seahaven.Ctx,
    row: Mapping[str, Any],
    *,
    old_body: Mapping[str, Any] | None = None,
    billing_reason: str = "subscription_update",
    moment: str | None = None,
) -> None:
    """`trial_end = "now"`: the recorded outcomes.

    `old_body` is the caller's pre-change wire body when it holds one
    (apply_update captures it before its own writes); without it the row's
    serialization stands in, which loses co-supplied fields' deltas — the
    row an update path passes has often already absorbed the `sets` UPDATE.

    `billing_reason` follows the trigger (probed in both billing modes,
    Phase 12 CR round 4's sandbox trail): the update-driven end bills
    `subscription_update`; `advance_cycle`'s natural-boundary walker passes
    `subscription_cycle` (unprobed — nothing routed reaches it).

    `moment` overrides the instant the end happens at — the walker passes
    the boundary, which a frozen clock cannot reach on its own."""
    now = moment if moment is not None else ctx.clock.iso()
    captured = _serialize(ctx, row) if old_body is None else old_body
    if row["collection_method"] == "send_invoice":
        # Probed (Phase 12 CR round 5): a send_invoice trial end answers
        # 200 `active` with the invoice left DRAFT (`auto_advance: true`,
        # `attempted: false`, `attempt_count: 0`, `due_date` = now +
        # days_until_due) and no charge — with or without a payment method,
        # because activation never waits on payment for send_invoice. Roll
        # first, invoice after, exactly as the PM branch: the line bills the
        # NEW period, never the trial span the $0 invoice already covered.
        _set_status(ctx, row["id"], "active")
        _roll_periods_to(ctx, row["id"], now)
        period_end = _period_end(ctx, row["id"])
        if period_end is not None:
            invoice = _cycle_invoice(
                ctx,
                row,
                billing_reason=billing_reason,
                auto_advance=True,
                finalize=False,
                period_start=now,
                period_end=period_end,
            )
            _set_latest_invoice(ctx, row["id"], invoice["id"])
        # an itemless trial end bills nothing (the PM branch's ruling)
        fresh_body = _serialize(ctx, _re_read(ctx, row["id"]))
        events.emit_event(
            ctx,
            type="customer.subscription.updated",
            obj=fresh_body,
            previous=_previous(captured, fresh_body),
        )
        return
    settings = _load_dict(row["trial_settings"]) or _DEFAULT_TRIAL_SETTINGS
    behavior = settings.get("end_behavior", {}).get("missing_payment_method", "create_invoice")
    pm_row = _resolve_pm(ctx, row, row["customer"])
    if pm_row is not None:
        # Roll first, invoice after (probed): the first paid invoice's line
        # spans the NEW period `[now, now + interval)`, and the row's
        # persisted discounts — including a once-coupon the trial parked —
        # apply to it and are consumed here.
        _set_status(ctx, row["id"], "active")
        _roll_periods_to(ctx, row["id"], now)
        period_end = _period_end(ctx, row["id"])
        if period_end is None:
            # An itemless subscription (spec-blessed) has nothing to bill
            # and nothing to roll: the status moves and no invoice is
            # minted — the declared unrecorded ruling.
            fresh_body = _serialize(ctx, _re_read(ctx, row["id"]))
            events.emit_event(
                ctx,
                type="customer.subscription.updated",
                obj=fresh_body,
                previous=_previous(captured, fresh_body),
            )
            return
        invoice = _cycle_invoice(
            ctx,
            row,
            billing_reason=billing_reason,
            auto_advance=False,
            finalize=True,
            period_start=now,
            period_end=period_end,
        )
        # Latest before pay: `on_invoice_paid` keys on the subscription's
        # latest invoice, and the once-coupon consumption rides on it.
        _set_latest_invoice(ctx, row["id"], invoice["id"])
        outcome = invoicing.pay_invoice(ctx, invoice["id"], sub_row=row)
        if outcome["outcome"] != "paid":
            # The post-trial charge that fails lands the subscription in
            # `past_due` (billing_engine §1's trialing row); the parked
            # once-coupons wait for the invoice's payment
            # (`on_invoice_paid` consumes them).
            _set_status(ctx, row["id"], "past_due")
        fresh_body = _serialize(ctx, _re_read(ctx, row["id"]))
        events.emit_event(
            ctx,
            type="customer.subscription.updated",
            obj=fresh_body,
            previous=_previous(captured, fresh_body),
        )
        return
    if behavior == "pause":
        # Live collapses each item's period to the pause instant (probed:
        # `[trial_start, pause_moment)`); under a frozen clock that instant
        # equals the trial start, and the schema's
        # `current_period_start < current_period_end` CHECK forbids a
        # zero-length period — the items' existing `[start, trial_end)`
        # periods already say everything a frozen clock can say, so they
        # stand. Declared divergence, not an oversight.
        _set_status(ctx, row["id"], "paused")
        events.emit_event(
            ctx, type="customer.subscription.paused", obj=_serialize(ctx, _re_read(ctx, row["id"]))
        )
        return
    if behavior == "cancel":
        stamp = {
            "comment": None,
            "feedback": None,
            "feedback_option": None,
            "reason": "cancellation_requested",
        }
        ctx.db.execute(
            "UPDATE subscriptions SET status = 'canceled', canceled_at = ?, ended_at = ?,"
            " cancellation_details = ? WHERE id = ?",
            now,
            now,
            _json.dumps(stamp),
            row["id"],
        )
        events.emit_event(
            ctx, type="customer.subscription.deleted", obj=_serialize(ctx, _re_read(ctx, row["id"]))
        )
        return
    # The default (`create_invoice`) with no method: the recorded refusal —
    # the invoice the behavior names cannot be collected.
    _refuse_no_payment_method()


def _roll_periods_to(ctx: seahaven.Ctx, sub_id: str, start: str) -> None:
    """Restart every item's period at `start`, one interval forward."""
    for item in _items_of(ctx, sub_id):
        price_row = _price_of(ctx, item["price"])
        interval, interval_count = _interval_of(price_row)
        end = invoicing.add_interval(start, interval=interval, interval_count=interval_count)
        if end <= start:
            raise seahaven.WorldBug(f"non-forward period for item {item['id']!r}")
        ctx.db.execute(
            "UPDATE subscription_items SET current_period_start = ?, current_period_end = ?"
            " WHERE id = ?",
            start,
            end,
            item["id"],
        )


# --- update / cancel / resume ----------------------------------------------------------


_CANCELED_OK = frozenset(("cancellation_details", "metadata"))


def apply_update(ctx: seahaven.Ctx, sub_id: str, params: dict[str, Any]) -> dict[str, Any]:
    row = _re_read(ctx, sub_id)
    # The pre-change wire body, serialized before this call's writes (the
    # `items` derivation hazard `_previous` documents).
    old_body = _serialize(ctx, row)
    if row["status"] == "canceled" or row["status"] == "incomplete_expired":
        # Recorded for `canceled` (`invalid_canceled_subscription_fields`);
        # the `incomplete_expired` spelling is the same refusal applied by
        # symmetry — unrecorded, declared.
        offending = [key for key in params if key not in _CANCELED_OK]
        if offending:
            raise invalid_request(_CANCELED_FIELDS, code="invalid_canceled_subscription_fields")
    if row["status"] == "incomplete" and (
        offending := [
            key
            for key in params
            if key not in ("description", "default_source", "default_payment_method", "metadata")
        ]
    ):
        # Recorded: `metadata` and `description` both succeed on incomplete
        # (correcting §1's metadata/default-source-only row), and
        # `default_payment_method` accepts both a set and the empty-string
        # clear (probed live on an incomplete 3DS subscription, Phase 12 CR
        # round 2 — the rescue move that points the subscription at a
        # working card); any wider change refuses with the spec's
        # `status_transition_invalid` — the message spelling is unprobed,
        # declared.
        raise invalid_request(
            f"Cannot update {offending[0]} on an incomplete subscription. Only "
            "metadata, description, default_source and default_payment_method can be "
            "updated.",
            code="status_transition_invalid",
            param=offending[0],
        )

    now = ctx.clock.iso()
    sets: dict[str, Any] = {}
    if "metadata" in params:
        sets["metadata"] = _json.dumps(dict(params["metadata"] or {}))
    if "description" in params:
        sets["description"] = params["description"]
    if "default_payment_method" in params:
        if params["default_payment_method"] is None:
            # Recorded (Phase 12 CR probe): the empty string clears the
            # field (200, `default_payment_method: null`); the binder maps
            # `""` to None for this parameter.
            sets["default_payment_method"] = None
        else:
            _lookup.require_live_row(
                ctx,
                "payment_methods",
                "PaymentMethod",
                params["default_payment_method"],
                param="default_payment_method",
            )
            sets["default_payment_method"] = params["default_payment_method"]
    if "cancel_at_period_end" in params:
        sets["cancel_at_period_end"] = int(params["cancel_at_period_end"])
        # An itemless subscription (spec-blessed) carries no period, so the
        # flag stands with a null scheduled instant — an honestly-declared
        # unrecorded shape (live's itemless update is unprobed; the MCP
        # surface does not reach the item DELETE).
        sets["cancel_at"] = _period_end(ctx, sub_id) if params["cancel_at_period_end"] else None
    if "cancel_at" in params:
        sets["cancel_at"] = params["cancel_at"]
    if "collection_method" in params:
        sets["collection_method"] = params["collection_method"]
    if "days_until_due" in params:
        if (
            params["days_until_due"] is not None
            and params.get("collection_method", row["collection_method"]) != "send_invoice"
        ):
            raise invalid_request(
                "You can only specify 'days_until_due' if invoice collection method "
                "is 'send_invoice'."
            )
        sets["days_until_due"] = params["days_until_due"]
    if "default_tax_rates" in params:
        _tax_specs(ctx, params["default_tax_rates"])
        sets["default_tax_rates"] = _json.dumps(list(params["default_tax_rates"]))
    if "discounts" in params:
        # Probed (Phase 12 CR round 5): an update parks the discount on the
        # row — once-coupons included, on both active and trialing rows; the
        # once ones are consumed by the next invoice that pays.
        _, persisted = _resolve_discounts(ctx, params["discounts"], park_once=True)
        sets["discounts"] = _json.dumps(persisted)
    if "pause_collection" in params:
        pause = params["pause_collection"]
        sets["pause_collection"] = (
            None
            if pause is None
            else _json.dumps(
                {
                    "behavior": pause.get("behavior"),
                    # The binder hands the timestamp over as canonical ISO;
                    # values inside a JSON blob are Unix seconds
                    # (`data_model.md` §3.10), so the storage converts.
                    "resumes_at": (
                        _time.to_unix(pause["resumes_at"])
                        if pause.get("resumes_at") is not None
                        else None
                    ),
                }
            )
        )
    if "payment_settings" in params:
        payment_settings = dict(params["payment_settings"] or {})
        merged = {**_DEFAULT_PAYMENT_SETTINGS, **payment_settings}
        sets["payment_settings"] = _json.dumps(merged)
    if "billing_thresholds" in params:
        sets["billing_thresholds"] = (
            None
            if params["billing_thresholds"] is None
            else _json.dumps(params["billing_thresholds"])
        )
    if "trial_settings" in params:
        sets["trial_settings"] = _json.dumps(_canonical_trial_settings(params["trial_settings"]))
    if "cancellation_details" in params or "cancel_at_period_end" in params:
        # Recorded (Phase 12): scheduling the cancel stamps the reason and
        # un-scheduling clears the stamp; a simultaneously supplied
        # `cancellation_details` merges over the stamp (its own `reason`
        # key, if any, wins).
        current = _load_dict(row["cancellation_details"])
        details = dict(params.get("cancellation_details") or {})
        if "cancel_at_period_end" in params and "reason" not in details:
            details["reason"] = "cancellation_requested" if params["cancel_at_period_end"] else None
        sets["cancellation_details"] = _json.dumps({**current, **details})
    if sets:
        assignments = ", ".join(f"{column} = ?" for column in sets)
        ctx.db.execute(
            f"UPDATE subscriptions SET {assignments} WHERE id = ?", *sets.values(), sub_id
        )

    if "items" in params:
        _apply_items_update(ctx, row, params["items"])
    if "trial_end" in params and params["trial_end"] == "now":
        fresh = _re_read(ctx, sub_id)
        if fresh["status"] == "trialing":
            ctx.db.execute("UPDATE subscriptions SET trial_end = ? WHERE id = ?", now, sub_id)
            # The pre-change body is apply_update's own capture: `fresh`
            # was read after this call's `sets` UPDATE, so serializing it
            # here would lose co-supplied fields' deltas from the diff.
            trial_end_now(ctx, fresh, old_body=old_body)
        else:
            # Recorded past-timestamp refusal guard: only "now" reaches here.
            raise invalid_request(
                "The parameter `trial_end` expects a unix timestamp representing a "
                "date and time in the future.",
                param="trial_end",
            )
    elif "trial_end" in params:
        end = _time.from_unix(int(params["trial_end"]))
        if end <= now:
            raise invalid_request(
                "The parameter `trial_end` expects a unix timestamp representing a "
                f"date and time in the future. You specified the value "
                f"`{params['trial_end']}` which is in the past.",
                param="trial_end",
            )
        if row["status"] == "paused":
            # Probed (Phase 12 CR round 5): the paused refusal carries no
            # `code` and no `param`, message verbatim.
            raise invalid_request(
                "You cannot set `trial_end` while a subscription is `paused`. Resume the "
                "subscription first before setting `trial_end`."
            )
        if row["status"] not in ("active", "trialing"):
            # Probed on `active` only (round 3/4's trail); the remaining
            # conversions are unprobed and refuse rather than silently
            # producing a shape live never showed. The message is a
            # declared unprobed spelling.
            raise invalid_request(
                f"Cannot update trial_end on a subscription with status `{row['status']}`."
                " Only active or trialing subscriptions can be updated with trial_end.",
                code="status_transition_invalid",
                param="trial_end",
            )
        # Probed (Phase 12 CR round 3, uncommitted probe trail): a future
        # `trial_end` on a subscription not already trialing converts it —
        # status flips to `trialing`, `trial_start = now`, the item period
        # rebuilds to `[now, trial_end)`, the anchor moves to the trial end,
        # and one `subscription_update` invoice is minted: a full-price
        # `Unused time on <product> after <date>` credit line over the
        # remainder of the abandoned period plus the $0 trial line, paid
        # with `attempt_count: 0`. A subscription already trialing moves
        # its end the same way, minus the credit (nothing was abandoned).
        abandoned = {item["id"]: item["current_period_end"] for item in _items_of(ctx, sub_id)}
        ctx.db.execute(
            "UPDATE subscriptions SET trial_end = ?, trial_start = ?,"
            " billing_cycle_anchor = ?, status = 'trialing' WHERE id = ?",
            end,
            row["trial_start"] if row["status"] == "trialing" else now,
            end,
            sub_id,
        )
        # the trial period is `[now, trial_end)` — not an interval past a
        # boundary, so this is its own roll rather than `_roll_periods_to`
        ctx.db.execute(
            "UPDATE subscription_items SET current_period_start = ?, current_period_end = ?"
            " WHERE subscription = ?",
            now,
            end,
            sub_id,
        )
        if row["status"] != "trialing":
            _conversion_invoice(ctx, row, now=now, trial_end=end, abandoned_ends=abandoned)

    fresh = _re_read(ctx, sub_id)
    body = _serialize(ctx, fresh)
    if not (row["status"] == "trialing" and params.get("trial_end") == "now"):
        # Only the `trial_end="now"` path skips this emission — it fired its
        # own inside `trial_end_now`, against the same serialized pair. Every
        # other combination (including a trialing subscription moving its
        # future trial end) emits here.
        events.emit_event(
            ctx,
            type="customer.subscription.updated",
            obj=body,
            previous=_previous(old_body, body),
        )
    return body


def _previous(old_body: Mapping[str, Any], new_body: Mapping[str, Any]) -> dict[str, Any] | None:
    """The changed keys' prior values, as the engine's update path computes
    them: two SERIALIZED bodies, never a raw row against a body (a raw diff
    would put ISO text, JSON text and the internal `x_seq` on the wire).

    `old_body` must have been serialized BEFORE the change's writes: the
    subscription's `items` envelope is derived from the live table, so
    serializing a pre-change row after the writes would read the new items
    into the old body and the diff would come back empty."""
    from stripeapi.dispatch import resource

    previous = resource._previous_attributes(dict(old_body), dict(new_body))
    return previous or None


def _apply_items_update(
    ctx: seahaven.Ctx, row: Mapping[str, Any], items_param: list[dict[str, Any]]
) -> None:
    """`items[]` on update: an `id` updates that item (price/quantity/
    tax_rates); an entry without one adds an item spanning `[now, the
    subscription's period end)` — the recorded new-item period. An
    itemless subscription (spec-blessed) has no period to align to, so the
    new item spans its own `[now, now + interval)` — the create shape, and
    an honestly-declared unrecorded ruling. No proration lines until
    Phase 14 (declared)."""
    now = ctx.clock.iso()
    sub_period_end = _period_end(ctx, row["id"])
    for index, item in enumerate(items_param):
        if "id" in item:
            sets: dict[str, Any] = {}
            if "price" in item:
                _price_of(ctx, item["price"])
                sets["price"] = item["price"]
            if "quantity" in item:
                sets["quantity"] = item["quantity"]
            if "tax_rates" in item:
                sets["tax_rates"] = _json.dumps(list(item["tax_rates"]))
            if sets:
                assignments = ", ".join(f"{column} = ?" for column in sets)
                ctx.db.execute(
                    f"UPDATE subscription_items SET {assignments} WHERE id = ?",
                    *sets.values(),
                    item["id"],
                )
            continue
        price_row = _price_of(ctx, item["price"])
        _guard_item_addition(ctx, row["id"], price_row, price_param=f"items[{index}][price]")
        period_end = sub_period_end
        if period_end is None:
            interval, interval_count = _interval_of(price_row)
            period_end = invoicing.add_interval(
                now, interval=interval, interval_count=interval_count
            )
        _insert_row(
            ctx,
            "subscription_items",
            {
                "id": _ids.stripe_id(ctx, "si_"),
                "x_seq": _seq.next_seq(ctx, "subscription_items"),
                "created": now,
                "current_period_start": now,
                "current_period_end": period_end,
                "price": price_row["id"],
                "quantity": _default_quantity(item.get("quantity"), price_row),
                "subscription": row["id"],
                "tax_rates": _json.dumps(list(item.get("tax_rates", []))),
            },
        )


def cancel_subscription(
    ctx: seahaven.Ctx,
    sub_id: str,
    *,
    prorate: bool = False,
    invoice_now: bool = False,
    cancellation_details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """`DELETE /v1/subscriptions/{id}`: immediate. A second cancel of the
    same id is the recorded 404 (the canceled row is gone for this path)."""
    row = _re_read(ctx, sub_id)
    if row["status"] in ("canceled", "incomplete_expired"):
        raise resource_missing("subscription", sub_id, param="id")
    now = ctx.clock.iso()
    details = {
        "comment": None,
        "feedback": None,
        "feedback_option": None,
        "reason": "cancellation_requested",
        **dict(cancellation_details or {}),
    }
    ctx.db.execute(
        "UPDATE subscriptions SET status = 'canceled', canceled_at = ?, ended_at = ?,"
        " cancellation_details = ? WHERE id = ?",
        now,
        now,
        _json.dumps(details),
        sub_id,
    )
    # `prorate`/`invoice_now` are accepted and recorded; the credit-only
    # proration item and the final invoice are Phase 14's span (declared) —
    # with no pending items nothing would bill here anyway.
    body = _serialize(ctx, _re_read(ctx, sub_id))
    events.emit_event(ctx, type="customer.subscription.deleted", obj=body)
    return body


def resume_subscription(
    ctx: seahaven.Ctx, sub_id: str, *, billing_cycle_anchor: str = "now"
) -> dict[str, Any]:
    """`POST /v1/subscriptions/{id}/resume` — the recorded park: the status
    STAYS `paused` (with or without a payment method; both probed), a
    SetupIntent is minted, the resume is parked as `pending_update`
    (`expires_at` 23h out), the anchor target is recorded, and the cycle
    invoice is created open and unattempted. Activation happens when the
    SetupIntent confirms (`apply_pending_update_on_seti_success`)."""
    row = _re_read(ctx, sub_id)
    if row["status"] in TERMINAL_STATUSES:
        # Recorded (Phase 12): a canceled subscription is missing for this
        # path, exactly as it is for a second DELETE.
        raise resource_missing("subscription", sub_id, param="id")
    if row["status"] != "paused":
        # Probed verbatim (Phase 12; cassette 12 records only this path's
        # canceled-subscription 404 sibling).
        raise invalid_request("You can only resume a subscription if it is `paused`.")
    old_body = _serialize(ctx, row)
    now = ctx.clock.iso()
    # The anchor the resumed periods restart at: the resume instant for
    # `now` (the default), the stored anchor for `unchanged`.
    anchor_target = now if billing_cycle_anchor == "now" else row["billing_cycle_anchor"]
    # A seti the pause already minted is reused (recorded: the pause body's
    # id is the resume body's id); its row stays usable until confirmed.
    existing = ctx.db.one(
        "SELECT id FROM setup_intents WHERE id = ? AND status IN"
        " ('requires_payment_method', 'requires_confirmation', 'requires_action')",
        row["pending_setup_intent"],
    )
    seti_id = existing["id"] if existing is not None else _mint_resume_setup_intent(ctx, row)
    # The parked update carries the anchor the confirm applies. The
    # cassette's own resume body parks null — the recorded shape varies
    # between recordings (the ad-hoc probe showed a timestamp), and parking
    # what this world will apply is the honest spelling; allow-listed.
    pending_update = {
        "billing_cycle_anchor": _time.to_unix(anchor_target),
        "discount": None,
        "discounts": None,
        "expires_at": _time.to_unix(invoicing.iso_plus_hours(now, hours=23)),
        "metadata": None,
        "subscription_items": None,
        "trial_end": None,
        "trial_from_plan": None,
    }
    ctx.db.execute(
        "UPDATE subscriptions SET pending_setup_intent = ?, pending_update = ? WHERE id = ?",
        seti_id,
        _json.dumps(pending_update),
        sub_id,
    )
    # The resume cycle invoice bills the PARKED-ANCHOR period —
    # `[anchor_target, anchor_target + interval)` per item — not the paused
    # trial span the item rows still carry (they roll only when the
    # confirming SetupIntent applies the parked update). Unprobed on live
    # (the recorded resume body names the invoice but not its lines);
    # aligned with the roll-then-invoice rule every trial-end path follows,
    # and declared here.
    line_inputs = invoicing.build_item_lines(ctx, _items_of(ctx, sub_id), currency=row["currency"])
    rolled: list[invoicing.ItemLine] = []
    for line in line_inputs:
        price_row = _price_of(ctx, line.price_id)
        interval, interval_count = _interval_of(price_row)
        rolled.append(
            replace(
                line,
                period_start=anchor_target,
                period_end=invoicing.add_interval(
                    anchor_target, interval=interval, interval_count=interval_count
                ),
            )
        )
    totals = invoicing.compute_totals(
        rolled,
        currency=row["currency"],
        invoice_discounts=_persisted_discount_specs(ctx, row),
        tax_rates=_persisted_tax_specs(ctx, row),
    )
    if not rolled:
        # An itemless subscription (spec-blessed) resumes with nothing to
        # bill: the park stands, no invoice is minted — the trial-end
        # branches' ruling applied.
        body = _serialize(ctx, _re_read(ctx, sub_id))
        events.emit_event(
            ctx, type="customer.subscription.updated", obj=body, previous=_previous(old_body, body)
        )
        return body
    invoice = invoicing.create_invoice(
        ctx,
        customer_id=row["customer"],
        currency=row["currency"],
        collection_method=row["collection_method"],
        billing_reason="subscription_cycle",
        totals=totals,
        subscription_id=sub_id,
        auto_advance=False,
        days_until_due=row["days_until_due"],
        period_start=anchor_target,
        period_end=rolled[0].period_end,
    )
    if row["collection_method"] != "send_invoice":
        invoicing.finalize_invoice(ctx, invoice["id"])
    _set_latest_invoice(ctx, sub_id, invoice["id"])
    body = _serialize(ctx, _re_read(ctx, sub_id))
    events.emit_event(
        ctx, type="customer.subscription.updated", obj=body, previous=_previous(old_body, body)
    )
    return body


def _mint_resume_setup_intent(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> str:
    """The resume flow's SetupIntent row: off-session, the customer's own,
    awaiting a method (its `payment_method_types` are this world's
    `["card"]` — the recording's `["card", "klarna", "link"]` is the
    account's dashboard configuration, allow-listed)."""
    seti_id = _ids.stripe_id(ctx, "seti_")
    _insert_row(
        ctx,
        "setup_intents",
        {
            "id": seti_id,
            "x_seq": _seq.next_seq(ctx, "setup_intents"),
            "created": ctx.clock.iso(),
            "client_secret": payment_intents._mint_client_secret(ctx, seti_id),
            "customer": row["customer"],
            "status": "requires_payment_method",
            "usage": "off_session",
            "payment_method_types": _json.dumps(["card"]),
            "payment_method_options": _json.dumps(
                {
                    "card": {
                        "mandate_options": None,
                        "network": None,
                        "request_three_d_secure": "automatic",
                    }
                }
            ),
        },
    )
    return seti_id


def apply_pending_update_on_seti_success(ctx: seahaven.Ctx, seti_id: str) -> None:
    """The confirm hook: a succeeded resume SetupIntent applies its parked
    update — pay the open cycle invoice, activate, restart the periods at
    the parked anchor. Live's minted seti reads `canceled` within moments
    (an async artifact this world declares instead of reproducing); the
    documented mechanism — the update applies when the customer completes
    setup — is what this hook implements."""
    row = ctx.db.one("SELECT * FROM subscriptions WHERE pending_setup_intent = ?", seti_id)
    if row is None:
        return
    pending = _load_dict(row["pending_update"])
    anchor = (
        _time.from_unix(pending["billing_cycle_anchor"])
        if pending.get("billing_cycle_anchor") is not None
        else ctx.clock.iso()
    )
    seti = ctx.db.one("SELECT * FROM setup_intents WHERE id = ?", seti_id)
    pm_row = None
    if seti is not None and seti["payment_method"] is not None:
        pm_row = _lookup.require_live_row(
            ctx,
            "payment_methods",
            "PaymentMethod",
            seti["payment_method"],
            param="payment_method",
        )
    invoice_id = row["latest_invoice"]
    invoice = (
        ctx.db.one("SELECT * FROM invoices WHERE id = ?", invoice_id)
        if invoice_id is not None
        else None
    )
    if invoice is not None and invoice["status"] == "open":
        # The method that just confirmed the SetupIntent is the method the
        # customer chose for the resume — it pays the open cycle invoice.
        outcome = invoicing.pay_invoice(ctx, invoice_id, sub_row=row, pm_row=pm_row)
        if outcome["outcome"] != "paid":
            # The parked update waits (it cannot expire under a frozen
            # clock); the open invoice is the customer's to pay.
            return
    # A latest invoice that is not open (an itemless resume leaves the
    # paid $0 trial invoice as latest — nothing was minted to pay) or is
    # absent satisfies the payment step: the parked update simply applies
    # (activate, clear pendings, roll — a no-op with no items).
    ctx.db.execute(
        "UPDATE subscriptions SET status = 'active', billing_cycle_anchor = ?,"
        " pending_setup_intent = NULL, pending_update = NULL WHERE id = ?",
        anchor,
        row["id"],
    )
    _roll_periods_to(ctx, row["id"], anchor)
    fresh = _re_read(ctx, row["id"])
    events.emit_event(ctx, type="customer.subscription.resumed", obj=_serialize(ctx, fresh))
    events.emit_event(
        ctx, type="customer.subscription.pending_update_applied", obj=_serialize(ctx, fresh)
    )


# --- the recovery hook and the unrouted walkers ------------------------------------------


def on_invoice_paid(ctx: seahaven.Ctx, invoice_id: str) -> dict[str, Any] | None:
    """The single recovery hook: a subscription in `incomplete`, `past_due`
    or `unpaid` whose LATEST invoice reached `paid` becomes `active` — no
    `subscriptions.update` required (billing_engine §1, resolved 2026-09-18)."""
    invoice = ctx.db.one("SELECT * FROM invoices WHERE id = ?", invoice_id)
    if invoice is None or invoice["parent_subscription"] is None:
        return None
    row = _re_read(ctx, invoice["parent_subscription"])
    if row["latest_invoice"] != invoice_id:
        return None
    # Any paid invoice consumes the row's parked once-coupons — the
    # trial-end invoice (probed, round 4's trail) and, symmetrically, a
    # renewal or a recovered invoice (round 5's update-path finding).
    _consume_once_discounts(ctx, row["id"])
    if row["status"] not in ("incomplete", "past_due", "unpaid"):
        return None
    old_body = _serialize(ctx, row)
    _set_status(ctx, row["id"], "active")
    fresh = _re_read(ctx, row["id"])
    body = _serialize(ctx, fresh)
    events.emit_event(
        ctx, type="customer.subscription.updated", obj=body, previous=_previous(old_body, body)
    )
    return body


#: The statuses a boundary can roll from: `trialing` ends its trial,
#: `active`/`past_due` renew, `unpaid` renews into drafts. Terminal rows
#: renew nothing ever; `paused` generates no invoices at all (§1's
#: distinction); `incomplete` bills nothing further until paid.
RENEWING_STATUSES = ("active", "past_due", "unpaid", "trialing")


def advance_cycle(ctx: seahaven.Ctx, sub_id: str) -> dict[str, Any]:
    """Roll one billing period at its boundary — fixture-generator and test
    only, never routed: nothing in a frozen-clock instance can reach a
    boundary on its own (billing_engine §7)."""
    row = _re_read(ctx, sub_id)
    if row["status"] not in RENEWING_STATUSES:
        raise seahaven.WorldBug(
            f"advance_cycle on a {row['status']!r} subscription: {sub_id!r} — "
            "terminal, paused and incomplete rows generate no renewal"
        )
    boundary = _period_end(ctx, sub_id)
    if boundary is None or _period_start(ctx, sub_id) is None:
        # the walker's own guard: an itemless subscription has no boundary
        # to roll (routed surfaces answer graceful shapes instead)
        raise seahaven.WorldBug(f"advance_cycle on an itemless subscription: {sub_id!r}")
    if row["status"] == "trialing":
        # Capture before the trial_end stamp: the emitted update's
        # previous_attributes must show the old trial end, which the
        # re-read row below would already have overwritten.
        ctx.db.execute("UPDATE subscriptions SET trial_end = ? WHERE id = ?", boundary, sub_id)
        trial_end_now(
            ctx,
            _re_read(ctx, sub_id),
            old_body=_serialize(ctx, row),
            # The natural boundary bills the cycle; the update-driven end
            # bills `subscription_update` (probed, round 4's trail). This
            # walker's spelling is unprobed — nothing routed reaches it —
            # and the boundary, not the frozen `now`, is the instant.
            billing_reason="subscription_cycle",
            moment=boundary,
        )
        return _re_read(ctx, sub_id)
    if row["cancel_at_period_end"]:
        # The deferred cancel fires: no renewal invoice is generated.
        ctx.db.execute(
            "UPDATE subscriptions SET status = 'canceled', canceled_at = ?, ended_at = ?"
            " WHERE id = ?",
            boundary,
            boundary,
            sub_id,
        )
        events.emit_event(
            ctx,
            type="customer.subscription.deleted",
            obj=_serialize(ctx, _re_read(ctx, sub_id)),
        )
        return _re_read(ctx, sub_id)
    pause_loaded: object = _json.loads(row["pause_collection"])
    pause = pause_loaded if isinstance(pause_loaded, dict) else None
    unpaid = row["status"] == "unpaid"
    # billing_engine §1: a keep_as_draft pause leaves the cycle invoice in
    # `draft` (kept) — never finalized, never attempted, the behavioral
    # overlap with `unpaid`.
    #
    # UNPAID WINS over the pause behavior (billing_engine §1's unpaid row:
    # cycle invoices "stay draft — never finalized, never attempted"): a
    # void/mark_uncollectible pause has nothing to act on, because those
    # behaviors apply to finalized invoices, and an unpaid renewal never
    # becomes one. The draft stands untouched — the keep_as_draft cell
    # exactly — and no `marked_uncollectible_at` stamp exists to write:
    # §2's stamp marks the open -> uncollectible transition, which this
    # cell never performs.
    keep_draft = unpaid or (pause is not None and pause.get("behavior") == "keep_as_draft")
    previous_start = _period_start(ctx, sub_id)
    invoice = _cycle_invoice(
        ctx,
        row,
        billing_reason="subscription_cycle",
        # The unpaid and kept-draft cases never auto-advance.
        auto_advance=not keep_draft,
        finalize=not keep_draft,
        # The renewal bills the interval that just closed, not the instant
        # it was generated at (the create invoice's recorded degenerate
        # period is creation-specific).
        period_start=previous_start,
        period_end=boundary,
    )
    _set_latest_invoice(ctx, sub_id, invoice["id"])
    if keep_draft:
        pass  # the draft stands: unpaid and keep_as_draft never touch it
    elif pause is not None and pause.get("behavior") == "void":
        invoicing.void_invoice(ctx, invoice["id"])
    elif pause is not None and pause.get("behavior") == "mark_uncollectible":
        invoicing.mark_uncollectible_invoice(ctx, invoice["id"])
    elif row["collection_method"] == "charge_automatically":
        outcome = invoicing.pay_invoice(ctx, invoice["id"], sub_row=row)
        if outcome["outcome"] != "paid":
            _set_status(ctx, sub_id, "past_due")
    _roll_periods_to(ctx, sub_id, boundary)
    return _re_read(ctx, sub_id)


def expire_incomplete(ctx: seahaven.Ctx, sub_id: str) -> dict[str, Any]:
    """incomplete -> incomplete_expired at the 23-hour mark — fixture and
    test only (the timer cannot self-fire; billing_engine §7). The open
    invoice is voided and no further invoice is ever generated."""
    row = _re_read(ctx, sub_id)
    if row["status"] != "incomplete":
        raise seahaven.WorldBug(f"expire_incomplete on a {row['status']!r} subscription")
    old_body = _serialize(ctx, row)
    now = ctx.clock.iso()
    if row["latest_invoice"] is not None:
        invoice = _lookup.require_row(
            ctx, "invoices", "invoice", row["latest_invoice"], param="invoice"
        )
        if invoice["status"] == "open":
            invoicing.void_invoice(ctx, invoice["id"])
    ctx.db.execute(
        "UPDATE subscriptions SET status = 'incomplete_expired', ended_at = ? WHERE id = ?",
        now,
        sub_id,
    )
    fresh = _re_read(ctx, sub_id)
    body = _serialize(ctx, fresh)
    events.emit_event(
        ctx, type="customer.subscription.updated", obj=body, previous=_previous(old_body, body)
    )
    return fresh
