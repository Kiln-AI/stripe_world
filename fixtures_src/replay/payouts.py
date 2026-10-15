"""The payout cadence driver (replay.md §6): a manual payout of the available balance at each
instant, and its arrival when the arrival date comes before `until`."""

from collections.abc import Callable, Iterable

import seahaven
from fixtures_src.replay import engine
from fixtures_src.replay.client import Client
from fixtures_src.replay.timeline import Timeline

__all__ = ["Payouts"]


class Payouts:
    def __init__(self, timeline: Timeline, client: Client, *, destination: str) -> None:
        self.timeline = timeline
        self.client = client
        self.destination = destination

    def schedule(self, instants: Iterable[str]) -> None:
        """At each instant: `GET /v1/balance`; when the available usd amount is positive,
        `POST /v1/payouts`; then schedule its arrival (dropped if after `until`, leaving it
        pending)."""
        for instant in instants:
            self.timeline.add(instant, "payout", self._payout)

    def _payout(self, ctx: seahaven.Ctx) -> None:
        balance = self.client.call(ctx, "GET", "/v1/balance")
        amount = sum(
            int(entry["amount"]) for entry in balance["available"] if entry["currency"] == "usd"
        )
        if amount <= 0:
            return
        payout = self.client.call(
            ctx,
            "POST",
            "/v1/payouts",
            {"amount": amount, "currency": "usd", "destination": self.destination},
        )
        row = ctx.db.one("SELECT arrival_date FROM payouts WHERE id = ?", payout["id"])
        if row is not None:
            self.timeline.add(
                str(row["arrival_date"]), f"payout {payout['id']} arrives", _arrive(payout["id"])
            )


def _arrive(payout_id: str) -> Callable[[seahaven.Ctx], None]:
    def run(ctx: seahaven.Ctx) -> None:
        engine.arrive(ctx, payout_id)

    return run
