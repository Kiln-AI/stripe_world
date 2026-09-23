"""The invoices slice through the real four-tool chain: the manual create
(both sweep modes), the draft window fields, the per-endpoint 404 spellings,
the list filters, and the draft-editing line endpoints — pinned by cassette
13 at `2026-08-26.dahlia`. The status machine's transitions and their
refusals live in `tests/billing/test_invoice_machine.py` beside their
invariants."""

import json

import pytest
import seahaven

from conftest import BLANK_NOW
from seahaven_stripe_world.errors import StripeToolError

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)

BLANK_UNIX = 1788271200  # BLANK_NOW in seconds; conftest owns the instant


def call(instance: seahaven.Instance, method: str, path: str, params: dict | None = None):
    if method == "GET":
        return instance.call("stripe_api_read", path=path, params=params)
    return instance.call("stripe_api_write", method=method, path=path, params=params)


@pytest.fixture()
def customer(instance: seahaven.Instance) -> str:
    cus = call(instance, "POST", "/v1/customers", {"email": "inv@example.test"})["id"]
    pm = call(
        instance, "POST", "/v1/payment_methods", {"type": "card", "card": {"token": "tok_visa"}}
    )["id"]
    call(instance, "POST", f"/v1/payment_methods/{pm}/attach", {"customer": cus})
    call(
        instance,
        "POST",
        f"/v1/customers/{cus}",
        {"invoice_settings": {"default_payment_method": pm}},
    )
    return cus


def item(instance: seahaven.Instance, customer: str, amount: int, description: str) -> str:
    return call(
        instance,
        "POST",
        "/v1/invoiceitems",
        {
            "customer": customer,
            "amount": amount,
            "currency": "usd",
            "description": description,
        },
    )


def test_the_default_create_excludes_pending_items(instance, customer) -> None:
    item(instance, customer, 2000, "setup")
    body = call(instance, "POST", "/v1/invoices", {"customer": customer})
    assert body["object"] == "invoice"
    assert body["status"] == "draft"
    assert body["billing_reason"] == "manual"
    assert body["auto_advance"] is False
    assert body["attempted"] is False
    assert body["attempt_count"] == 0
    assert body["number"] is None
    assert body["effective_at"] is None
    assert body["ending_balance"] is None
    assert body["starting_balance"] == 0
    assert body["amount_due"] == 0  # the total IS 0: nothing was swept
    assert body["period_start"] == body["period_end"] == body["created"]
    assert body["lines"]["data"] == []
    assert body["lines"]["url"] == f"/v1/invoices/{body['id']}/lines"
    assert "total_count" not in body["lines"]
    # the body's constants and echoes
    assert body["issuer"] == {"type": "self"}
    assert body["automatic_tax"] == {
        "disabled_reason": None,
        "enabled": False,
        "liability": None,
        "provider": None,
        "status": None,
    }
    assert body["rendering"] is None
    assert body["customer_tax_ids"] == []
    assert body["webhooks_delivered_at"] == body["created"]
    assert body["hosted_invoice_url"] is None
    assert body["invoice_pdf"] is None
    assert "payments" not in body
    assert "threshold_reason" not in body
    assert "confirmation_secret" not in body


def test_include_sweeps_everything_newest_first(instance, customer) -> None:
    item(instance, customer, 2000, "older")
    item(instance, customer, 1500, "newer")
    body = call(
        instance,
        "POST",
        "/v1/invoices",
        {"customer": customer, "pending_invoice_items_behavior": "include"},
    )
    amounts = [(line["amount"], line["description"]) for line in body["lines"]["data"]]
    assert amounts == [(1500, "newer"), (2000, "older")]
    assert body["subtotal"] == body["total"] == 3500
    assert body["amount_due"] == body["amount_remaining"] == 3500
    assert body["amount_paid"] == 0
    line = body["lines"]["data"][0]
    assert line["object"] == "line_item"
    assert line["parent"] == {
        "invoice_item_details": {
            "invoice_item": line["parent"]["invoice_item_details"]["invoice_item"],
            "proration": False,
            "proration_details": {"credited_items": None},
            "subscription": None,
        },
        "subscription_item_details": None,
        "type": "invoice_item_details",
    }
    assert line["pricing"]["type"] == "price_details"
    assert line["discounts"] == []
    assert line["livemode"] is True
    # the lines sub-list pages the same rows
    sub = call(instance, "GET", f"/v1/invoices/{body['id']}/lines")
    assert [row["id"] for row in sub["data"]] == [row["id"] for row in body["lines"]["data"]]
    assert sub["url"] == f"/v1/invoices/{body['id']}/lines"


