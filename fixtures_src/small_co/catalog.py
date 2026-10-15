"""Wrenfield's catalog (components/small_co.md §2): products, prices and coupons, each created by
its own step at its own instant. `Catalog` maps keys to the ids the API returns, so the cast can
name a price by key."""

from typing import Any

import seahaven
from fixtures_src.replay.client import Client
from fixtures_src.replay.timeline import Timeline

from seahaven_stripe_world import _time

__all__ = ["Catalog"]


def _price(
    product: str, amount: int, nickname: str, lookup_key: str, interval: str | None
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "product": product,
        "unit_amount": amount,
        "currency": "usd",
        "nickname": nickname,
        "lookup_key": lookup_key,
    }
    if interval is not None:
        params["recurring"] = {"interval": interval}
    return params


class Catalog:
    def __init__(self) -> None:
        self.ids: dict[str, str] = {}

    def price(self, key: str) -> str:
        return self.ids[key]

    def schedule(self, tl: Timeline, client: Client) -> None:
        def private_beta(ctx: seahaven.Ctx) -> None:
            solo = client.call(
                ctx,
                "POST",
                "/v1/products",
                {
                    "name": "Wrenfield Solo",
                    "description": "Booking page, lesson reminders and payment records for one "
                    "teacher.",
                },
            )["id"]
            self.ids["solo"] = solo
            self.ids["founding_annual"] = client.call(
                ctx,
                "POST",
                "/v1/prices",
                _price(solo, 9600, "Founding member", "founding_annual", "year"),
            )["id"]

        def launch_catalog(ctx: seahaven.Ctx) -> None:
            studio = client.call(
                ctx,
                "POST",
                "/v1/products",
                {
                    "name": "Wrenfield Studio",
                    "description": "Wrenfield for a studio of up to five teachers.",
                },
            )["id"]
            setup = client.call(
                ctx,
                "POST",
                "/v1/products",
                {
                    "name": "Done-for-you setup",
                    "description": "We import your students and build your booking page.",
                },
            )["id"]
            solo = self.ids["solo"]
            for key, product, amount, nickname, interval in (
                ("solo_monthly", solo, 1200, "Solo monthly", "month"),
                ("solo_annual", solo, 12000, "Solo annual", "year"),
                ("studio_monthly", studio, 2900, "Studio monthly", "month"),
                ("studio_annual", studio, 29000, "Studio annual", "year"),
                ("setup", setup, 4900, "Setup", None),
            ):
                self.ids[key] = client.call(
                    ctx, "POST", "/v1/prices", _price(product, amount, nickname, key, interval)
                )["id"]

        def launch_coupon(ctx: seahaven.Ctx) -> None:
            # 50% off the first invoice, not small_co.md §2's 3 repeating months: this world
            # never ends a repeating discount (https://github.com/Kiln-AI/stripe_world/issues/13).
            client.call(
                ctx,
                "POST",
                "/v1/coupons",
                {
                    "id": "LAUNCH",
                    "percent_off": 50,
                    "duration": "once",
                    "redeem_by": _time.to_unix("2026-03-31T23:59:59.000Z"),
                    "name": "Launch: 50% off your first invoice",
                },
            )

        def archive_founding(ctx: seahaven.Ctx) -> None:
            client.call(ctx, "POST", f"/v1/prices/{self.ids['founding_annual']}", {"active": False})

        def newsletter_coupon(ctx: seahaven.Ctx) -> None:
            client.call(
                ctx,
                "POST",
                "/v1/coupons",
                {
                    "id": "TEACHWELL25",
                    "percent_off": 25,
                    "duration": "once",
                    "name": "Teachwell newsletter: 25% off",
                },
            )

        tl.add("2025-08-26T15:00:00.000Z", "catalog: Wrenfield Solo", private_beta)
        tl.add("2026-02-23T16:00:00.000Z", "catalog: launch prices", launch_catalog)
        tl.add("2026-02-27T18:00:00.000Z", "catalog: LAUNCH coupon", launch_coupon)
        tl.add("2026-03-02T09:00:00.000Z", "catalog: archive founding price", archive_founding)
        tl.add("2026-07-13T16:00:00.000Z", "catalog: TEACHWELL25 coupon", newsletter_coupon)
