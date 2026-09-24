"""Credit notes and customer balance transactions (Phase 15): the
three-channel settlement model, credit note numbering, the void lifecycle,
and the customer-scoped balance transaction API.

Every assertion through ``instance.call`` exercises the real four-tool
chain, the schema conformance validator, and the ``credit_note`` /
``customer_balance_transaction`` serializers end to end.
"""

import pytest
import seahaven

from conftest import BLANK_NOW, api_read, api_write
from seahaven_stripe_world.errors import StripeToolError

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW, clock_mode="fixed")


def call(instance: seahaven.Instance, method: str, path: str, params: dict | None = None):
    if method == "GET":
        return api_read(instance, path, params)
    return api_write(instance, method, path, params)


@pytest.fixture()
def customer(instance: seahaven.Instance) -> str:
    return call(instance, "POST", "/v1/customers", {"email": "cn@example.test"})["id"]


@pytest.fixture()
def product(instance: seahaven.Instance) -> str:
    return call(instance, "POST", "/v1/products", {"name": "Widget"})["id"]


@pytest.fixture()
def price(instance: seahaven.Instance, product: str) -> str:
    return call(
        instance,
        "POST",
        "/v1/prices",
        {
            "product": product,
            "unit_amount": 2000,
            "currency": "usd",
            "recurring": {"interval": "month"},
        },
    )["id"]


@pytest.fixture()
def pm(instance: seahaven.Instance, customer: str) -> str:
    pm_body = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"number": "4242424242424242", "exp_month": 12, "exp_year": 2030}},
    )
    call(
        instance,
        "POST",
        f"/v1/payment_methods/{pm_body['id']}/attach",
        {"customer": customer},
    )
    # Set as default for invoices
    call(
        instance,
        "POST",
        f"/v1/customers/{customer}",
        {"invoice_settings": {"default_payment_method": pm_body["id"]}},
    )
    return pm_body["id"]


@pytest.fixture()
def paid_invoice(instance: seahaven.Instance, customer: str, price: str, pm: str) -> str:
    """Create a subscription (which creates and pays an invoice), return the invoice id."""
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": customer, "items": [{"price": price}]},
    )
    assert sub["status"] == "active"
    return sub["latest_invoice"]


@pytest.fixture()
def open_invoice(instance: seahaven.Instance, customer: str) -> str:
    """A manual invoice, finalized but not paid."""
    inv = call(
        instance,
        "POST",
        "/v1/invoices",
        {"customer": customer, "pending_invoice_items_behavior": "exclude"},
    )
    # Add a line item
    call(
        instance,
        "POST",
        f"/v1/invoices/{inv['id']}/add_lines",
        {"lines": [{"amount": 5000, "description": "Service fee"}]},
    )
    # Finalize
    finalized = call(instance, "POST", f"/v1/invoices/{inv['id']}/finalize")
    assert finalized["status"] == "open"
    return inv["id"]


# ---------------------------------------------------------------------------
# Credit note creation
# ---------------------------------------------------------------------------


def test_create_credit_note_on_paid_invoice(instance, paid_invoice) -> None:
    """Post-payment credit note: default settlement is refund."""
    call(instance, "GET", f"/v1/invoices/{paid_invoice}")
    body = call(
        instance,
        "POST",
        "/v1/credit_notes",
        {"invoice": paid_invoice, "amount": 500},
    )
    assert body["object"] == "credit_note"
    assert body["status"] == "issued"
    assert body["amount"] == 500
    assert body["type"] == "post_payment"
    assert body["post_payment_amount"] == 500
    assert body["pre_payment_amount"] == 0
    assert body["currency"] == "usd"
    assert body["invoice"] == paid_invoice
    assert body["livemode"] is True
    assert body["id"].startswith("cn_")
    # Number follows the pattern
    assert "-CN-1" in body["number"]
    # PDF is the derived URL
    assert body["pdf"] == f"https://pay.stripe.com/credit_notes/{body['id']}/pdf"
    # Lines envelope
    assert body["lines"]["object"] == "list"
    assert len(body["lines"]["data"]) == 1
    line = body["lines"]["data"][0]
    assert line["object"] == "credit_note_line_item"
    assert line["amount"] == 500
    assert line["id"].startswith("cnli_")
    # The invoice's post_payment_credit_notes_amount is updated
    inv_after = call(instance, "GET", f"/v1/invoices/{paid_invoice}")
    assert inv_after["post_payment_credit_notes_amount"] == 500