def test_the_draft_window_fields(instance, customer) -> None:
    item(instance, customer, 800, "late fee")
    auto = call(
        instance,
        "POST",
        "/v1/invoices",
        {
            "customer": customer,
            "auto_advance": True,
            "pending_invoice_items_behavior": "include",
        },
    )
    created = auto["created"]
    # recorded offsets (cassette 13): the ceil and the floor of the same
    # un-floored instant, one second apart
    assert auto["automatically_finalizes_at"] == created + 3601
    assert auto["next_payment_attempt"] == created + 3600
    cleared = call(instance, "POST", f"/v1/invoices/{auto['id']}", {"auto_advance": False})
    assert cleared["auto_advance"] is False
    assert cleared["automatically_finalizes_at"] is None
    assert cleared["next_payment_attempt"] is None
    # re-set recomputes from created, not from the update moment
    reset = call(instance, "POST", f"/v1/invoices/{auto['id']}", {"auto_advance": True})
    assert reset["automatically_finalizes_at"] == created + 3601
    assert reset["next_payment_attempt"] == created + 3600
    # send_invoice advances but never schedules a payment attempt
    sent = call(
        instance,
        "POST",
        "/v1/invoices",
        {
            "customer": customer,
            "auto_advance": True,
            "collection_method": "send_invoice",
            "days_until_due": 7,
        },
    )
    assert sent["automatically_finalizes_at"] == sent["created"] + 3601
    assert sent["next_payment_attempt"] is None
    assert sent["due_date"] == sent["created"] + 7 * 86_400


def test_the_send_invoice_due_date_refusals(instance, customer) -> None:
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            "/v1/invoices",
            {"customer": customer, "collection_method": "send_invoice"},
        )
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["message"] == (
        "If invoice collection method is 'send_invoice', you must specify "
        "'due_date' or 'days_until_due'."
    )
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", "/v1/invoices", {"customer": customer, "days_until_due": 5})
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["message"] == (
        "You can only specify 'due_date' or 'days_until_due' if invoice "
        "collection method is 'send_invoice'."
    )


def test_the_404_param_spellings_are_per_endpoint(instance, customer) -> None:
    invoice = call(instance, "POST", "/v1/invoices", {"customer": customer})
    del invoice  # every step below names a missing id
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "GET", "/v1/invoices/in_nope")
    assert exc_info.value.status == 404
    assert exc_info.value.stripe_body["error"]["message"] == "No such invoice: 'in_nope'"
    assert exc_info.value.stripe_body["error"]["param"] == "invoice"
    for path, param, params in (
        ("/v1/invoices/in_nope/pay", "id", {}),
        ("/v1/invoices/in_nope/finalize", "id", {}),
        ("/v1/invoices/in_nope/void", "id", {}),
        ("/v1/invoices/in_nope/send", "invoice", {}),
        ("/v1/invoices/in_nope/mark_uncollectible", "id", {}),
        ("/v1/invoices/in_nope/add_lines", "invoice", {"lines": [{"amount": 1}]}),
    ):
        with pytest.raises(StripeToolError) as exc_info:
            call(instance, "POST", path, params)
        assert exc_info.value.status == 404, path
        assert exc_info.value.stripe_body["error"]["message"] == "No such invoice: 'in_nope'", path
        assert exc_info.value.stripe_body["error"]["param"] == param, path
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", "/v1/invoices/in_nope", {"metadata": {"k": "v"}})
    assert exc_info.value.stripe_body["error"]["param"] == "id"
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "DELETE", "/v1/invoices/in_nope")
    assert exc_info.value.stripe_body["error"]["param"] == "invoice"


