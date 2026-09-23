"""The invoice path's arithmetic, pure and recorded: the totals a
subscription's first invoice carries (plain, coupon, tax, and the two
composed), the customer-balance settlement, and the per-customer number
sequence. Every number here is a Phase 12 probe value at
`2026-08-26.dahlia`."""

from fractions import Fraction
from typing import Any

import pytest
import seahaven

from conftest import BLANK_NOW
from seahaven_stripe_world.billing import invoicing

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def _line(amount: int = 2000, quantity: int = 1) -> invoicing.ItemLine:
    return invoicing.ItemLine(
        subscription_item_id="si_x",
        subscription_id="sub_x",
        price_id="price_x",
        product_id="prod_x",
        unit_amount=amount,
        quantity=quantity,
        period_start="2026-09-01T14:00:00.000Z",
        period_end="2026-10-01T14:00:00.000Z",
        description="x",
    )


TEN_PERCENT = invoicing.DiscountSpec("di_x", "coupon_x", None, Fraction(10))
FIVE_PERCENT_GST = invoicing.TaxRateSpec("txr_x", Fraction(5), False)


def test_plain_totals_match_the_recording() -> None:
    totals = invoicing.compute_totals([_line(2000)], currency="cad")
    assert totals.subtotal == 2000
    assert totals.total == 2000
    assert totals.total_discount_amounts == []
    assert totals.total_taxes == []
    line = totals.lines[0]
    assert line["description"] == "x"  # description is the caller's
    assert line["amount"] == 2000
    assert line["pricing"]["unit_amount_decimal"] == "2000"
    assert line["parent"]["type"] == "subscription_item_details"
    assert line["taxes"] == []


def test_coupon_totals_match_the_recording() -> None:
    # Recorded (Phase 12): 10% once-coupon takes 3000 -> 2700
    totals = invoicing.compute_totals(
        [_line(3000)], currency="cad", invoice_discounts=[TEN_PERCENT]
    )
    assert totals.subtotal == 3000
    assert totals.invoice_discount_total == 300
    assert totals.total == 2700
    assert totals.total_discount_amounts == [{"amount": 300, "discount": "di_x"}]
    assert totals.lines[0]["discount_amounts"] == [{"amount": 300, "discount": "di_x"}]


def test_tax_totals_match_the_recording() -> None:
    # Recorded (Phase 12): 5% exclusive GST on 3000 -> 3150
    totals = invoicing.compute_totals([_line(3000)], currency="cad", tax_rates=[FIVE_PERCENT_GST])
    assert totals.subtotal == 3000
    assert totals.total == 3150
    assert totals.total_excluding_tax == 3000
    tax = totals.total_taxes[0]
    assert tax["amount"] == 150
    assert tax["tax_behavior"] == "exclusive"
    assert tax["taxability_reason"] == "not_available"  # the recording's value
    assert tax["tax_rate_details"] == {"tax_rate": "txr_x"}


def test_coupon_and_tax_compose_on_the_post_discount_base() -> None:
    # Recorded (Phase 12): 3000 - 300 coupon, then 5% on 2700 -> 2835
    totals = invoicing.compute_totals(
        [_line(3000)], currency="cad", invoice_discounts=[TEN_PERCENT], tax_rates=[FIVE_PERCENT_GST]
    )
    assert totals.total == 2835
    assert totals.total_taxes[0]["amount"] == 135
    assert totals.total_taxes[0]["taxable_amount"] == 2700
    assert totals.lines[0]["taxes"][0]["taxable_amount"] == 2700


def test_the_discount_apportions_floor_then_remainder() -> None:
    # The documented coupon split: 500 across [1000, 2000] -> [166, 334]
    totals = invoicing.compute_totals(
        [_line(1000), _line(2000)],
        currency="usd",
        invoice_discounts=[invoicing.DiscountSpec("di_a", "c", 500, None)],
    )
    amounts = [entry["amount"] for entry in totals.lines[0]["discount_amounts"]]
    amounts += [entry["amount"] for entry in totals.lines[1]["discount_amounts"]]
    assert amounts == [166, 334]


def test_inclusive_tax_carves_out() -> None:
    inclusive = invoicing.TaxRateSpec("txr_i", Fraction(10), True)
    totals = invoicing.compute_totals([_line(1100)], currency="usd", tax_rates=[inclusive])
    assert totals.total == 1100  # inclusive tax never adds
    assert totals.subtotal_excluding_tax == 1000
    assert totals.total_excluding_tax == 1000
    assert totals.total_taxes[0]["amount"] == 100