def test_create_credit_note_on_open_invoice(instance, open_invoice) -> None:
    """Pre-payment credit note: reduces amount_remaining on the invoice."""
    inv_before = call(instance, "GET", f"/v1/invoices/{open_invoice}")
    remaining_before = inv_before["amount_remaining"]
    body = call(
        instance,
        "POST",
        "/v1/credit_notes",
        {"invoice": open_invoice, "amount": 1000},
    )
    assert body["type"] == "pre_payment"
    assert body["pre_payment_amount"] == 1000
    assert body["post_payment_amount"] == 0
    # Invoice amount_remaining reduced
    inv_after = call(instance, "GET", f"/v1/invoices/{open_invoice}")
    assert inv_after["amount_remaining"] == remaining_before - 1000
    assert inv_after["pre_payment_credit_notes_amount"] == 1000


def test_create_credit_note_with_credit_amount(instance, paid_invoice, customer) -> None:
    """Post-payment credit note with credit_amount writes a cbt row."""
    body = call(
        instance,
        "POST",
        "/v1/credit_notes",
        {"invoice": paid_invoice, "credit_amount": 300, "amount": 300},
    )
    assert body["customer_balance_transaction"] is not None
    cbt_id = body["customer_balance_transaction"]
    assert cbt_id.startswith("cbtxn_")
    # The cbt row is visible
    cbt = call(
        instance,
        "GET",
        f"/v1/customers/{customer}/balance_transactions/{cbt_id}",
    )
    assert cbt["type"] == "credit_note"
    assert cbt["amount"] == -300  # negative = credit
    assert cbt["credit_note"] == body["id"]
    # Customer balance decreased (credit applied)
    cus = call(instance, "GET", f"/v1/customers/{customer}")
    assert cus["balance"] == -300


def test_create_credit_note_with_out_of_band(instance, paid_invoice) -> None:
    """Out-of-band settlement records the amount."""
    body = call(
        instance,
        "POST",
        "/v1/credit_notes",
        {"invoice": paid_invoice, "out_of_band_amount": 400, "amount": 400},
    )
    assert body["out_of_band_amount"] == 400


def test_create_credit_note_with_lines(instance, paid_invoice) -> None:
    """Lines provided explicitly."""
    body = call(
        instance,
        "POST",
        "/v1/credit_notes",
        {
            "invoice": paid_invoice,
            "lines": [
                {"type": "custom_line_item", "amount": 200, "description": "Partial refund"},
                {"type": "custom_line_item", "amount": 300, "description": "Adjustment"},
            ],
        },
    )
    assert body["amount"] == 500
    assert len(body["lines"]["data"]) == 2
    assert body["lines"]["data"][0]["description"] == "Partial refund"
    assert body["lines"]["data"][1]["description"] == "Adjustment"


def test_credit_note_amount_cannot_exceed_creditable(instance, paid_invoice) -> None:
    """Cannot credit more than the invoice was paid for."""
    inv = call(instance, "GET", f"/v1/invoices/{paid_invoice}")
    over_amount = inv["amount_paid"] + 1
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            "/v1/credit_notes",
            {"invoice": paid_invoice, "amount": over_amount},
        )
    assert exc_info.value.status == 400


def test_credit_note_on_draft_invoice_refused(instance, customer) -> None:
    """Credit notes cannot be created on draft invoices."""
    inv = call(
        instance,
        "POST",
        "/v1/invoices",
        {"customer": customer, "pending_invoice_items_behavior": "exclude"},
    )
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            "/v1/credit_notes",
            {"invoice": inv["id"], "amount": 100},
        )
    assert exc_info.value.status == 400


