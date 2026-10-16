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
            self.customers[member.key] = self.client.create_customer(
                ctx, name=member.name, email=member.email, description=member.description
            )

        return run

    def _default_card(self, ctx: seahaven.Ctx, key: str, customer: str) -> str:
        row = ctx.db.one(
            "SELECT json_extract(invoice_settings, '$.default_payment_method') AS pm"
            " FROM customers WHERE id = ?",
            customer,
        )
        if row is None or row["pm"] is None:
            raise FixtureBuildError(f"{key} has no default card")
        return str(row["pm"])

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
                case OneOffPayment():
                    if beat.card is not None:
                        card, exp = beat.card
                        pm = client.add_card(ctx, customer, card, exp=exp, default=False)
                    else:
                        pm = self._default_card(ctx, key, customer)
                    client.call(
                        ctx,
                        "POST",
                        "/v1/payment_intents",
                        {
                            "amount": beat.amount,
                            "currency": "usd",
                            "customer": customer,
                            "payment_method": pm,
                            "description": beat.description,
                            "confirm": True,
                            "payment_method_types": ["card"],
                        },
                    )

        return run


def schedule(
    tl: Timeline, client: Client, subs: Subscriptions, catalog: Catalog, cast: Sequence[Member]
) -> None:
    """Schedule every member's creation and beats."""
    story = _Story(client, subs, catalog)
    for member in cast:
        tl.add(member.created, f"customer {member.key}", story.create(member))
        for beat in member.beats:
            tl.add(beat.at, f"{member.key}: {type(beat).__name__}", story.beat(member.key, beat))
