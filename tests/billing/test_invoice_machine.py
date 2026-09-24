"""The invoice status machine through the routed handlers: every recorded
transition and wrong-state refusal (cassette 13), plus the billing
invariants the machine must hold (I1-I4, I9, I10 as SQL over
`inst.inspect()` — the same queries an eval reward function runs)."""

import json

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
def armed(instance: seahaven.Instance) -> str:
    """A customer whose default method pays synchronously."""
    cus = call(instance, "POST", "/v1/customers", {"email": "m@example.test"})["id"]
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


def draft_with_items(instance: seahaven.Instance, customer: str, amount: int) -> dict:
    call(
        instance,
        "POST",
        "/v1/invoiceitems",
        {"customer": customer, "amount": amount, "currency": "usd", "description": "fee"},
    )
    return call(
        instance,
        "POST",
        "/v1/invoices",
        {"customer": customer, "pending_invoice_items_behavior": "include"},
    )


def test_finalize_assigns_the_number_and_attempts_nothing(instance, armed) -> None:
    invoice = draft_with_items(instance, armed, 3000)
    open_ = call(instance, "POST", f"/v1/invoices/{invoice['id']}/finalize", {})
    assert open_["status"] == "open"
    assert open_["number"].endswith("-0001")
    assert open_["effective_at"] is not None  # null on the draft, stamped here
    assert open_["automatically_finalizes_at"] is None
    assert open_["attempted"] is False  # plain finalize collects nothing
    assert open_["attempt_count"] == 0
    assert open_["amount_due"] == 3000
    assert open_["hosted_invoice_url"].startswith("https://pay.stripe.com/invoice/")
    assert open_["invoice_pdf"].endswith("/pdf")
    assert open_["status_transitions"]["finalized_at"] is not None
    assert open_["status_transitions"]["paid_at"] is None
    # the sequence is per customer and shared by every finalization path
    second = draft_with_items(instance, armed, 1000)
    paid = call(instance, "POST", f"/v1/invoices/{second['id']}/pay", {})
    assert paid["number"].endswith("-0002")


def test_the_zero_amount_finalize_settles_inside_the_call(instance, armed) -> None:
    invoice = call(instance, "POST", "/v1/invoices", {"customer": armed})
    paid = call(instance, "POST", f"/v1/invoices/{invoice['id']}/finalize", {})
    assert paid["status"] == "paid"
    assert paid["attempted"] is True
    assert paid["attempt_count"] == 0
    assert paid["status_transitions"]["paid_at"] == paid["status_transitions"]["finalized_at"]


def test_paying_a_draft_finalizes_and_collects(instance, armed) -> None:
    invoice = draft_with_items(instance, armed, 3500)
    paid = call(instance, "POST", f"/v1/invoices/{invoice['id']}/pay", {})
    assert paid["status"] == "paid"
    assert paid["attempted"] is True
    assert paid["attempt_count"] == 1
    assert paid["amount_paid"] == paid["amount_due"] == 3500
    assert paid["amount_remaining"] == 0
    assert paid["auto_advance"] is False  # paying ends the advancement
    transitions = paid["status_transitions"]
    assert transitions["finalized_at"] == transitions["paid_at"]
    charge = instance.inspect().one(
        "SELECT * FROM charges WHERE payment_intent IS NULL AND status = 'succeeded'"
    )
    assert charge["amount"] == 3500
    ledger = instance.inspect().one("SELECT * FROM balance_transactions WHERE type = 'charge'")
    assert ledger["source"] == charge["id"]


def test_the_out_of_band_pay_attempts_nothing(instance, armed) -> None:
    invoice = draft_with_items(instance, armed, 4400)
    call(instance, "POST", f"/v1/invoices/{invoice['id']}/finalize", {})
    paid = call(instance, "POST", f"/v1/invoices/{invoice['id']}/pay", {"paid_out_of_band": True})
    assert paid["status"] == "paid"
    assert paid["attempted"] is False  # recorded: not even the flag flips
    assert paid["attempt_count"] == 0
    assert paid["amount_paid"] == 4400
    assert instance.inspect().one("SELECT COUNT(*) AS n FROM charges")["n"] == 0