def test_refund_refused_on_out_of_band_paid_invoice(instance, customer) -> None:
    """Requesting refund_amount on an invoice paid out-of-band (no charge)
    is an error -- there is no charge to refund."""
    inv = call(
        instance,
        "POST",
        "/v1/invoices",
        {"customer": customer, "pending_invoice_items_behavior": "exclude"},
    )
    call(
        instance,
        "POST",
        f"/v1/invoices/{inv['id']}/add_lines",
        {"lines": [{"amount": 3000, "description": "Consulting"}]},
    )
    call(instance, "POST", f"/v1/invoices/{inv['id']}/finalize")
    # Pay out-of-band (no charge created)
    call(instance, "POST", f"/v1/invoices/{inv['id']}/pay", {"paid_out_of_band": True})
    inv_paid = call(instance, "GET", f"/v1/invoices/{inv['id']}")
    assert inv_paid["status"] == "paid"
    # Requesting refund_amount should fail
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            "/v1/credit_notes",
            {"invoice": inv["id"], "refund_amount": 1000, "amount": 1000},
        )
    assert exc_info.value.status == 400
    assert "charge" in exc_info.value.stripe_body["error"]["message"].lower()
    # credit_amount or out_of_band_amount should still work
    cn = call(
        instance,
        "POST",
        "/v1/credit_notes",
        {"invoice": inv["id"], "out_of_band_amount": 1000, "amount": 1000},
    )
    assert cn["status"] == "issued"
    assert cn["out_of_band_amount"] == 1000


# ---------------------------------------------------------------------------
# Credit note numbering
# ---------------------------------------------------------------------------


def test_credit_note_numbering(instance, paid_invoice) -> None:
    """Three credit notes against one invoice are -CN-1, -CN-2, -CN-3,
    and voiding the second does not renumber the third (data_model test)."""
    cn1 = call(
        instance,
        "POST",
        "/v1/credit_notes",
        {"invoice": paid_invoice, "amount": 100},
    )
    cn2 = call(
        instance,
        "POST",
        "/v1/credit_notes",
        {"invoice": paid_invoice, "amount": 100},
    )
    cn3 = call(
        instance,
        "POST",
        "/v1/credit_notes",
        {"invoice": paid_invoice, "amount": 100},
    )
    assert cn1["number"].endswith("-CN-1")
    assert cn2["number"].endswith("-CN-2")
    assert cn3["number"].endswith("-CN-3")
    # Void cn2 — cn3's number stays unchanged
    call(instance, "POST", f"/v1/credit_notes/{cn2['id']}/void")
    cn3_after = call(instance, "GET", f"/v1/credit_notes/{cn3['id']}")
    assert cn3_after["number"].endswith("-CN-3")


# ---------------------------------------------------------------------------
# Credit note update
# ---------------------------------------------------------------------------


def test_update_credit_note_memo(instance, paid_invoice) -> None:
    """Update memo on an existing credit note."""
    cn = call(
        instance,
        "POST",
        "/v1/credit_notes",
        {"invoice": paid_invoice, "amount": 200, "memo": "Original"},
    )
    assert cn["memo"] == "Original"
    updated = call(
        instance,
        "POST",
        f"/v1/credit_notes/{cn['id']}",
        {"memo": "Updated memo"},
    )
    assert updated["memo"] == "Updated memo"


def test_update_credit_note_metadata(instance, paid_invoice) -> None:
    """Update metadata on an existing credit note."""
    cn = call(
        instance,
        "POST",
        "/v1/credit_notes",
        {"invoice": paid_invoice, "amount": 200},
    )
    updated = call(
        instance,
        "POST",
        f"/v1/credit_notes/{cn['id']}",
        {"metadata": {"key": "value"}},
    )
    assert updated["metadata"] == {"key": "value"}


# ---------------------------------------------------------------------------
# Credit note void
# ---------------------------------------------------------------------------


def test_void_credit_note(instance, paid_invoice) -> None:
    """Void an issued credit note."""
    cn = call(
        instance,
        "POST",
        "/v1/credit_notes",
        {"invoice": paid_invoice, "amount": 200},
    )
    assert cn["status"] == "issued"
    voided = call(instance, "POST", f"/v1/credit_notes/{cn['id']}/void")
    assert voided["status"] == "void"
    assert voided["voided_at"] is not None


def test_void_already_voided_is_refused(instance, paid_invoice) -> None:
    """Cannot void an already-voided credit note."""
    cn = call(
        instance,
        "POST",
        "/v1/credit_notes",
        {"invoice": paid_invoice, "amount": 200},
    )
    call(instance, "POST", f"/v1/credit_notes/{cn['id']}/void")
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", f"/v1/credit_notes/{cn['id']}/void")
    assert exc_info.value.status == 400


