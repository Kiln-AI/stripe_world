"""The catalog steps and the lives of the population (components/big_co.md §2, §4).

`Lives` is the subscription driver's `Hooks`: at each boundary it draws what happens to a
subscription over the coming period and schedules it. Every scheduled step re-reads what it acts
on when it runs and does nothing when it no longer applies. Every API step goes through
`Client.call`, so an answer the story did not expect stops the build; the one expected failure is
a card payment on a card that declines.
"""

import math
import random
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import seahaven
from fixtures_src.big_co.population import NOW, Prospect
from fixtures_src.big_co.shape import CATALOG, SERVICES, TAX_RATES, Shape
from fixtures_src.replay import cards
from fixtures_src.replay.cards import Card
from fixtures_src.replay.client import Client
from fixtures_src.replay.clock import plus
from fixtures_src.replay.subscriptions import Subscriptions, finalize_when_due
from fixtures_src.replay.timeline import FixtureBuildError, Timeline

from seahaven_stripe_world import _json, _time
from seahaven_stripe_world.billing import invoicing

__all__ = ["SLA_MEMO", "Catalog", "Lives", "business_days"]

Run = Callable[[seahaven.Ctx], None]

_ALIVE = ("active", "past_due", "trialing", "unpaid")
_NEXT_TIER = {"starter": "pro", "pro": "business"}
_PREV_TIER = {"pro": "starter", "business": "pro"}
_FEEDBACK = ("too_expensive", "switched_service", "missing_features", "unused", "customer_service")
_DISPUTE_CARDS = {
    "fraudulent": cards.DISPUTE_FRAUDULENT,
    "not_received": cards.DISPUTE_NOT_RECEIVED,
    "inquiry": cards.DISPUTE_INQUIRY,
}
_ASSOCIATIONS = (
    ("MSPG10", "Mountain States Plumbing Guild: 10% off"),
    ("FRHA10", "Front Range HVAC Alliance: 10% off"),
    ("RMEC10", "Rocky Mountain Electrical Contractors: 10% off"),
)
_RAMP_DAYS = 90
_CONCESSIONS_FROM = "2025-06-01T00:00:00.000Z"
_CONCESSION_DRAW = 0.02
# The chance a Pro or Business subscription at its last boundary before NOW is picked for a
# scheduled downgrade; the picks stop at `Shape.scheduled_downgrades`.
_DOWNGRADE_DRAW = 0.05
SLA_SELECT_AT = "2026-04-20T15:00:00.000Z"
SLA_MEMO = "Service credit: April 14 outage"


def _between(rng: random.Random, start: str, end: str) -> str:
    a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
    if b <= a:
        return start
    return seahaven.Clock(a + (b - a) * rng.random()).iso()


def _poisson(rng: random.Random, mean: float) -> int:
    limit, k, p = math.exp(-mean), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _months(iso: str, n: int) -> str:
    return invoicing.add_interval(iso, interval="month", interval_count=n)


def business_days() -> Iterator[str]:
    """Monday to Friday at 15:00Z, 2025-03-05 to 2026-08-31 (§4.8)."""
    day = datetime(2025, 3, 5, 15, tzinfo=UTC)
    last = datetime(2026, 8, 31, 15, tzinfo=UTC)
    while day <= last:
        if day.weekday() < 5:
            yield seahaven.Clock(day).iso()
        day += timedelta(days=1)


class Catalog:
    """Products, prices, tax rates and coupons, made at their instants (§2)."""

    def __init__(self) -> None:
        self.products: dict[str, str] = {}
        self.prices: dict[str, str] = {}
        self.tax: dict[str, str] = {}
        self.tier_of_price: dict[str, str] = {}

    def schedule(self, tl: Timeline, client: Client) -> None:
        tl.add("2025-02-11T16:00:00.000Z", "catalog", lambda ctx: self._products(ctx, client))
        tl.add("2025-02-11T16:30:00.000Z", "tax rates", lambda ctx: self._tax(ctx, client))
        tl.add("2025-03-08T16:00:00.000Z", "archive legacy", lambda ctx: self._archive(ctx, client))
        tl.add("2025-04-01T15:00:00.000Z", "SWITCH25", lambda ctx: self._switch25(ctx, client))
        tl.add("2025-05-15T15:00:00.000Z", "associations", lambda ctx: self._assoc(ctx, client))

    def _products(self, ctx: seahaven.Ctx, client: Client) -> None:
        for spec in CATALOG:
            product = client.call(
                ctx, "POST", "/v1/products", {"name": spec.name, "description": spec.description}
            )
            self.products[spec.key] = product["id"]
            for lookup_key, amount, interval in spec.prices:
                params: dict[str, Any] = {
                    "product": product["id"],
                    "unit_amount": amount,
                    "currency": "usd",
                    "lookup_key": lookup_key,
                }
                if interval is not None:
                    params["recurring"] = {"interval": interval}
                price = client.call(ctx, "POST", "/v1/prices", params)
                self.prices[lookup_key] = price["id"]
                self.tier_of_price[price["id"]] = lookup_key.removeprefix("legacy_").split("_")[0]

    def _tax(self, ctx: seahaven.Ctx, client: Client) -> None:
        for state, _name, percentage in TAX_RATES:
            rate = client.call(
                ctx,
                "POST",
                "/v1/tax_rates",
                {
                    "display_name": "Sales Tax",
                    "jurisdiction": state,
                    "state": state,
                    "country": "US",
                    "tax_type": "sales_tax",
                    # Every rate here is exact in binary floating point (6.25, 6.5, 5.75, ...).
                    "percentage": float(percentage),
                    "inclusive": False,
                },
            )
            self.tax[state] = rate["id"]

    def _archive(self, ctx: seahaven.Ctx, client: Client) -> None:
        for key in ("legacy_starter_monthly", "legacy_pro_monthly"):
            client.call(ctx, "POST", f"/v1/prices/{self.prices[key]}", {"active": False})
        for key in ("legacy_starter", "legacy_pro"):
            client.call(ctx, "POST", f"/v1/products/{self.products[key]}", {"active": False})

    def _switch25(self, ctx: seahaven.Ctx, client: Client) -> None:
        client.call(
            ctx,
            "POST",
            "/v1/coupons",
            {
                "id": "SWITCH25",
                "percent_off": 25.0,
                "duration": "repeating",
                "duration_in_months": 3,
                "name": "Competitive switch: 25% off 3 months",
            },
        )

    def _assoc(self, ctx: seahaven.Ctx, client: Client) -> None:
        for coupon_id, name in _ASSOCIATIONS:
            client.call(
                ctx,
                "POST",
                "/v1/coupons",
                {"id": coupon_id, "percent_off": 10.0, "duration": "forever", "name": name},
            )