def test_a_declined_pay_returns_the_envelope_and_keeps_every_write(instance) -> None:
    """The phase's headline rule (architecture §7: raise loses, return
    keeps): a declined /pay answers the 402 envelope as a VALUE, and the
    finalization, the failed charge, the attempt counters and the
    `invoice.payment_failed` event all survive the call."""
    cus = call(instance, "POST", "/v1/customers", {"email": "dec@example.test"})["id"]
    dec = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {
            "type": "card",
            "card": {"number": "4000000000000341", "exp_month": 9, "exp_year": 2027, "cvc": "123"},
        },
    )["id"]
    call(instance, "POST", f"/v1/payment_methods/{dec}/attach", {"customer": cus})
    call(
        instance,
        "POST",
        f"/v1/customers/{cus}",
        {"invoice_settings": {"default_payment_method": dec}},
    )
    call(
        instance,
        "POST",
        "/v1/invoiceitems",
        {"customer": cus, "amount": 2500, "currency": "usd", "description": "declined fee"},
    )
    invoice = call(
        instance,
        "POST",
        "/v1/invoices",
        {"customer": cus, "pending_invoice_items_behavior": "include"},
    )
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", f"/v1/invoices/{invoice['id']}/pay", {})
    assert exc_info.value.status == 402
    assert exc_info.value.stripe_body["error"]["type"] == "card_error"
    assert exc_info.value.stripe_body["error"]["code"] == "card_declined"
    # the writes survived: the invoice finalized OPEN with the attempt
    # counted, the failed charge exists, and the event family is complete
    fresh = call(instance, "GET", f"/v1/invoices/{invoice['id']}")
    assert fresh["status"] == "open"
    assert fresh["number"] is not None
    assert fresh["attempted"] is True
    assert fresh["attempt_count"] == 1
    charge = instance.inspect().one(
        "SELECT * FROM charges WHERE status = 'failed' AND payment_intent IS NULL"
    )
    assert charge["amount"] == 2500
    assert (
        instance.inspect().one(
            "SELECT COUNT(*) AS n FROM balance_transactions WHERE type = 'charge'"
        )["n"]
        == 0
    )  # a declined charge writes no ledger row (Phase 8's rule)
    types = [
        row["type"]
        for row in instance.inspect().rows(
            "SELECT type FROM events WHERE type LIKE 'invoice.%' ORDER BY x_seq"
        )
    ]
    assert types == [
        "invoice.created",
        "invoice.finalized",
        "invoice.payment_failed",
    ]


def test_pay_refuses_a_void_or_uncollectible_invoice(instance, armed) -> None:
    """The machine table's dead ends: only `open` collects."""
    invoice = draft_with_items(instance, armed, 900)
    call(instance, "POST", f"/v1/invoices/{invoice['id']}/finalize", {})
    call(instance, "POST", f"/v1/invoices/{invoice['id']}/void", {})
    for params in ({}, {"paid_out_of_band": True}):
        with pytest.raises(StripeToolError) as exc_info:
            call(instance, "POST", f"/v1/invoices/{invoice['id']}/pay", params)
        assert exc_info.value.status == 400
        assert exc_info.value.stripe_body["error"]["message"] == (
            "You can only pass in open invoices. This invoice isn't open."
        )
    after = call(instance, "GET", f"/v1/invoices/{invoice['id']}")
    assert after["status"] == "void"  # out-of-band did not resurrect it
    assert after["status_transitions"]["paid_at"] is None


def test_void_and_mark_uncollectible_from_open(instance, armed) -> None:
    invoice = draft_with_items(instance, armed, 3000)
    call(instance, "POST", f"/v1/invoices/{invoice['id']}/finalize", {})
    voided = call(instance, "POST", f"/v1/invoices/{invoice['id']}/void", {})
    assert voided["status"] == "void"
    assert voided["amount_due"] == 3000  # left as it was (recorded)
    assert voided["amount_remaining"] == 3000
    assert voided["status_transitions"]["voided_at"] is not None
    marked = draft_with_items(instance, armed, 1200)
    call(instance, "POST", f"/v1/invoices/{marked['id']}/finalize", {})
    uncollectible = call(instance, "POST", f"/v1/invoices/{marked['id']}/mark_uncollectible", {})
    assert uncollectible["status"] == "uncollectible"
    assert uncollectible["status_transitions"]["marked_uncollectible_at"] is not None