def test_void_reverses_invoice_amount(instance, open_invoice) -> None:
    """Voiding a pre-payment credit note restores the invoice amounts."""
    inv_before = call(instance, "GET", f"/v1/invoices/{open_invoice}")
    cn = call(
        instance,
        "POST",
        "/v1/credit_notes",
        {"invoice": open_invoice, "amount": 1000},
    )
    inv_mid = call(instance, "GET", f"/v1/invoices/{open_invoice}")
    assert inv_mid["amount_remaining"] == inv_before["amount_remaining"] - 1000
    call(instance, "POST", f"/v1/credit_notes/{cn['id']}/void")
    inv_after = call(instance, "GET", f"/v1/invoices/{open_invoice}")
    assert inv_after["amount_remaining"] == inv_before["amount_remaining"]
    assert inv_after["pre_payment_credit_notes_amount"] == 0


def test_void_reverses_customer_balance(instance, paid_invoice, customer) -> None:
    """Voiding a credit_amount credit note writes a reversing CBT and
    restores the customer's balance to its prior value."""
    cus_before = call(instance, "GET", f"/v1/customers/{customer}")
    balance_before = cus_before["balance"]
    # Create a credit note that settles via customer balance
    cn = call(
        instance,
        "POST",
        "/v1/credit_notes",
        {"invoice": paid_invoice, "credit_amount": 600, "amount": 600},
    )
    cbt_id = cn["customer_balance_transaction"]
    assert cbt_id is not None
    # Balance decreased by 600
    cus_mid = call(instance, "GET", f"/v1/customers/{customer}")
    assert cus_mid["balance"] == balance_before - 600
    # Void the credit note
    call(instance, "POST", f"/v1/credit_notes/{cn['id']}/void")
    # Customer balance restored
    cus_after = call(instance, "GET", f"/v1/customers/{customer}")
    assert cus_after["balance"] == balance_before
    # A reversing CBT was written
    txns = call(
        instance,
        "GET",
        f"/v1/customers/{customer}/balance_transactions",
    )["data"]
    # The most recent CBT should be the reversal (positive amount)
    reversal = txns[0]
    assert reversal["amount"] == 600  # opposite of the original -600
    assert reversal["type"] == "credit_note"
    assert reversal["credit_note"] == cn["id"]


def test_refund_targets_correct_invoice_charge(instance, customer, price, pm) -> None:
    """When a customer has two paid invoices, a credit note against the
    first refunds the first invoice's charge, not the second."""
    # Create two subscriptions to get two paid invoices
    sub1 = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": customer, "items": [{"price": price}]},
    )
    inv1_id = sub1["latest_invoice"]
    # Create a second price and subscription for a second charge
    product2 = call(instance, "POST", "/v1/products", {"name": "Gadget"})["id"]
    price2 = call(
        instance,
        "POST",
        "/v1/prices",
        {
            "product": product2,
            "unit_amount": 3000,
            "currency": "usd",
            "recurring": {"interval": "month"},
        },
    )["id"]
    sub2 = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": customer, "items": [{"price": price2}]},
    )
    inv2_id = sub2["latest_invoice"]
    assert inv1_id != inv2_id
    # Credit note against the first invoice (amount 500, paid for 2000)
    cn = call(
        instance,
        "POST",
        "/v1/credit_notes",
        {"invoice": inv1_id, "refund_amount": 500, "amount": 500},
    )
    # The refund should target the charge for inv1 (2000), not inv2 (3000)
    assert len(cn["refunds"]) == 1
    refund_id = cn["refunds"][0]["refund"]
    refund_obj = call(instance, "GET", f"/v1/refunds/{refund_id}")
    assert refund_obj["amount"] == 500
    # The charge refunded should be for 2000, not 3000
    charge_obj = call(instance, "GET", f"/v1/charges/{refund_obj['charge']}")
    assert charge_obj["amount"] == 2000


# ---------------------------------------------------------------------------
# Credit note list and retrieve
# ---------------------------------------------------------------------------


def test_list_credit_notes(instance, paid_invoice) -> None:
    """List credit notes, optionally filtering by invoice."""
    call(
        instance,
        "POST",
        "/v1/credit_notes",
        {"invoice": paid_invoice, "amount": 100},
    )
    resp = call(instance, "GET", "/v1/credit_notes", {"invoice": paid_invoice})
    assert resp["object"] == "list"
    assert len(resp["data"]) >= 1


