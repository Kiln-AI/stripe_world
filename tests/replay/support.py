"""Shared scaffolding for the replay-engine tests: a blank test-mode instance carrying the
Wrenfield account, and a small story builder over the real drivers."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import seahaven
from fixtures_src import accounts
from fixtures_src.replay import cards
from fixtures_src.replay.cards import Card
from fixtures_src.replay.client import Client
from fixtures_src.replay.clock import plus
from fixtures_src.replay.subscriptions import Hooks, NoHooks, Subscriptions
from fixtures_src.replay.timeline import Timeline

from seahaven_stripe_world.world import world

NOW = "2026-09-01T14:00:00.000Z"
CATALOG_AT = "2026-01-05T09:00:00.000Z"


@contextmanager
def blank() -> Iterator[seahaven.Instance]:
    """A blank instance opened as the company builders open theirs."""
    with world.instance(None, now=NOW, clock_mode="fixed", startup={"livemode": False}) as inst:
        yield inst


class Story:
    """One account, one monthly $12 price, a timeline to `NOW`, and the subscription driver."""

    def __init__(self, ctx: seahaven.Ctx, hooks: Hooks | None = None) -> None:
        accounts.install_account(ctx, accounts.WRENFIELD)
        self.timeline = Timeline(ctx, until=NOW)
        self.client = Client()
        self.subs = Subscriptions(self.timeline, self.client, hooks or NoHooks())
        self.ids: dict[str, str] = {}
        self.timeline.add(CATALOG_AT, "catalog", self._catalog)

    def _catalog(self, ctx: seahaven.Ctx) -> None:
        product = self.client.call(ctx, "POST", "/v1/products", {"name": "Wrenfield Solo"})
        self.ids["price"] = self.client.call(
            ctx,
            "POST",
            "/v1/prices",
            {
                "product": product["id"],
                "unit_amount": 1200,
                "currency": "usd",
                "recurring": {"interval": "month"},
            },
        )["id"]

    def signup(
        self,
        at: str,
        key: str,
        *,
        card: Card | None = cards.GOOD[0],
        trial_days: int | None = None,
        payment_behavior: str | None = None,
    ) -> None:
        """At `at`: a customer, optionally a card, and a subscription the driver follows."""

        def run(ctx: seahaven.Ctx) -> None:
            customer = self.client.create_customer(
                ctx, name=f"Customer {key}", email=f"{key}@okaforpiano.com"
            )
            self.ids[f"{key}.customer"] = customer
            if card is not None:
                self.ids[f"{key}.pm"] = self.client.add_card(ctx, customer, card, exp=(4, 2029))
            sub = self.client.subscribe(
                ctx,
                customer,
                [{"price": self.ids["price"]}],
                trial_days=trial_days,
                payment_behavior=payment_behavior,
            )
            self.ids[key] = sub["id"]
            self.subs.follow(ctx, sub["id"])

        self.timeline.add(at, f"signup {key}", run)

    def swap_card(self, at: str, key: str, card: Card) -> None:
        def run(ctx: seahaven.Ctx) -> None:
            self.client.add_card(ctx, self.ids[f"{key}.customer"], card, exp=(6, 2030))

        self.timeline.add(at, f"swap card {key}", run)


def rows(inst: seahaven.Instance, sql: str, *params: Any) -> list[dict[str, Any]]:
    return inst.inspect().rows(sql, *params)


__all__ = ["CATALOG_AT", "NOW", "Story", "blank", "plus", "rows"]
