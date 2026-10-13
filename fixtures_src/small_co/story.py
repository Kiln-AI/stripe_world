"""The cast as steps (components/small_co.md §3): a customer at each member's `created`, then one
step per beat. Beats at the same instant run in listed order (the timeline breaks ties by
scheduling order)."""

from collections.abc import Callable, Sequence
from typing import Any

import seahaven
from fixtures_src.replay.client import Client
from fixtures_src.replay.subscriptions import Subscriptions
from fixtures_src.replay.timeline import FixtureBuildError, Timeline
from fixtures_src.small_co.cast import (
    Beat,
    BillPending,
    CancelAtPeriodEnd,
    CancelNow,
    Card,
    ChangePlan,
    InvoiceItem,
    Member,
    OneOffPayment,
    Refund,
    Subscribe,
)
from fixtures_src.small_co.catalog import Catalog

from seahaven_stripe_world.resources import disputes

__all__ = ["schedule"]


class _Story:
    def __init__(self, client: Client, subs: Subscriptions, catalog: Catalog) -> None:
        self.client = client
        self.subs = subs
        self.catalog = catalog
        self.customers: dict[str, str] = {}
        self.subscriptions: dict[str, str] = {}

    def create(self, member: Member) -> Callable[[seahaven.Ctx], None]:
        def run(ctx: seahaven.Ctx) -> None:
            params: dict[str, Any] = {"name": member.name, "description": member.description}
            if member.email is not None:
                params["email"] = member.email
            self.customers[member.key] = self.client.call(ctx, "POST", "/v1/customers", params)[
                "id"
            ]

        return run

    def _sub(self, key: str) -> str:
        if key not in self.subscriptions:
            raise FixtureBuildError(f"{key} has no subscription")
        return self.subscriptions[key]

    def beat(self, key: str, beat: Beat) -> Callable[[seahaven.Ctx], None]:
        client = self.client

        def run(ctx: seahaven.Ctx) -> None:
            customer = self.customers[key]
            match beat:
                case Card():
                    client.add_card(ctx, customer, beat.card, exp=beat.exp)
                case Subscribe():
                    sub = client.subscribe(
                        ctx,
                        customer,
                        [{"price": self.catalog.price(beat.price)}],
                        trial_days=beat.trial_days,
                        coupon=beat.coupon,
                    )
                    self.subscriptions[key] = sub["id"]
                    _open_dispute(ctx, customer)
                    self.subs.follow(ctx, sub["id"])
                case ChangePlan():
                    sub_id = self._sub(key)
                    item = ctx.db.one(
                        "SELECT id FROM subscription_items WHERE subscription = ?"
                        " ORDER BY x_seq LIMIT 1",
                        sub_id,
                    )
                    if item is None:
                        raise FixtureBuildError(f"{sub_id} has no items")
                    client.update_subscription(
                        ctx,
                        sub_id,
                        {
                            "items": [{"id": item["id"], "price": self.catalog.price(beat.price)}],
                            "proration_behavior": beat.proration,
                        },
                    )
                    self.subs.follow(ctx, sub_id)
                case CancelAtPeriodEnd():
                    sub_id = self._sub(key)
                    details: dict[str, Any] = {"feedback": beat.feedback}
                    if beat.comment is not None:
                        details["comment"] = beat.comment
                    client.update_subscription(
                        ctx,
                        sub_id,
                        {"cancel_at_period_end": True, "cancellation_details": details},
                    )
                    self.subs.follow(ctx, sub_id)
                case CancelNow():
                    sub_id = self._sub(key)
                    client.cancel_subscription(
                        ctx, sub_id, feedback=beat.feedback, comment=beat.comment
                    )
                    self.subs.follow(ctx, sub_id)
                case Refund():
                    charge = client.latest_charge(ctx, customer)
                    client.refund(ctx, charge["id"], amount=beat.amount)
                case InvoiceItem():
                    client.invoice_item(ctx, customer, self.catalog.price(beat.price))
                case BillPending():
                    invoice = client.call(
                        ctx,
                        "POST",
                        "/v1/invoices",
                        {
                            "customer": customer,
                            "pending_invoice_items_behavior": "include",
                            "auto_advance": False,
                        },
                    )["id"]
                    client.call(ctx, "POST", f"/v1/invoices/{invoice}/finalize")
                    client.pay_invoice(ctx, invoice)
                case OneOffPayment():
                    row = ctx.db.one(
                        "SELECT json_extract(invoice_settings, '$.default_payment_method') AS pm"
                        " FROM customers WHERE id = ?",
                        customer,
                    )
                    if row is None or row["pm"] is None:
                        raise FixtureBuildError(f"{key} has no default card")
                    client.call(
                        ctx,
                        "POST",
                        "/v1/payment_intents",
                        {
                            "amount": beat.amount,
                            "currency": "usd",
                            "customer": customer,
                            "payment_method": row["pm"],
                            "description": beat.description,
                            "confirm": True,
                            "payment_method_types": ["card"],
                        },
                    )

        return run


def _open_dispute(ctx: seahaven.Ctx, customer: str) -> None:
    """Open the dispute a dispute-tagged card's first subscription payment carries.

    The world opens it on the payment-intent confirm path only; an invoice paid through a
    subscription does not reach `maybe_create_dispute`. The step calls that same world function
    at the same instant, right after the paying call, so Grace's dispute exists as the card says.
    """
    charge = ctx.db.one(
        "SELECT * FROM charges WHERE customer = ? AND status = 'succeeded' AND NOT disputed"
        " ORDER BY x_seq DESC LIMIT 1",
        customer,
    )
    if charge is None or charge["payment_method"] is None:
        return
    pm = ctx.db.one("SELECT * FROM payment_methods WHERE id = ?", charge["payment_method"])
    if pm is not None:
        disputes.maybe_create_dispute(ctx, charge, pm)


def schedule(
    tl: Timeline, client: Client, subs: Subscriptions, catalog: Catalog, cast: Sequence[Member]
) -> None:
    """Schedule every member's creation and beats."""
    story = _Story(client, subs, catalog)
    for member in cast:
        tl.add(member.created, f"customer {member.key}", story.create(member))
        for beat in member.beats:
            tl.add(beat.at, f"{member.key}: {type(beat).__name__}", story.beat(member.key, beat))