def test_retrieve_credit_note(instance, paid_invoice) -> None:
    """Retrieve a specific credit note."""
    cn = call(
        instance,
        "POST",
        "/v1/credit_notes",
        {"invoice": paid_invoice, "amount": 100},
    )
    retrieved = call(instance, "GET", f"/v1/credit_notes/{cn['id']}")
    assert retrieved["id"] == cn["id"]
    assert retrieved["amount"] == 100


def test_retrieve_missing_credit_note(instance) -> None:
    """404 for a non-existent credit note."""
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "GET", "/v1/credit_notes/cn_nonexistent")
    assert exc_info.value.status == 404


# ---------------------------------------------------------------------------
# Credit note lines
# ---------------------------------------------------------------------------


def test_list_credit_note_lines(instance, paid_invoice) -> None:
    """Paginate the embedded lines of a credit note."""
    cn = call(
        instance,
        "POST",
        "/v1/credit_notes",
        {"invoice": paid_invoice, "amount": 500},
    )
    resp = call(instance, "GET", f"/v1/credit_notes/{cn['id']}/lines")
    assert resp["object"] == "list"
    assert len(resp["data"]) == 1
    assert resp["data"][0]["object"] == "credit_note_line_item"


# ---------------------------------------------------------------------------
# Credit note preview
# ---------------------------------------------------------------------------


def test_preview_credit_note(instance, paid_invoice) -> None:
    """Preview a credit note without persisting it."""
    resp = call(
        instance,
        "GET",
        "/v1/credit_notes/preview",
        {"invoice": paid_invoice, "amount": 300},
    )
    body = resp
    assert body["object"] == "credit_note"
    assert body["amount"] == 300
    # No row persisted
    list_resp = call(instance, "GET", "/v1/credit_notes", {"invoice": paid_invoice})
    assert len(list_resp["data"]) == 0


def test_preview_lines(instance, paid_invoice) -> None:
    """Preview credit note lines without persisting."""
    resp = call(
        instance,
        "GET",
        "/v1/credit_notes/preview/lines",
        {"invoice": paid_invoice, "amount": 300},
    )
    assert resp["object"] == "list"


# ---------------------------------------------------------------------------
# Customer balance transactions
# ---------------------------------------------------------------------------


def test_create_customer_balance_transaction(instance, customer) -> None:
    """Create an adjustment balance transaction."""
    body = call(
        instance,
        "POST",
        f"/v1/customers/{customer}/balance_transactions",
        {"amount": -500, "currency": "usd", "description": "Courtesy credit"},
    )
    assert body["object"] == "customer_balance_transaction"
    assert body["amount"] == -500
    assert body["type"] == "adjustment"
    assert body["ending_balance"] == -500
    assert body["currency"] == "usd"
    assert body["description"] == "Courtesy credit"
    assert body["id"].startswith("cbtxn_")
    assert body["livemode"] is True
    # Customer balance updated
    cus = call(instance, "GET", f"/v1/customers/{customer}")
    assert cus["balance"] == -500


def test_create_positive_balance_transaction(instance, customer) -> None:
    """Positive amount = customer owes more."""
    call(
        instance,
        "POST",
        f"/v1/customers/{customer}/balance_transactions",
        {"amount": 1000, "currency": "usd"},
    )
    cus = call(instance, "GET", f"/v1/customers/{customer}")
    assert cus["balance"] == 1000


def test_list_customer_balance_transactions(instance, customer) -> None:
    """List scoped to one customer."""
    call(
        instance,
        "POST",
        f"/v1/customers/{customer}/balance_transactions",
        {"amount": -100, "currency": "usd"},
    )
    call(
        instance,
        "POST",
        f"/v1/customers/{customer}/balance_transactions",
        {"amount": -200, "currency": "usd"},
    )
    resp = call(instance, "GET", f"/v1/customers/{customer}/balance_transactions")
    data = resp["data"]
    assert len(data) >= 2
    # Newest first
    assert data[0]["amount"] == -200
    assert data[1]["amount"] == -100