def test_the_list_filters(instance, customer) -> None:
    draft = call(instance, "POST", "/v1/invoices", {"customer": customer})
    other = call(instance, "POST", "/v1/customers", {"email": "o@example.test"})["id"]
    call(instance, "POST", "/v1/invoices", {"customer": other})
    by_customer = call(instance, "GET", "/v1/invoices", {"customer": customer})
    assert [row["id"] for row in by_customer["data"]] == [draft["id"]]
    by_status = call(instance, "GET", "/v1/invoices", {"customer": customer, "status": "draft"})
    assert [row["id"] for row in by_status["data"]] == [draft["id"]]
    by_method = call(
        instance,
        "GET",
        "/v1/invoices",
        {"customer": customer, "collection_method": "send_invoice"},
    )
    assert by_method["data"] == []
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "GET", "/v1/invoices", {"status": "bogus"})
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["message"] == (
        "Invalid status: must be one of draft, open, void, paid, or uncollectible"
    )
    created_range = call(
        instance,
        "GET",
        "/v1/invoices",
        {"created": {"gte": BLANK_UNIX + 1}},
    )
    assert created_range["data"] == []
    due_range = call(instance, "GET", "/v1/invoices", {"due_date": {"lt": BLANK_UNIX + 1}})
    assert due_range["data"] == []


def test_the_line_editing_family(instance, customer) -> None:
    item(instance, customer, 2000, "base")
    invoice = call(
        instance,
        "POST",
        "/v1/invoices",
        {"customer": customer, "pending_invoice_items_behavior": "include"},
    )
    added = call(
        instance,
        "POST",
        f"/v1/invoices/{invoice['id']}/add_lines",
        {"lines": [{"amount": 999, "description": "Extra line"}]},
    )
    assert added["status"] == "draft"
    line_id = added["lines"]["data"][0]["id"]
    assert added["lines"]["data"][0]["amount"] == 999
    assert added["total"] == 2999
    # amount XOR quantity (recorded refusal); quantity alone has no unit to
    # multiply, so the binder's missing-parameter refusal answers it
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            f"/v1/invoices/{invoice['id']}/add_lines",
            {"lines": [{"amount": 5, "quantity": 2}]},
        )
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["message"] == (
        "You may only specify one of these parameters: amount, quantity."
    )
    assert exc_info.value.stripe_body["error"]["param"] == "lines[0][amount]"
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            f"/v1/invoices/{invoice['id']}/add_lines",
            {"lines": [{"quantity": 3}]},
        )
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["param"] == "lines[0][amount]"
    # the single-line update answers the LINE, same id, new one-off price —
    # and emits the family's invoice.updated beside it (the sibling-consistent
    # ruling; see allowed_differences.py)
    events_before = len(
        instance.inspect().rows("SELECT 1 FROM events WHERE type = 'invoice.updated'")
    )
    one = call(
        instance,
        "POST",
        f"/v1/invoices/{invoice['id']}/lines/{line_id}",
        {"amount": 777, "description": "Extra line (revised)"},
    )
    assert (
        one["pricing"]["price_details"]["product"]
        == (added["lines"]["data"][0]["pricing"]["price_details"]["product"])
    )
    assert one["object"] == "line_item"
    assert one["id"] == line_id
    assert one["amount"] == 777
    assert (
        len(instance.inspect().rows("SELECT 1 FROM events WHERE type = 'invoice.updated'"))
        == events_before + 1
    )
    # update_lines answers the invoice
    many = call(
        instance,
        "POST",
        f"/v1/invoices/{invoice['id']}/update_lines",
        {"lines": [{"id": line_id, "amount": 888}]},
    )
    assert many["object"] == "invoice"
    assert many["total"] == 2888
    assert many["lines"]["data"][0]["id"] == line_id  # ids survive rebuilds
    # quantity is a multiplier, not a no-op: unit 888 x 7 bills 6216
    quantified = call(
        instance,
        "POST",
        f"/v1/invoices/{invoice['id']}/lines/{line_id}",
        {"quantity": 7},
    )
    assert quantified["amount"] == 6216
    assert quantified["quantity"] == 7
    via_many = call(
        instance,
        "POST",
        f"/v1/invoices/{invoice['id']}/update_lines",
        {"lines": [{"id": line_id, "quantity": 2}]},
    )
    assert via_many["total"] == 2000 + 888 * 2
    # on add_lines quantity can never multiply: beside `amount` it is the
    # recorded XOR refusal above, and alone it has no unit (live pairs it
    # with `price_data`, which this surface cuts) — the missing-parameter
    # refusal answers it, so the add_lines multiplier is the invoiceitem
    # create's own (see tests/test_invoiceitems.py).
    # behavior is required on remove_lines (recorded refusal)
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            f"/v1/invoices/{invoice['id']}/remove_lines",
            {"lines": [{"id": line_id}]},
        )
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["code"] == "parameter_missing"
    assert exc_info.value.stripe_body["error"]["param"] == "lines[0][behavior]"
    removed = call(
        instance,
        "POST",
        f"/v1/invoices/{invoice['id']}/remove_lines",
        {"lines": [{"id": line_id, "behavior": "delete"}]},
    )
    assert removed["total"] == 2000
    assert len(removed["lines"]["data"]) == 1
    # unassign returns the item to pending instead of deleting it
    re_added = call(
        instance,
        "POST",
        f"/v1/invoices/{invoice['id']}/add_lines",
        {"lines": [{"amount": 111, "description": "borrowed"}]},
    )
    borrow_id = re_added["lines"]["data"][0]["id"]
    call(
        instance,
        "POST",
        f"/v1/invoices/{invoice['id']}/remove_lines",
        {"lines": [{"id": borrow_id, "behavior": "unassign"}]},
    )
    pending = call(instance, "GET", "/v1/invoiceitems", {"customer": customer, "pending": True})
    assert [row["amount"] for row in pending["data"]] == [111]