def test_the_wrong_state_refusals(instance, armed) -> None:
    invoice = draft_with_items(instance, armed, 3500)
    invoice_id = invoice["id"]
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", f"/v1/invoices/{invoice_id}/void", {})
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["message"] == (
        "You can only pass in open invoices. This invoice isn't open."
    )
    call(instance, "POST", f"/v1/invoices/{invoice_id}/pay", {})
    for path, message in (
        ("/pay", "Invoice is already paid"),
        ("/void", "Invoices with `paid` payments cannot be voided."),
        ("/mark_uncollectible", "You can only pass in open invoices. This invoice isn't open."),
        (
            "/finalize",
            "This invoice is already finalized, you can't re-finalize a non-draft invoice.",
        ),
    ):
        with pytest.raises(StripeToolError) as exc_info:
            call(instance, "POST", f"/v1/invoices/{invoice_id}{path}", {})
        assert exc_info.value.status == 400, path
        assert exc_info.value.stripe_body["error"]["message"] == message, path
        assert "code" not in exc_info.value.stripe_body["error"], path
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "DELETE", f"/v1/invoices/{invoice_id}")
    assert exc_info.value.stripe_body["error"]["message"] == "You can only delete draft invoices."
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", f"/v1/invoices/{invoice_id}", {"description": "no"})
    assert (
        exc_info.value.stripe_body["error"]["message"]
        == "Finalized invoices can't be updated in this way"
    )
    assert exc_info.value.stripe_body["error"]["param"] == "description"
    # metadata succeeds where description refused (recorded)
    meta = call(instance, "POST", f"/v1/invoices/{invoice_id}", {"metadata": {"k": "v"}})
    assert meta["metadata"] == {"k": "v"}
    # the re-mark has its own spelling
    other = draft_with_items(instance, armed, 100)
    call(instance, "POST", f"/v1/invoices/{other['id']}/finalize", {})
    call(instance, "POST", f"/v1/invoices/{other['id']}/mark_uncollectible", {})
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", f"/v1/invoices/{other['id']}/mark_uncollectible", {})
    assert exc_info.value.stripe_body["error"]["message"] == (
        "This invoice has already been marked uncollectible."
    )


def test_the_send_family(instance, armed) -> None:
    call(
        instance,
        "POST",
        "/v1/invoiceitems",
        {"customer": armed, "amount": 1200, "currency": "usd", "description": "mailed"},
    )
    invoice = call(
        instance,
        "POST",
        "/v1/invoices",
        {
            "customer": armed,
            "collection_method": "send_invoice",
            "days_until_due": 7,
            "pending_invoice_items_behavior": "include",
        },
    )
    assert invoice["total"] > 0  # stays open through finalize; $0 settles
    # /send on a draft finalizes (a $0 result settles)
    zero = call(
        instance,
        "POST",
        "/v1/invoices",
        {"customer": armed, "collection_method": "send_invoice", "days_until_due": 7},
    )
    sent_draft = call(instance, "POST", f"/v1/invoices/{zero['id']}/send", {})
    assert sent_draft["status"] == "paid"
    assert sent_draft["number"] is not None
    # /send on an open send_invoice answers the unchanged body
    call(instance, "POST", f"/v1/invoices/{invoice['id']}/finalize", {})
    sent_open = call(instance, "POST", f"/v1/invoices/{invoice['id']}/send", {})
    assert sent_open["status"] == "open"
    # /send on a paid invoice refuses with the recorded support-pointer form
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", f"/v1/invoices/{zero['id']}/send", {})
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["message"] == (
        "This invoice cannot be sent right now. Please contact us via "
        "https://support.stripe.com/contact with details, so we can help."
    )


def test_delete_draft_keeps_its_items_attached(instance, armed) -> None:
    invoice = draft_with_items(instance, armed, 800)
    deleted = call(instance, "DELETE", f"/v1/invoices/{invoice['id']}")
    assert deleted == {"id": invoice["id"], "object": "invoice", "deleted": True}
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "GET", f"/v1/invoices/{invoice['id']}")
    assert exc_info.value.status == 404
    # recorded: nothing is released, and the pending view is empty
    pending = call(instance, "GET", "/v1/invoiceitems", {"customer": armed, "pending": True})
    assert pending["data"] == []
    attached = instance.inspect().one("SELECT invoice FROM invoiceitems WHERE customer = ?", armed)
    assert attached["invoice"] == invoice["id"]


def test_the_customer_balance_settles_at_finalization(instance) -> None:
    credited = call(
        instance,
        "POST",
        "/v1/customers",
        {"email": "bal@example.test", "balance": -1000},
    )
    pm = call(
        instance, "POST", "/v1/payment_methods", {"type": "card", "card": {"token": "tok_visa"}}
    )["id"]
    call(instance, "POST", f"/v1/payment_methods/{pm}/attach", {"customer": credited["id"]})
    call(
        instance,
        "POST",
        f"/v1/customers/{credited['id']}",
        {"invoice_settings": {"default_payment_method": pm}},
    )
    invoice = draft_with_items(instance, credited["id"], 3000)
    paid = call(instance, "POST", f"/v1/invoices/{invoice['id']}/pay", {})
    # §3.5 worked case: credit consumed, due reduced, cbt row written
    assert paid["starting_balance"] == -1000
    assert paid["amount_due"] == 2000
    assert paid["ending_balance"] == 0
    assert paid["amount_paid"] == 2000
    cbt = instance.inspect().one(
        "SELECT * FROM customer_balance_transactions WHERE invoice = ?", invoice["id"]
    )
    assert cbt["type"] == "applied_to_invoice"
    assert cbt["amount"] == 1000
    fresh = call(instance, "GET", f"/v1/customers/{credited['id']}")
    assert fresh["balance"] == 0