class Lives:
    """Each prospect's signup and life, and the driver's hooks (§4)."""

    def __init__(
        self, timeline: Timeline, client: Client, catalog: Catalog, rng: random.Random, shape: Shape
    ) -> None:
        self.tl = timeline
        self.client = client
        self.catalog = catalog
        self.rng = rng
        self.shape = shape
        self.subs: Subscriptions | None = None
        self.prospect: dict[str, Prospect] = {}  # subscription id -> prospect
        self.customer: dict[str, str] = {}  # prospect key -> customer id
        self.card: dict[str, Card | None] = {}  # customer id -> default card
        self.pm: dict[str, str] = {}  # customer id -> default payment method id
        self.seat_item: dict[str, str] = {}  # subscription id -> its seat item
        self.tier: dict[str, str] = {}  # subscription id -> current tier
        self.pay_open: dict[str, list[str]] = {}  # scripted key -> payment instants
        self.concessions = 0
        self.downgrades = 0  # scheduled downgrades made
        self.downgrades_pending = 0  # picked, their step not yet run

    def _driver(self) -> Subscriptions:
        if self.subs is None:
            raise FixtureBuildError("Lives.subs is not set")
        return self.subs

    # --- signup (§4) --------------------------------------------------------------------------

    def schedule(self, prospects: Sequence[Prospect]) -> None:
        for p in prospects:
            self.tl.add(p.created, f"signup {p.key}", self._signup(p))

    def _signup(self, p: Prospect) -> Run:
        def run(ctx: seahaven.Ctx) -> None:
            params: dict[str, Any] = {"name": p.company, "address": dict(p.address)}
            for key, value in (
                ("email", p.email),
                ("phone", p.phone),
                ("description", p.description),
            ):
                if value is not None:
                    params[key] = value
            if p.metadata:
                params["metadata"] = dict(p.metadata)
            if p.tax_exempt:
                params["tax_exempt"] = "exempt"
            customer = self.client.call(ctx, "POST", "/v1/customers", params)["id"]
            self.customer[p.key] = customer
            self.card[customer] = None
            if p.card is not None and p.card_exp is not None:
                self._add_card(ctx, customer, p.card, p.card_exp)
            if p.subscribes:
                seat_price = self._seat_price(ctx, p)
                ramp = next((beat[1:] for beat in p.script if beat[0] == "ramp"), None)
                recent_deal = p.tier == "enterprise" and p.created >= plus(NOW, days=-_RAMP_DAYS)
                if ramp is None and recent_deal and not p.scripted:
                    ramp = (
                        (p.seats, _months(p.created, 3)),
                        (round(p.seats * 1.5), _months(p.created, 6)),
                        (p.seats * 2, _months(p.created, 12)),
                    )
                sub_id = self._subscribe(ctx, p, customer, seat_price)
                if ramp is not None:
                    self._ramp(ctx, p, customer, sub_id, seat_price, ramp)
                self._track(ctx, p, sub_id)
            if p.onboarding is not None:
                self.tl.add(
                    plus(p.created, hours=2),
                    f"onboarding {p.key}",
                    self._onboarding(p, p.onboarding),
                )
            for beat in p.script:
                self._beat(p, beat)
            if p.scripted or not p.subscribes:
                return
            if p.trial == "card" and not p.converts:
                when = _between(self.rng, plus(p.created, days=3), plus(p.created, days=13))
                self.tl.add(when, f"trial lapses {p.key}", self._cancel_key(p.key, "unused"))
            if p.trial == "cardless" and p.converts:
                when = _between(self.rng, plus(p.created, days=2), plus(p.created, days=13))
                self.tl.add(when, f"card added {p.key}", self._card_step(p.key, None))

        return run

    def _add_card(self, ctx: seahaven.Ctx, customer: str, card: Card, exp: tuple[int, int]) -> None:
        """A new default card."""
        self.pm[customer] = self.client.add_card(ctx, customer, card, exp=exp)
        self.card[customer] = card

    def _seat_price(self, ctx: seahaven.Ctx, p: Prospect) -> str:
        if p.tier == "enterprise":
            if p.enterprise_rate is None:
                raise FixtureBuildError(f"{p.key}: an Enterprise prospect without a rate")
            return self.client.call(
                ctx,
                "POST",
                "/v1/prices",
                {
                    "product": self.catalog.products["enterprise"],
                    "unit_amount": p.enterprise_rate * 12,
                    "currency": "usd",
                    "recurring": {"interval": "year"},
                    "nickname": f"Enterprise — {p.company} {p.created[:4]}",
                },
            )["id"]
        if p.legacy:
            return self.catalog.prices[f"legacy_{p.tier}_monthly"]
        return self.catalog.prices[f"{p.tier}_{_suffix(p)}"]

    def _items(self, p: Prospect, seat_price: str, seats: int) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = [{"price": seat_price, "quantity": seats}]
        if p.vehicles:
            items.append(
                {"price": self.catalog.prices[f"fleet_{_suffix(p)}"], "quantity": p.vehicles}
            )
        if p.sms:
            items.append({"price": self.catalog.prices[f"sms_{_suffix(p)}"]})
        return items

    def _tax_rates(self, p: Prospect) -> list[str]:
        state = str(p.address.get("state"))
        if p.tax_exempt or state not in self.catalog.tax:
            return []
        return [self.catalog.tax[state]]

    def _subscribe(self, ctx: seahaven.Ctx, p: Prospect, customer: str, seat_price: str) -> str:
        invoiced = p.tier == "enterprise"
        params: dict[str, Any] = {
            "customer": customer,
            "items": self._items(p, seat_price, p.seats),
            "collection_method": "send_invoice" if invoiced else "charge_automatically",
        }
        if invoiced:
            params["days_until_due"] = 30
        if rates := self._tax_rates(p):
            params["default_tax_rates"] = rates
        if p.coupon is not None:
            params["discounts"] = [{"coupon": p.coupon}]
        if p.trial in ("card", "cardless"):
            params["trial_period_days"] = 14
        elif p.trial == "migration":
            if p.trial_end is None:
                raise FixtureBuildError(f"{p.key}: a migrated prospect without a renewal date")
            params["trial_end"] = _time.to_unix(p.trial_end)
        if p.trial != "none":
            # An invoiced (send_invoice) contract needs no card at its trial end.
            behavior = "create_invoice" if invoiced else "pause"
            params["trial_settings"] = {"end_behavior": {"missing_payment_method": behavior}}
        return str(self.client.call(ctx, "POST", "/v1/subscriptions", params)["id"])

    def _ramp(
        self,
        ctx: seahaven.Ctx,
        p: Prospect,
        customer: str,
        sub_id: str,
        seat_price: str,
        phases: Sequence[tuple[int, str]],
    ) -> None:
        """The ramp schedule of §4.5, in its first phase.

        big_co.md has the schedule create the subscription. This world's schedule routes declare
        no `days_until_due` (neither in `default_settings` nor in a phase), and a `send_invoice`
        subscription needs one, so a schedule cannot create the contract. The deal's subscription
        is made first (`send_invoice`, 30 days) and the schedule takes it over with
        `from_subscription`, then gets the three phases: the same state, through the API.
        """
        schedule = self.client.call(
            ctx,
            "POST",
            "/v1/subscription_schedules",
            {"customer": customer, "from_subscription": sub_id},
        )["id"]
        start = p.created
        rates = self._tax_rates(p)
        payload: list[dict[str, Any]] = []
        for seats, end in phases:
            phase: dict[str, Any] = {
                "items": self._items(p, seat_price, seats),
                "start_date": _time.to_unix(start),
                "end_date": _time.to_unix(end),
            }
            if rates:
                phase["default_tax_rates"] = rates
            payload.append(phase)
            start = end
        self.client.call(
            ctx,
            "POST",
            f"/v1/subscription_schedules/{schedule}",
            {
                "end_behavior": "release",
                "default_settings": {"collection_method": "send_invoice"},
                "phases": payload,
                "metadata": {"deal": "ramp"},
            },
        )

    def _track(self, ctx: seahaven.Ctx, p: Prospect, sub_id: str) -> None:
        self.prospect[sub_id] = p
        self.tier[sub_id] = p.tier
        item = ctx.db.one(
            "SELECT id FROM subscription_items WHERE subscription = ? ORDER BY x_seq LIMIT 1",
            sub_id,
        )
        if item is not None:
            self.seat_item[sub_id] = str(item["id"])
        self._driver().follow(ctx, sub_id)
        if not p.scripted:
            self._draw(ctx, sub_id, 0)

    def _onboarding(self, p: Prospect, price_key: str) -> Run:
        """Sales invoices the onboarding package; it finalizes on its own and is paid (§4.3)."""

        def run(ctx: seahaven.Ctx) -> None:
            customer = self.customer[p.key]
            self.client.invoice_item(ctx, customer, self.catalog.prices[price_key])
            invoice = self.client.call(
                ctx,
                "POST",
                "/v1/invoices",
                {
                    "customer": customer,
                    "collection_method": "send_invoice",
                    "days_until_due": 30,
                    "pending_invoice_items_behavior": "include",
                    "auto_advance": True,
                    "description": "FieldOps onboarding package",
                },
            )
            finalize_when_due(
                self.tl, self.client, invoice, lambda c, inv: self._invoice_opened(c, p, inv)
            )

        return run

    # --- scripted beats (§5) ------------------------------------------------------------------

    def _beat(self, p: Prospect, beat: tuple[Any, ...]) -> None:
        kind = beat[0]
        if kind == "card":
            self.tl.add(beat[1], f"card {p.key}", self._card_step(p.key, beat[2]))
        elif kind == "disputed_service":
            self.tl.add(
                beat[1],
                f"disputed service {p.key}",
                self._service(p.key, beat[2], beat[3], course_for=None),
            )
        elif kind == "evidence":
            self.tl.add(beat[1], f"evidence {p.key}", self._evidence_latest(p.key, beat[2]))
        elif kind == "accept_dispute":
            self.tl.add(beat[1], f"accept dispute {p.key}", self._accept_latest(p.key))
        elif kind == "cancel_at_period_end":
            self.tl.add(beat[1], f"cancel at end {p.key}", self._cancel_at_end(p.key, beat[2]))
        elif kind == "onboarding":
            self.tl.add(beat[1], f"onboarding {p.key}", self._onboarding(p, beat[2]))
        elif kind == "pay_open":
            self.pay_open.setdefault(p.key, []).append(beat[1])
        elif kind != "ramp":
            raise FixtureBuildError(f"{p.key}: unknown beat {kind!r}")

    def _latest_sub(self, ctx: seahaven.Ctx, key: str) -> dict[str, Any] | None:
        customer = self.customer.get(key)
        if customer is None:
            return None
        row = ctx.db.one(
            "SELECT id, status FROM subscriptions WHERE customer = ? ORDER BY x_seq DESC LIMIT 1",
            customer,
        )
        return None if row is None else dict(row)

    def _card_step(self, key: str, card: Card | None) -> Run:
        """A new default card: `card`, or a good one drawn when the step runs."""

        def run(ctx: seahaven.Ctx) -> None:
            chosen = card if card is not None else cards.pick_good(self.rng)
            self._add_card(ctx, self.customer[key], chosen, cards.expiry(self.rng, NOW))

        return run

    def _latest_dispute(self, ctx: seahaven.Ctx, key: str) -> str:
        row = ctx.db.one(
            "SELECT d.id FROM disputes d JOIN charges c ON c.id = d.charge"
            " WHERE c.customer = ? ORDER BY d.x_seq DESC LIMIT 1",
            self.customer[key],
        )
        if row is None:
            raise FixtureBuildError(f"{key}: no dispute to answer")
        return str(row["id"])

    def _evidence_latest(self, key: str, text: str) -> Run:
        return lambda ctx: self._evidence(ctx, self._latest_dispute(ctx, key), text)

    def _accept_latest(self, key: str) -> Run:
        return lambda ctx: self._close(self._latest_dispute(ctx, key))(ctx)

    def _cancel_at_end(self, key: str, feedback: str) -> Run:
        def run(ctx: seahaven.Ctx) -> None:
            sub = self._latest_sub(ctx, key)
            if sub is None or sub["status"] != "active":
                raise FixtureBuildError(f"{key}: no active subscription to cancel at period end")
            self._set_cancel_at_end(ctx, str(sub["id"]), feedback)

        return run

    # --- the driver's hooks (§4.1) ------------------------------------------------------------

    def before_boundary(self, ctx: seahaven.Ctx, sub_id: str, n: int) -> None:
        pass

    def after_boundary(self, ctx: seahaven.Ctx, sub_id: str, n: int) -> None:
        p = self.prospect.get(sub_id)
        if p is not None and not p.scripted:
            self._draw(ctx, sub_id, n)

    def on_open_invoice(self, ctx: seahaven.Ctx, sub_id: str, invoice_id: str) -> None:
        p = self.prospect.get(sub_id)
        if p is not None:
            self._invoice_opened(ctx, p, invoice_id)

    def on_retry(self, ctx: seahaven.Ctx, sub_id: str, invoice_id: str, attempt: int) -> bool:
        """The driver pays when the customer has replaced the declining card."""
        p = self.prospect.get(sub_id)
        if p is None:
            return False
        return self.card.get(self.customer[p.key]) in cards.GOOD

    def on_status_change(self, ctx: seahaven.Ctx, sub_id: str, old: str, new: str) -> None:
        if new == "unpaid":
            when = plus(ctx.clock.iso(), days=self.shape.unpaid_cancel_after_days)
            self.tl.add(
                when,
                f"close unpaid {sub_id}",
                self._cancel_sub(sub_id, "customer_service", "Unpaid: closed by customer success"),
            )

    # --- disputes (§4.2) ----------------------------------------------------------------------

    def _service(
        self, key: str, card: Card | None, service: tuple[str, int], *, course_for: str | None
    ) -> Run:
        """A one-off services payment, confirmed on the spot through `POST /v1/payment_intents`.

        With `card`, a dispute test card the customer adds for this payment: the world opens its
        dispute as the payment confirms, which is the only place it opens a card's dispute. With
        `course_for` (the customer's subscription), the dispute's course is then drawn (§4.2);
        scripted disputes have theirs as beats. Without `card`, the card on file pays, when it
        is a good one.
        """

        def run(ctx: seahaven.Ctx) -> None:
            customer = self.customer[key]
            if card is None:
                pm = self.pm.get(customer)
                if pm is None or self.card.get(customer) not in cards.GOOD:
                    return
            else:
                exp = cards.expiry(self.rng, NOW)
                pm = self.client.add_card(ctx, customer, card, exp=exp, default=False)
            description, amount = service
            intent = self.client.call(
                ctx,
                "POST",
                "/v1/payment_intents",
                {
                    "amount": amount,
                    "currency": "usd",
                    "customer": customer,
                    "payment_method": pm,
                    "confirm": True,
                    "off_session": True,
                    "description": description,
                },
            )
            if card is None:
                return
            dispute = ctx.db.one(
                "SELECT id, reason FROM disputes WHERE payment_intent = ?", intent["id"]
            )
            if dispute is None:
                raise FixtureBuildError(f"card {card.number} opened no dispute on {intent['id']}")
            if course_for is not None:
                self._dispute_course(ctx, dispute, course_for)

        return run

    def _dispute_course(self, ctx: seahaven.Ctx, dispute: dict[str, Any], sub_id: str) -> None:
        """Decide a new dispute's course by its age at NOW (§4.2)."""
        now, rng, shape = ctx.clock.iso(), self.rng, self.shape
        dispute_id = str(dispute["id"])
        if now >= plus(NOW, days=-shape.dispute_fresh_days):
            if rng.random() < shape.fresh_evidence_share:
                when = plus(now, days=rng.randint(1, 5))
                self.tl.add(
                    when, f"evidence {dispute_id}", self._submit(dispute_id, "See attached.")
                )
        elif rng.random() < shape.settled_won_share:
            when = plus(now, days=rng.randint(3, 10))
            self.tl.add(when, f"won {dispute_id}", self._submit(dispute_id, "winning_evidence"))
        elif rng.random() < 0.5:
            when = plus(now, days=rng.randint(2, 10))
            self.tl.add(when, f"accept {dispute_id}", self._close(dispute_id))
        else:
            when = plus(now, days=rng.randint(3, 10))
            self.tl.add(when, f"lost {dispute_id}", self._submit(dispute_id, "losing_evidence"))
        if dispute["reason"] == "fraudulent" and rng.random() < shape.fraud_dispute_cancels:
            when = plus(now, days=rng.randint(0, 3), hours=3)
            self.tl.add(
                when,
                f"fraud cancel {sub_id}",
                self._cancel_sub(sub_id, "other", "Disputed as fraudulent"),
            )

    def _evidence(self, ctx: seahaven.Ctx, dispute: str, text: str) -> None:
        self.client.call(
            ctx,
            "POST",
            f"/v1/disputes/{dispute}",
            {"evidence": {"uncategorized_text": text}, "submit": True},
        )

    def _submit(self, dispute: str, text: str) -> Run:
        return lambda ctx: self._evidence(ctx, dispute, text)

    def _close(self, dispute: str) -> Run:
        def run(ctx: seahaven.Ctx) -> None:
            self.client.call(ctx, "POST", f"/v1/disputes/{dispute}/close")

        return run

    # --- open invoices (§4.3) -----------------------------------------------------------------

    def _invoice_opened(self, ctx: seahaven.Ctx, p: Prospect, invoice_id: str) -> None:
        now = ctx.clock.iso()
        if p.scripted:
            queue = self.pay_open.get(p.key)
            if queue:
                self.tl.add(queue.pop(0), f"pay {invoice_id}", self._pay(p, invoice_id, False))
            return
        if p.pays_by_card and p.card is not None:
            when = plus(now, days=self.rng.randint(5, 28), hours=self.rng.randint(0, 6))
            self.tl.add(when, f"pay {invoice_id}", self._pay(p, invoice_id, False))
        else:
            when = plus(now, days=self.rng.randint(3, 35), hours=self.rng.randint(0, 6))
            self.tl.add(when, f"paid by check {invoice_id}", self._pay(p, invoice_id, True))

    def _pay(self, p: Prospect, invoice_id: str, out_of_band: bool) -> Run:
        def run(ctx: seahaven.Ctx) -> None:
            row = ctx.db.one("SELECT status FROM invoices WHERE id = ?", invoice_id)
            if row is None or row["status"] != "open":
                return  # paid, voided or uncollectible meanwhile
            customer = self.customer[p.key]
            if out_of_band:
                self.client.pay_invoice(ctx, invoice_id, out_of_band=True)
                return
            if self.card.get(customer) is cards.DECLINING:
                # The one expected failure: the card on file declines and the invoice stays open.
                response = self.client.attempt(ctx, "POST", f"/v1/invoices/{invoice_id}/pay")
                if response.status != 402:
                    raise FixtureBuildError(
                        f"paying {invoice_id} on a declining card answered {response.status}"
                    )
                return
            self.client.pay_invoice(ctx, invoice_id)

        return run

    # --- the boundary draws (§4.1) ------------------------------------------------------------

    def _draw(self, ctx: seahaven.Ctx, sub_id: str, n: int) -> None:
        """What happens to `sub_id` over the period that starts now."""
        row = ctx.db.one(
            "SELECT s.status, s.collection_method, s.customer, s.latest_invoice, s.schedule,"
            " s.cancel_at_period_end, MIN(i.current_period_end) AS end_at"
            " FROM subscriptions s JOIN subscription_items i ON i.subscription = s.id"
            " WHERE s.id = ?",
            sub_id,
        )
        if row is None or row["status"] != "active" or row["end_at"] is None:
            return
        if row["schedule"] is not None:
            return  # a ramp or a scheduled downgrade: the deal is set
        rng, shape, p = self.rng, self.shape, self.prospect[sub_id]
        now, end = ctx.clock.iso(), str(row["end_at"])
        months = 12 if p.interval == "year" else 1
        tier = self.tier[sub_id]
        by_card = row["collection_method"] == "charge_automatically"

        if n > 0:
            self._settle_draws(ctx, sub_id, row, by_card)
        if row["cancel_at_period_end"]:
            return

        if tier == "enterprise":
            churn = shape.enterprise_nonrenewal
        elif months == 12:
            churn = shape.annual_nonrenewal
        else:
            churn = shape.monthly_churn[tier]
        if rng.random() < churn:
            when = _between(rng, now, end)
            if rng.random() < shape.churn_at_period_end_share:
                self.tl.add(when, f"churn {sub_id}", self._churn_at_end(sub_id))
            else:
                feedback = rng.choice(_FEEDBACK)
                self.tl.add(when, f"cancel {sub_id}", self._cancel_sub(sub_id, feedback, None))
            return

        for _ in range(_poisson(rng, shape.seat_change_monthly * months)):
            up = rng.random() < shape.seat_up_share
            self.tl.add(_between(rng, now, end), f"seats {sub_id}", self._seats(sub_id, up))
        if tier in _NEXT_TIER and rng.random() < shape.upgrade_monthly * months:
            self.tl.add(
                _between(rng, now, end), f"upgrade {sub_id}", self._retier(sub_id, _NEXT_TIER)
            )
        elif tier in _PREV_TIER and rng.random() < shape.immediate_downgrade_monthly * months:
            self.tl.add(
                _between(rng, now, end), f"downgrade {sub_id}", self._retier(sub_id, _PREV_TIER)
            )

        if by_card:
            customer = str(row["customer"])
            roll = rng.random()
            if roll < shape.decline_per_renewal:
                swap = plus(end, days=-rng.randint(1, 5), hours=-rng.randint(0, 6))
                if swap > now:
                    self.tl.add(
                        swap, f"declining card {sub_id}", self._swap(customer, cards.DECLINING)
                    )
                    if rng.random() < shape.decline_recovers:
                        fix = plus(end, days=rng.randint(1, 10), hours=rng.randint(1, 8))
                        self.tl.add(fix, f"new card {sub_id}", self._swap(customer, None))
            elif roll < shape.decline_per_renewal + shape.dispute_per_charge:
                kinds = list(shape.dispute_mix)
                kind = rng.choices(kinds, weights=[shape.dispute_mix[k] for k in kinds])[0]
                service = self._service(
                    p.key, _DISPUTE_CARDS[kind], rng.choice(SERVICES), course_for=sub_id
                )
                self.tl.add(_between(rng, now, end), f"disputed service {sub_id}", service)
            if rng.random() < shape.services_per_period:
                service = self._service(p.key, None, rng.choice(SERVICES), course_for=None)
                self.tl.add(_between(rng, now, end), f"service {sub_id}", service)

        if (
            self.concessions < shape.concessions
            and n > 0
            and now >= _CONCESSIONS_FROM
            and p.coupon is None
            and rng.random() < _CONCESSION_DRAW
        ):
            self.concessions += 1
            self.tl.add(_between(rng, now, end), f"concession {sub_id}", self._concession(sub_id))

        if (
            self.downgrades + self.downgrades_pending < shape.scheduled_downgrades
            and n > 0
            and end > NOW
            and tier in _PREV_TIER
            and rng.random() < _DOWNGRADE_DRAW
        ):
            # This is the subscription's last boundary before NOW (its period runs past it). A
            # pick whose step finds the subscription changed frees its place for a later one.
            self.downgrades_pending += 1
            self.tl.add(
                _between(rng, now, NOW),
                f"schedule downgrade {sub_id}",
                self._schedule_downgrade(sub_id),
            )

    def _settle_draws(
        self, ctx: seahaven.Ctx, sub_id: str, row: dict[str, Any], by_card: bool
    ) -> None:
        """Refund and credit-note draws on the invoice this boundary just made."""
        rng, shape, now = self.rng, self.shape, ctx.clock.iso()
        invoice = ctx.db.one(
            "SELECT id, status, total, _charge FROM invoices WHERE id = ?", row["latest_invoice"]
        )
        if invoice is None or invoice["status"] != "paid" or int(invoice["total"]) <= 0:
            return
        if by_card and rng.random() < shape.refund_per_charge:
            charge = ctx.db.one(
                "SELECT id, amount FROM charges WHERE id = ? AND status = 'succeeded'"
                " AND NOT disputed",
                invoice["_charge"],
            )
            if charge is not None:
                amount = None
                if rng.random() < shape.refund_partial_share:
                    amount = int(int(charge["amount"]) * rng.uniform(0.10, 0.50))
                reason = rng.choice(("requested_by_customer", "duplicate"))
                when = plus(now, days=rng.randint(1, 14), hours=rng.randint(0, 8))
                self.tl.add(
                    when, f"refund {charge['id']}", self._refund(str(charge["id"]), amount, reason)
                )
        elif rng.random() < shape.credit_note_per_invoice:
            amount = max(1, int(int(invoice["total"]) * rng.uniform(0.05, 1.0)))
            reason = rng.choice(("order_change", "duplicate"))
            when = plus(now, days=rng.randint(1, 20), hours=rng.randint(0, 8))
            self.tl.add(
                when,
                f"credit note {invoice['id']}",
                self._credit_note(str(invoice["id"]), amount, reason, None),
            )

    # --- the drawn steps ----------------------------------------------------------------------

    def _active(self, ctx: seahaven.Ctx, sub_id: str) -> bool:
        """Active, not already ending, and not governed by a schedule."""
        row = ctx.db.one(
            "SELECT status, cancel_at_period_end, schedule FROM subscriptions WHERE id = ?", sub_id
        )
        return (
            row is not None
            and row["status"] == "active"
            and not row["cancel_at_period_end"]
            and row["schedule"] is None
        )

    def _set_cancel_at_end(self, ctx: seahaven.Ctx, sub_id: str, feedback: str) -> None:
        self.client.update_subscription(
            ctx,
            sub_id,
            {"cancel_at_period_end": True, "cancellation_details": {"feedback": feedback}},
        )
        self._driver().follow(ctx, sub_id)

    def _churn_at_end(self, sub_id: str) -> Run:
        def run(ctx: seahaven.Ctx) -> None:
            if self._active(ctx, sub_id):
                self._set_cancel_at_end(ctx, sub_id, self.rng.choice(_FEEDBACK))

        return run

    def _cancel_key(self, key: str, feedback: str) -> Run:
        def run(ctx: seahaven.Ctx) -> None:
            sub = self._latest_sub(ctx, key)
            if sub is not None:
                self._cancel_now(ctx, str(sub["id"]), feedback, None)

        return run

    def _cancel_now(
        self, ctx: seahaven.Ctx, sub_id: str, feedback: str, comment: str | None
    ) -> None:
        row = ctx.db.one("SELECT status FROM subscriptions WHERE id = ?", sub_id)
        if row is None or row["status"] not in _ALIVE:
            return
        self.client.cancel_subscription(ctx, sub_id, feedback=feedback, comment=comment)
        self._driver().follow(ctx, sub_id)

    def _cancel_sub(self, sub_id: str, feedback: str, comment: str | None) -> Run:
        return lambda ctx: self._cancel_now(ctx, sub_id, feedback, comment)

    def _seats(self, sub_id: str, up: bool) -> Run:
        def run(ctx: seahaven.Ctx) -> None:
            item_id = self.seat_item.get(sub_id)
            if item_id is None or not self._active(ctx, sub_id):
                return
            item = ctx.db.one("SELECT quantity FROM subscription_items WHERE id = ?", item_id)
            if item is None:
                return
            qty = int(item["quantity"] or 1)
            tier = self.tier[sub_id]
            low, high = self.shape.seats[tier]
            if tier in ("starter", "pro"):
                delta = self.rng.randint(1, 3)
            else:
                delta = max(1, round(qty * self.rng.uniform(0.05, 0.20)))
            new = min(high, qty + delta) if up else max(low, qty - delta)
            if new == qty:
                return
            self.client.update_subscription(
                ctx,
                sub_id,
                {
                    "items": [{"id": item_id, "quantity": new}],
                    "proration_behavior": "create_prorations",
                },
            )
            self._driver().follow(ctx, sub_id)

        return run

    def _retier(self, sub_id: str, move: dict[str, str]) -> Run:
        """An upgrade (prorated onto the next invoice) or an immediate downgrade (invoiced at once,
        crediting the balance)."""

        def run(ctx: seahaven.Ctx) -> None:
            item_id = self.seat_item.get(sub_id)
            tier = self.tier[sub_id]
            if item_id is None or tier not in move or not self._active(ctx, sub_id):
                return
            p = self.prospect[sub_id]
            customer = self.customer[p.key]
            downgrade = move is _PREV_TIER
            if downgrade and self.card.get(customer) not in cards.GOOD:
                return  # an invoice-now change waits for a card that pays
            item = ctx.db.one("SELECT quantity FROM subscription_items WHERE id = ?", item_id)
            if item is None:
                return
            target = move[tier]
            low, high = self.shape.seats[target]
            qty = min(high, max(low, int(item["quantity"] or 1)))
            self.client.update_subscription(
                ctx,
                sub_id,
                {
                    "items": [
                        {"id": item_id, "price": self.catalog.prices[f"{target}_{_suffix(p)}"],
                         "quantity": qty}
                    ],
                    "proration_behavior": "always_invoice" if downgrade else "create_prorations",
                },
            )  # fmt: skip
            self.tier[sub_id] = target
            self._driver().follow(ctx, sub_id)

        return run

    def _swap(self, customer: str, card: Card | None) -> Run:
        """A new default card: `card`, or a good one drawn when the step runs."""

        def run(ctx: seahaven.Ctx) -> None:
            chosen = card if card is not None else cards.pick_good(self.rng)
            self._add_card(ctx, customer, chosen, cards.expiry(self.rng, NOW))

        return run

    def _refund(self, charge: str, amount: int | None, reason: str) -> Run:
        def run(ctx: seahaven.Ctx) -> None:
            row = ctx.db.one(
                "SELECT amount_captured - amount_refunded AS refundable, disputed FROM charges"
                " WHERE id = ?",
                charge,
            )
            if row is None or row["disputed"] or int(row["refundable"]) <= 0:
                return
            if amount is not None and amount > int(row["refundable"]):
                return
            self.client.refund(ctx, charge, amount=amount, reason=reason)

        return run

    def _credit_note(self, invoice: str, amount: int, reason: str, memo: str | None) -> Run:
        """A credit note to the customer's balance, for `amount` or what is left to credit on the
        invoice when that is less (a balance applied to the invoice, or an earlier note)."""

        def run(ctx: seahaven.Ctx) -> None:
            row = ctx.db.one(
                "SELECT amount_paid - post_payment_credit_notes_amount AS creditable FROM invoices"
                " WHERE id = ? AND status = 'paid'",
                invoice,
            )
            credit = 0 if row is None else min(amount, int(row["creditable"]))
            if credit <= 0:
                return
            params: dict[str, Any] = {
                "invoice": invoice,
                "amount": credit,
                "credit_amount": credit,
                "reason": reason,
            }
            if memo is not None:
                params["memo"] = memo
            self.client.call(ctx, "POST", "/v1/credit_notes", params)

        return run

    def _concession(self, sub_id: str) -> Run:
        """A one-off amount-off coupon made for this customer and applied (§4.6)."""

        def run(ctx: seahaven.Ctx) -> None:
            if not self._active(ctx, sub_id):
                return
            p = self.prospect[sub_id]
            coupon = self.client.call(
                ctx,
                "POST",
                "/v1/coupons",
                {
                    "amount_off": self.rng.randint(1, 5) * 10000,
                    "currency": "usd",
                    "duration": "once",
                    "name": f"Sales concession: {p.company}",
                },
            )
            self.client.update_subscription(ctx, sub_id, {"discounts": [{"coupon": coupon["id"]}]})
            self._driver().follow(ctx, sub_id)

        return run

    def _schedule_downgrade(self, sub_id: str) -> Run:
        """A downgrade set for the next renewal (§4.5): a schedule made from the subscription,
        updated to two phases, the current items to the period end and the lower tier after."""

        def run(ctx: seahaven.Ctx) -> None:
            self.downgrades_pending -= 1
            if not self._active(ctx, sub_id):
                return
            tier = self.tier[sub_id]
            p = self.prospect[sub_id]
            period = ctx.db.one(
                "SELECT MIN(current_period_start) AS start_at, MIN(current_period_end) AS end_at"
                " FROM subscription_items WHERE subscription = ?",
                sub_id,
            )
            items = ctx.db.rows(
                "SELECT price, quantity FROM subscription_items WHERE subscription = ?"
                " ORDER BY x_seq",
                sub_id,
            )
            if period is None or not items or tier not in _PREV_TIER:
                return
            current = [{"price": r["price"], "quantity": int(r["quantity"] or 1)} for r in items]
            lower = _PREV_TIER[tier]
            low, high = self.shape.seats[lower]
            later = [dict(item) for item in current]
            later[0] = {
                "price": self.catalog.prices[f"{lower}_{_suffix(p)}"],
                "quantity": min(high, max(low, current[0]["quantity"])),
            }
            schedule = self.client.call(
                ctx,
                "POST",
                "/v1/subscription_schedules",
                {"customer": self.customer[p.key], "from_subscription": sub_id},
            )["id"]
            self.client.call(
                ctx,
                "POST",
                f"/v1/subscription_schedules/{schedule}",
                {
                    "end_behavior": "release",
                    "phases": [
                        {
                            "items": current,
                            "start_date": _time.to_unix(str(period["start_at"])),
                            "end_date": _time.to_unix(str(period["end_at"])),
                        },
                        {"items": later, "iterations": 1},
                    ],
                },
            )
            self.downgrades += 1

        return run

    # --- the SLA batch (§4.7) -----------------------------------------------------------------

    def schedule_sla_batch(self) -> None:
        self.tl.add(SLA_SELECT_AT, "SLA batch", self._sla_select)

    def _sla_select(self, ctx: seahaven.Ctx) -> None:
        business = [pid for pid, tier in self.catalog.tier_of_price.items() if tier == "business"]
        rows = ctx.db.rows(
            "SELECT s.customer, MIN(s.created) AS first_at, MIN(s.id) AS first_id"
            " FROM subscriptions s JOIN subscription_items i ON i.subscription = s.id"
            " JOIN prices p ON p.id = i.price"
            " WHERE (p.id IN (SELECT value FROM json_each(?)) OR p.product = ?)"
            " AND s.created <= '2026-04-14T23:59:59.999Z'"
            " AND (s.ended_at IS NULL OR s.ended_at >= '2026-04-14T00:00:00.000Z')"
            " GROUP BY s.customer ORDER BY first_at, first_id",
            _json.dumps(business),
            self.catalog.products["enterprise"],
        )
        if not rows:
            return
        # Business hours in Denver, 2026-04-20 to 04-24: 09:00-17:00 MDT is 15:00-23:00Z.
        slots = [
            plus(f"2026-04-{20 + day}T15:00:00.000Z", minutes=minute)
            for day in range(5)
            for minute in range(8 * 60)
        ]
        stride = len(slots) / len(rows)
        for n, row in enumerate(rows):
            self.tl.add(
                slots[int(n * stride)], "SLA credit", self._sla_credit(str(row["customer"]))
            )

    def _sla_credit(self, customer: str) -> Run:
        def run(ctx: seahaven.Ctx) -> None:
            invoice = ctx.db.one(
                "SELECT id, total FROM invoices WHERE customer = ? AND status = 'paid'"
                " AND parent_subscription IS NOT NULL AND total > 0 ORDER BY x_seq DESC LIMIT 1",
                customer,
            )
            if invoice is None:
                return  # still on a trial: nothing paid to credit
            amount = int(invoice["total"]) // 10
            if amount > 0:
                self._credit_note(str(invoice["id"]), amount, "product_unsatisfactory", SLA_MEMO)(
                    ctx
                )

        return run


def _suffix(p: Prospect) -> str:
    return "monthly" if p.interval == "month" else "annual"