def test_the_settlement_worked_cases() -> None:
    # §3.5's three worked examples, verbatim
    assert invoicing.settle_customer_balance(400, -1000) == (0, -600, 400)
    assert invoicing.settle_customer_balance(1000, 500) == (1500, 0, -500)
    assert invoicing.settle_customer_balance(-334, 0) == (0, -334, -334)


def test_describe_amount_and_interval() -> None:
    assert invoicing.describe_amount(2000, "cad") == "$20.00"
    assert invoicing.describe_amount(2000, "usd") == "$20.00"
    assert invoicing.describe_amount(2000, "eur") == "€20.00"
    assert invoicing.describe_amount(1000, "jpy") == "¥1000"
    assert invoicing.describe_interval("month") == "month"
    assert invoicing.describe_interval("month", 3) == "3 months"


def test_add_interval_is_calendar_true() -> None:
    iso = "2026-01-31T14:00:00.000Z"
    assert invoicing.add_interval(iso, interval="month") == "2026-02-28T14:00:00.000Z"
    assert (
        invoicing.add_interval(iso, interval="month", interval_count=2)
        == "2026-03-31T14:00:00.000Z"
    )
    assert invoicing.add_interval(iso, interval="week") == "2026-02-07T14:00:00.000Z"
    assert invoicing.add_interval(iso, interval="year") == "2027-01-31T14:00:00.000Z"


def test_the_number_comes_from_the_customer_sequence(instance: seahaven.Instance) -> None:
    result = instance.call(
        "stripe_api_write",
        method="POST",
        path="/v1/customers",
        params={"email": "numbers@example.test"},
    )
    prefix = result["invoice_prefix"]
    row = instance.inspect().one("SELECT * FROM customers WHERE id = ?", result["id"])
    assert row is not None
    with instance.bulk() as ctx:
        first = invoicing.finalize_invoice(ctx, _draft_invoice(ctx, row, total=1000))
        second = invoicing.finalize_invoice(ctx, _draft_invoice(ctx, row, total=1000))
    assert first["number"] == f"{prefix}-0001"
    assert second["number"] == f"{prefix}-0002"
    fresh = instance.inspect().one("SELECT * FROM customers WHERE id = ?", row["id"])
    assert fresh is not None
    assert fresh["next_invoice_sequence"] == 3


def _draft_invoice(ctx: seahaven.Ctx, customer: dict[str, Any], *, total: int) -> str:
    totals = invoicing.compute_totals(
        [
            invoicing.ItemLine(
                "si_x",
                "sub_x",
                "price_x",
                "prod_x",
                total,
                1,
                ctx.clock.iso(),
                ctx.clock.iso(),
                "draft",
            )
        ],
        currency="usd",
    )
    row = invoicing.create_invoice(
        ctx,
        customer_id=customer["id"],
        currency="usd",
        collection_method="charge_automatically",
        billing_reason="subscription_create",
        totals=totals,
        subscription_id=None,
        auto_advance=False,
        days_until_due=None,
        period_start=ctx.clock.iso(),
        period_end=ctx.clock.iso(),
    )
    return row["id"]


def test_the_customer_balance_settles_into_the_cbt_row(instance: seahaven.Instance) -> None:
    result = instance.call(
        "stripe_api_write",
        method="POST",
        path="/v1/customers",
        params={"email": "credit@example.test", "balance": -1000},
    )
    cus = result
    row = instance.inspect().one("SELECT * FROM customers WHERE id = ?", cus["id"])
    assert row is not None
    with instance.bulk() as ctx:
        invoice_id = _draft_invoice(ctx, row, total=400)
        invoicing.finalize_invoice(ctx, invoice_id)
    invoice = instance.inspect().one("SELECT * FROM invoices WHERE id = ?", invoice_id)
    assert invoice is not None
    assert invoice["starting_balance"] == -1000
    assert invoice["ending_balance"] == -600
    assert invoice["amount_due"] == 0  # the credit covered it
    cbt = instance.inspect().one("SELECT * FROM customer_balance_transactions")
    assert cbt is not None
    assert cbt["type"] == "applied_to_invoice"
    assert cbt["amount"] == 400
    assert cbt["ending_balance"] == -600
    fresh = instance.inspect().one("SELECT * FROM customers WHERE id = ?", cus["id"])
    assert fresh is not None
    assert fresh["balance"] == -600