def test_invoice_events_through_the_machine(instance, armed) -> None:
    invoice = draft_with_items(instance, armed, 2500)
    invoice_id = invoice["id"]
    call(instance, "POST", f"/v1/invoices/{invoice_id}/finalize", {})
    call(instance, "POST", f"/v1/invoices/{invoice_id}/void", {})
    types = [
        row["type"]
        for row in instance.inspect().rows(
            "SELECT type FROM events WHERE type LIKE 'invoice.%' ORDER BY x_seq"
        )
    ]
    assert types == ["invoice.created", "invoice.finalized", "invoice.voided"]
    snapshot = json.loads(
        instance.inspect().one("SELECT data FROM events WHERE type = 'invoice.voided'")["data"]
    )["object"]
    assert snapshot["status"] == "void"


# --- the invariants (SQL an eval reward function can run verbatim) -------------------


def seeded(instance: seahaven.Instance, armed: str) -> None:
    """A small state spread: paid, open, draft, void, uncollectible."""
    one = draft_with_items(instance, armed, 3000)["id"]
    call(instance, "POST", f"/v1/invoices/{one}/pay", {})
    two = draft_with_items(instance, armed, 1200)["id"]
    call(instance, "POST", f"/v1/invoices/{two}/finalize", {})
    call(instance, "POST", f"/v1/invoices/{two}/void", {})
    three = draft_with_items(instance, armed, 500)["id"]
    call(instance, "POST", f"/v1/invoices/{three}/finalize", {})
    call(instance, "POST", f"/v1/invoices/{three}/mark_uncollectible", {})
    draft_with_items(instance, armed, 100)


def test_i1_lines_sum_to_the_subtotal(instance, armed) -> None:
    seeded(instance, armed)
    rows = instance.inspect().rows(
        """
        SELECT i.id, i.subtotal, SUM(json_extract(l.value, '$.amount')) AS line_sum
        FROM invoices i, json_each(i.lines) l
        GROUP BY i.id, i.subtotal
        HAVING SUM(json_extract(l.value, '$.amount')) <> i.subtotal
        """
    )
    assert rows == []


def test_i3_amount_remaining_is_derived(instance, armed) -> None:
    seeded(instance, armed)
    rows = instance.inspect().rows(
        "SELECT id FROM invoices WHERE amount_remaining <> amount_due - amount_paid"
    )
    assert rows == []


def test_i4_paid_invoices_are_settled_and_stamped(instance, armed) -> None:
    seeded(instance, armed)
    rows = instance.inspect().rows(
        """
        SELECT id FROM invoices
        WHERE (status =  'paid' AND (amount_remaining <> 0 OR
               json_extract(status_transitions, '$.paid_at') IS NULL))
           OR (status <> 'paid' AND
               json_extract(status_transitions, '$.paid_at') IS NOT NULL)
        """
    )
    assert rows == []


def test_i9_the_finalize_window_is_populated_exactly_when_it_should_be(instance, armed) -> None:
    seeded(instance, armed)
    call(
        instance,
        "POST",
        "/v1/invoices",
        {"customer": armed, "auto_advance": True},
    )
    rows = instance.inspect().rows(
        """
        SELECT id FROM invoices
        WHERE (status <> 'draft' AND automatically_finalizes_at IS NOT NULL)
           OR (status =  'draft' AND auto_advance = 0
               AND automatically_finalizes_at IS NOT NULL)
           OR (status =  'draft' AND auto_advance = 1
               AND automatically_finalizes_at IS NULL)
        """
    )
    assert rows == []


def test_i10_attempted_and_the_counter_agree(instance, armed) -> None:
    seeded(instance, armed)
    rows = instance.inspect().rows(
        """
        SELECT id FROM invoices
        WHERE (attempted = 1 AND attempt_count < 1)
           OR (attempted = 0 AND attempt_count > 0)
        """
    )
    assert rows == []


def test_the_total_identity_holds(instance, armed) -> None:
    seeded(instance, armed)
    rows = instance.inspect().rows(
        """
        SELECT id FROM invoices
        WHERE total <> subtotal
              - COALESCE((
                    SELECT SUM(json_extract(d.value, '$.amount'))
                    FROM json_each(total_discount_amounts) d
                ), 0)
              + COALESCE((
                    SELECT SUM(json_extract(t.value, '$.amount'))
                    FROM json_each(total_taxes) t
                    WHERE json_extract(t.value, '$.tax_behavior') = 'exclusive'
                ), 0)
        """
    )
    assert rows == []


def test_no_iso_timestamps_inside_the_lines_json(instance, armed) -> None:
    seeded(instance, armed)
    rows = instance.inspect().rows(
        """
        SELECT i.id FROM invoices i, json_tree(i.lines) t
        WHERE t.type = 'text' AND t.value LIKE '____-__-__T__:__:__.___Z'
        """
    )
    assert rows == []