def test_retrieve_customer_balance_transaction(instance, customer) -> None:
    """Retrieve a specific balance transaction."""
    created = call(
        instance,
        "POST",
        f"/v1/customers/{customer}/balance_transactions",
        {"amount": -300, "currency": "usd"},
    )
    retrieved = call(
        instance,
        "GET",
        f"/v1/customers/{customer}/balance_transactions/{created['id']}",
    )
    assert retrieved["id"] == created["id"]
    assert retrieved["amount"] == -300


def test_update_customer_balance_transaction(instance, customer) -> None:
    """Update description and metadata."""
    created = call(
        instance,
        "POST",
        f"/v1/customers/{customer}/balance_transactions",
        {"amount": -400, "currency": "usd", "description": "Original"},
    )
    updated = call(
        instance,
        "POST",
        f"/v1/customers/{customer}/balance_transactions/{created['id']}",
        {"description": "Updated", "metadata": {"foo": "bar"}},
    )
    assert updated["description"] == "Updated"
    assert updated["metadata"] == {"foo": "bar"}


def test_customer_balance_matches_ledger(instance, customer) -> None:
    """I14: customer balance equals sum of customer_balance_transactions
    amounts (data_model invariant I14)."""
    call(
        instance,
        "POST",
        f"/v1/customers/{customer}/balance_transactions",
        {"amount": -100, "currency": "usd"},
    )
    call(
        instance,
        "POST",
        f"/v1/customers/{customer}/balance_transactions",
        {"amount": 50, "currency": "usd"},
    )
    cus = call(instance, "GET", f"/v1/customers/{customer}")
    assert cus["balance"] == -50
    # The ending_balance of the last cbt should equal the customer's balance
    txns = call(
        instance,
        "GET",
        f"/v1/customers/{customer}/balance_transactions",
    )["data"]
    assert txns[0]["ending_balance"] == -50


def test_balance_transaction_on_wrong_customer_is_404(instance) -> None:
    """Retrieving a cbt under the wrong customer is a 404."""
    cus1 = call(instance, "POST", "/v1/customers", {"email": "a@test.com"})["id"]
    cus2 = call(instance, "POST", "/v1/customers", {"email": "b@test.com"})["id"]
    cbt = call(
        instance,
        "POST",
        f"/v1/customers/{cus1}/balance_transactions",
        {"amount": -100, "currency": "usd"},
    )
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "GET",
            f"/v1/customers/{cus2}/balance_transactions/{cbt['id']}",
        )
    # Scoped retrieve: the engine's scope-clause should filter it out
    assert exc_info.value.status == 404


# ---------------------------------------------------------------------------
# Integration: credit note + customer balance transaction
# ---------------------------------------------------------------------------


def test_credit_note_with_credit_creates_cbt_and_updates_balance(
    instance, paid_invoice, customer
) -> None:
    """The credit_amount settlement channel writes a cbt row of type
    ``credit_note`` and decreases the customer's balance (negative = credit).
    The two objects cross-reference each other."""
    cn = call(
        instance,
        "POST",
        "/v1/credit_notes",
        {"invoice": paid_invoice, "credit_amount": 500, "amount": 500},
    )
    # The credit note points at the cbt
    cbt_id = cn["customer_balance_transaction"]
    assert cbt_id is not None
    # The cbt points back at the credit note
    cbt = call(
        instance,
        "GET",
        f"/v1/customers/{customer}/balance_transactions/{cbt_id}",
    )
    assert cbt["credit_note"] == cn["id"]
    assert cbt["type"] == "credit_note"
    assert cbt["amount"] == -500
    # Customer balance decreased by the credit
    cus = call(instance, "GET", f"/v1/customers/{customer}")
    assert cus["balance"] == -500


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def test_credit_note_events(instance, paid_invoice) -> None:
    """Credit note create/update/void each emit the correct event type."""
    cn = call(
        instance,
        "POST",
        "/v1/credit_notes",
        {"invoice": paid_invoice, "amount": 200},
    )
    call(
        instance,
        "POST",
        f"/v1/credit_notes/{cn['id']}",
        {"memo": "Updated"},
    )
    call(instance, "POST", f"/v1/credit_notes/{cn['id']}/void")
    event_types = [
        row["type"]
        for row in instance.inspect().rows(
            "SELECT type FROM events WHERE type LIKE 'credit_note.%' ORDER BY x_seq"
        )
    ]
    assert "credit_note.created" in event_types
    assert "credit_note.updated" in event_types
    assert "credit_note.voided" in event_types
