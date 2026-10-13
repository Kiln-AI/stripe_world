"""The world's API dispatcher at an instant, and the helpers the stories use (replay.md §3).

`params` are typed Python values (ints, bools, nested dicts and lists), the shape the MCP tools
pass, not form strings. Each call runs in a savepoint, as the HTTP face's does, so a raised
`StripeApiError` loses only that call's writes.
"""

from collections.abc import Mapping, Sequence
from typing import Any

import seahaven
from fixtures_src.replay import engine
from fixtures_src.replay.cards import Card
from fixtures_src.replay.timeline import FixtureBuildError

from seahaven_stripe_world import _json
from seahaven_stripe_world.dispatch import dispatch
from seahaven_stripe_world.dispatch.response import ApiResponse
from seahaven_stripe_world.middleware.stripe_envelope import mint_request
from seahaven_stripe_world.stripe_errors import StripeApiError

__all__ = ["Client"]


class Client:
    """Calls `dispatch` the way the tool and HTTP faces do: a request id first, then the call."""

    def call(
        self,
        ctx: seahaven.Ctx,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        automatic: bool = False,
    ) -> dict[str, Any]:
        """The body on 200; anything else raises FixtureBuildError with the status and body."""
        response = self.attempt(ctx, method, path, params, automatic=automatic)
        if response.status != 200:
            raise FixtureBuildError(
                f"{method} {path} answered {response.status}: {_json.dumps(response.body)}"
            )
        return response.body

    def attempt(
        self,
        ctx: seahaven.Ctx,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        automatic: bool = False,
    ) -> ApiResponse:
        """The response whatever its status, for steps whose expected outcome is a decline.
        With `automatic=True` the request context is null (Stripe acting on its own)."""
        if automatic:
            engine.automatic(ctx)
        else:
            mint_request(ctx, None)
        try:
            with ctx.db.transaction():
                return dispatch(ctx, method, path, params, idempotency_key=None)
        except StripeApiError as error:
            return ApiResponse(error.status, error.envelope())

    # --- helpers ------------------------------------------------------------------------------

    def create_customer(
        self,
        ctx: seahaven.Ctx,
        *,
        name: str,
        email: str,
        phone: str | None = None,
        address: Mapping[str, Any] | None = None,
        description: str | None = None,
        metadata: Mapping[str, str] | None = None,
        tax_exempt: str | None = None,
    ) -> str:
        params: dict[str, Any] = {"name": name, "email": email}
        for key, value in (
            ("phone", phone),
            ("address", address),
            ("description", description),
            ("metadata", metadata),
            ("tax_exempt", tax_exempt),
        ):
            if value is not None:
                params[key] = value
        return self.call(ctx, "POST", "/v1/customers", params)["id"]

    def add_card(
        self,
        ctx: seahaven.Ctx,
        customer: str,
        card: Card,
        *,
        exp: tuple[int, int],
        default: bool = True,
    ) -> str:
        """Create a card payment method, attach it, and (by default) make it the invoice default."""
        name_row = ctx.db.one("SELECT name FROM customers WHERE id = ?", customer)
        params: dict[str, Any] = {
            "type": "card",
            "card": {"number": card.number, "exp_month": exp[0], "exp_year": exp[1], "cvc": "123"},
        }
        if name_row is not None and name_row["name"] is not None:
            params["billing_details"] = {"name": name_row["name"]}
        pm = self.call(ctx, "POST", "/v1/payment_methods", params)["id"]
        self.call(ctx, "POST", f"/v1/payment_methods/{pm}/attach", {"customer": customer})
        if default:
            self.call(
                ctx,
                "POST",
                f"/v1/customers/{customer}",
                {"invoice_settings": {"default_payment_method": pm}},
            )
        return pm

    def subscribe(
        self,
        ctx: seahaven.Ctx,
        customer: str,
        items: Sequence[Mapping[str, Any]],
        *,
        trial_days: int | None = None,
        trial_end: int | None = None,
        coupon: str | None = None,
        collection_method: str = "charge_automatically",
        days_until_due: int | None = None,
        tax_rates: Sequence[str] = (),
        metadata: Mapping[str, str] | None = None,
        payment_behavior: str | None = None,
    ) -> dict[str, Any]:
        """`POST /v1/subscriptions`; a trial pauses at its end when no card is on file.
        `trial_end` is Unix seconds, as the API takes it."""
        params: dict[str, Any] = {
            "customer": customer,
            "items": [dict(item) for item in items],
            "collection_method": collection_method,
        }
        if trial_days is not None:
            params["trial_period_days"] = trial_days
        if trial_end is not None:
            params["trial_end"] = trial_end
        if trial_days is not None or trial_end is not None:
            params["trial_settings"] = {"end_behavior": {"missing_payment_method": "pause"}}
        if coupon is not None:
            params["discounts"] = [{"coupon": coupon}]
        if days_until_due is not None:
            params["days_until_due"] = days_until_due
        if tax_rates:
            params["default_tax_rates"] = list(tax_rates)
        if metadata is not None:
            params["metadata"] = dict(metadata)
        if payment_behavior is not None:
            params["payment_behavior"] = payment_behavior
        return self.call(ctx, "POST", "/v1/subscriptions", params)

    def update_subscription(
        self, ctx: seahaven.Ctx, sub: str, params: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self.call(ctx, "POST", f"/v1/subscriptions/{sub}", params)

    def cancel_subscription(
        self, ctx: seahaven.Ctx, sub: str, *, feedback: str, comment: str | None = None
    ) -> dict[str, Any]:
        details: dict[str, Any] = {"feedback": feedback}
        if comment is not None:
            details["comment"] = comment
        return self.call(
            ctx, "DELETE", f"/v1/subscriptions/{sub}", {"cancellation_details": details}
        )

    def refund(
        self,
        ctx: seahaven.Ctx,
        charge: str,
        *,
        amount: int | None = None,
        reason: str = "requested_by_customer",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"charge": charge, "reason": reason}
        if amount is not None:
            params["amount"] = amount
        return self.call(ctx, "POST", "/v1/refunds", params)

    def invoice_item(
        self, ctx: seahaven.Ctx, customer: str, price: str, *, quantity: int = 1
    ) -> dict[str, Any]:
        """A pending invoice item for a one-time price. The route declares no `price` parameter,
        so the price's amount, currency and product name are read and sent as such."""
        row = ctx.db.one(
            "SELECT p.unit_amount, p.currency, pr.name FROM prices p"
            " JOIN products pr ON pr.id = p.product WHERE p.id = ?",
            price,
        )
        if row is None:
            raise FixtureBuildError(f"no price {price!r}")
        return self.call(
            ctx,
            "POST",
            "/v1/invoiceitems",
            {
                "customer": customer,
                "amount": int(row["unit_amount"]) * quantity,
                "currency": row["currency"],
                "description": row["name"],
            },
        )

    def pay_invoice(
        self, ctx: seahaven.Ctx, invoice: str, *, out_of_band: bool = False
    ) -> dict[str, Any]:
        params = {"paid_out_of_band": True} if out_of_band else None
        return self.call(ctx, "POST", f"/v1/invoices/{invoice}/pay", params)

    def latest_charge(self, ctx: seahaven.Ctx, customer: str) -> dict[str, Any]:
        """The customer's most recent succeeded charge (a read)."""
        row = ctx.db.one(
            "SELECT * FROM charges WHERE customer = ? AND status = 'succeeded'"
            " ORDER BY x_seq DESC LIMIT 1",
            customer,
        )
        if row is None:
            raise FixtureBuildError(f"customer {customer} has no succeeded charge")
        return dict(row)