def test_the_sweep_is_bounded_to_the_invoice_currency(instance, customer) -> None:
    """One currency per invoice: mismatched pending items stay pending for
    a later invoice of their own (the declared unprobed ruling). The
    unparameterized currency follows the NEWEST pending item, then the
    sweep is bounded to it."""
    call(
        instance,
        "POST",
        "/v1/invoiceitems",
        {"customer": customer, "amount": 500, "currency": "eur", "description": "eur fee"},
    )
    item(instance, customer, 1000, "usd fee")
    invoice = call(
        instance,
        "POST",
        "/v1/invoices",
        {"customer": customer, "pending_invoice_items_behavior": "include"},
    )
    assert invoice["currency"] == "usd"  # the newest item's, then bounded
    assert [(line["amount"], line["currency"]) for line in invoice["lines"]["data"]] == [
        (1000, "usd")
    ]
    pending = call(instance, "GET", "/v1/invoiceitems", {"customer": customer, "pending": True})
    assert [row["currency"] for row in pending["data"]] == ["eur"]


def test_the_created_snapshot_carries_the_swept_body(instance, customer) -> None:
    item(instance, customer, 1500, "snapshot")
    call(
        instance,
        "POST",
        "/v1/invoices",
        {
            "customer": customer,
            "description": "described",
            "pending_invoice_items_behavior": "include",
        },
    )
    snapshot = json.loads(
        instance.inspect().one("SELECT data FROM events WHERE type = 'invoice.created'")["data"]
    )["object"]
    assert snapshot["description"] == "described"
    assert [(line["amount"], line["description"]) for line in snapshot["lines"]["data"]] == [
        (1500, "snapshot")
    ]
    assert snapshot["total"] == 1500


def test_a_no_op_update_emits_no_event(instance, customer) -> None:
    invoice = call(instance, "POST", "/v1/invoices", {"customer": customer})
    call(instance, "POST", f"/v1/invoices/{invoice['id']}", {})
    changed = call(instance, "POST", f"/v1/invoices/{invoice['id']}", {"description": "now"})
    assert changed["description"] == "now"
    types = [
        row["type"]
        for row in instance.inspect().rows(
            "SELECT type FROM events WHERE type LIKE 'invoice.%' ORDER BY x_seq"
        )
    ]
    assert types == ["invoice.created", "invoice.updated"]


def test_the_created_events_fire(instance, customer) -> None:
    call(instance, "POST", "/v1/invoices", {"customer": customer})
    types = [
        row["type"]
        for row in instance.inspect().rows(
            "SELECT type FROM events WHERE type LIKE 'invoice.%' ORDER BY x_seq"
        )
    ]
    assert types == ["invoice.created"]
    body = instance.inspect().one("SELECT data FROM events WHERE type = 'invoice.created'")
    snapshot = json.loads(body["data"])["object"]
    assert snapshot["object"] == "invoice"
    assert snapshot["status"] == "draft"
