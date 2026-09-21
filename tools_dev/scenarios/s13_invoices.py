"""Scenario 13 (functional spec §12, the invoices slice): the five-status
invoice machine behind the four tools, the invoiceitem resource, and the
draft-editing line endpoints.

The customer holds a `tok_visa` card as its default method so /pay succeeds
synchronously (on a draft too — paying a draft finalizes and collects inside
the one call, probed); `paid_out_of_band` supplies the charge-free paid path;
drafts cover both `auto_advance` values so `automatically_finalizes_at` and
`next_payment_attempt` appear on the wire; every wrong-state refusal
(void/mark_uncollectible/finalize/delete) is recorded against each status it
refuses, one dedicated draft per probe.
"""

from tools_dev.scenarios._dsl import ref

SCENARIO = "13_invoices"
DESCRIPTION = (
    "Invoices: manual create (default-exclude and include), the draft window "
    "fields, pay-a-draft, finalize/pay/void/mark_uncollectible and their "
    "wrong-state refusals, delete-draft and delete-paid, send_invoice with "
    "/send, the invoiceitem CRUD with its pending filter, add_lines/"
    "update_lines/remove_lines on drafts, the lines sub-list, and the "
    "recorded parameter-error spellings."
)

_EMAIL = "s13@conformance.stripeapi.invalid"


def record(r):
    customer = r.step(
        "POST",
        "/v1/customers",
        {"email": _EMAIL, "description": "scenario 13"},
        binds_as="customer",
    )
    visa = r.step(
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_visa"}},
        binds_as="visa",
    )
    r.step(
        "POST",
        "/v1/payment_methods/{payment_method}/attach",
        {"customer": customer},
        path_refs={"payment_method": visa},
    )
    r.step(
        "POST",
        "/v1/customers/{customer}",
        {"invoice_settings": {"default_payment_method": visa}},
        path_refs={"customer": customer},
    )
    # --- invoiceitems: pending rows, newest-first list, retrieve, update ---
    r.step(
        "POST",
        "/v1/invoiceitems",
        {
            "customer": customer,
            "amount": 2000,
            "currency": "usd",
            "description": "One-time setup fee",
        },
        binds_as="ii1",
    )
    ii2 = r.step(
        "POST",
        "/v1/invoiceitems",
        {"customer": customer, "amount": 1500, "currency": "usd", "description": "Usage add-on"},
        binds_as="ii2",
    )
    r.step("GET", "/v1/invoiceitems", {"customer": customer, "limit": 5})
    r.step("GET", "/v1/invoiceitems", {"customer": customer, "pending": True})
    r.step(
        "GET",
        "/v1/invoiceitems/{invoiceitem}",
        path_refs={"invoiceitem": ii2},
    )
    r.step(
        "POST",
        "/v1/invoiceitems/{invoiceitem}",
        {"description": "Usage add-on (revised)"},
        path_refs={"invoiceitem": ii2},
    )
    r.step("GET", "/v1/invoiceitems/ii_nope")
    # --- the manual create: default-exclude, then include ---
    empty = r.step(
        "POST",
        "/v1/invoices",
        {"customer": customer},
        binds_as="empty",
    )
    r.step(
        "DELETE",
        "/v1/invoices/{invoice}",
        path_refs={"invoice": empty},
    )
    inv = r.step(
        "POST",
        "/v1/invoices",
        {"customer": customer, "pending_invoice_items_behavior": "include"},
        binds_as="inv",
    )
    r.step(
        "GET",
        "/v1/invoices/{invoice}",
        path_refs={"invoice": inv},
    )
    r.step(
        "GET",
        "/v1/invoices/{invoice}/lines",
        path_refs={"invoice": inv},
    )
    r.step("GET", "/v1/invoices", {"customer": customer, "limit": 5})
    r.step("GET", "/v1/invoices", {"customer": customer, "status": "draft"})
    r.step("GET", "/v1/invoices", {"status": "bogus"})
    # The draft window: auto_advance=true sets both scheduled timestamps.
    ii3 = r.step(
        "POST",
        "/v1/invoiceitems",
        {"customer": customer, "amount": 800, "currency": "usd", "description": "Late fee"},
        binds_as="ii3",
    )
    autoinv = r.step(
        "POST",
        "/v1/invoices",
        {"customer": customer, "auto_advance": True, "pending_invoice_items_behavior": "include"},
        binds_as="autoinv",
    )
    r.step(
        "POST",
        "/v1/invoices/{invoice}",
        {"auto_advance": False},
        path_refs={"invoice": autoinv},
    )
    r.step(
        "POST",
        "/v1/invoices/{invoice}",
        {"auto_advance": True},
        path_refs={"invoice": autoinv},
    )
    # Deleting a draft does NOT release its swept items (recorded — they
    # stay attached to the dead invoice, refusing later deletes).
    r.step(
        "DELETE",
        "/v1/invoices/{invoice}",
        path_refs={"invoice": autoinv},
    )
    r.step("GET", "/v1/invoiceitems", {"customer": customer, "pending": True})
    # --- paying a draft finalizes and collects inside the one call ---
    r.step(
        "POST",
        "/v1/invoices/{invoice}/pay",
        {},
        path_refs={"invoice": inv},
        binds_as="paidinv",
    )
    r.step(
        "POST",
        "/v1/invoices/{invoice}/pay",
        {},
        path_refs={"invoice": inv},
    )
    r.step(
        "POST",
        "/v1/invoices/{invoice}/void",
        {},
        path_refs={"invoice": inv},
    )
    r.step(
        "POST",
        "/v1/invoices/{invoice}/mark_uncollectible",
        {},
        path_refs={"invoice": inv},
    )
    r.step(
        "POST",
        "/v1/invoices/{invoice}/finalize",
        {},
        path_refs={"invoice": inv},
    )
    r.step(
        "DELETE",
        "/v1/invoices/{invoice}",
        path_refs={"invoice": inv},
    )
    # --- add/update/remove lines on a fresh draft ---
    edit = r.step(
        "POST",
        "/v1/invoices",
        {"customer": customer, "pending_invoice_items_behavior": "include"},
        binds_as="edit",
    )
    added = r.step(
        "POST",
        "/v1/invoices/{invoice}/add_lines",
        {"lines": [{"amount": 999, "description": "Extra line"}]},
        path_refs={"invoice": edit},
        binds_as="added",
    )
    r.step(
        "POST",
        "/v1/invoices/{invoice}/lines/{line_item_id}",
        {"amount": 777, "description": "Extra line (revised)"},
        path_refs={"invoice": edit, "line_item_id": ref(added, "lines.data[0].id")},
    )
    r.step(
        "POST",
        "/v1/invoices/{invoice}/update_lines",
        {"lines": [{"id": ref(added, "lines.data[0].id"), "amount": 888}]},
        path_refs={"invoice": edit},
    )
    r.step(
        "POST",
        "/v1/invoices/{invoice}/remove_lines",
        {"lines": [{"id": ref(added, "lines.data[0].id"), "behavior": "delete"}]},
        path_refs={"invoice": edit},
    )
    # --- the explicit finalize -> open -> pay path ---
    r.step(
        "POST",
        "/v1/invoices/{invoice}/finalize",
        {},
        path_refs={"invoice": edit},
    )
    r.step(
        "POST",
        "/v1/invoices/{invoice}/finalize",
        {},
        path_refs={"invoice": edit},
    )
    r.step(
        "POST",
        "/v1/invoices/{invoice}",
        {"description": "no longer editable"},
        path_refs={"invoice": edit},
    )
    r.step(
        "POST",
        "/v1/invoices/{invoice}",
        {"metadata": {"open": "yes"}},
        path_refs={"invoice": edit},
    )
    r.step(
        "POST",
        "/v1/invoices/{invoice}/pay",
        {},
        path_refs={"invoice": edit},
    )
    # --- void and mark_uncollectible from draft and from open ---
    vd = r.step(
        "POST",
        "/v1/invoices",
        {"customer": customer, "pending_invoice_items_behavior": "include"},
        binds_as="vd",
    )
    r.step(
        "POST",
        "/v1/invoices/{invoice}/void",
        {},
        path_refs={"invoice": vd},
    )
    r.step(
        "POST",
        "/v1/invoiceitems",
        {
            "customer": customer,
            "amount": 3000,
            "currency": "usd",
            "description": "Voided flow item",
        },
        binds_as="ii5",
    )
    vo = r.step(
        "POST",
        "/v1/invoices",
        {"customer": customer, "pending_invoice_items_behavior": "include"},
        binds_as="vo",
    )
    r.step(
        "POST",
        "/v1/invoices/{invoice}/finalize",
        {},
        path_refs={"invoice": vo},
    )
    r.step(
        "POST",
        "/v1/invoices/{invoice}/void",
        {},
        path_refs={"invoice": vo},
        binds_as="voided",
    )
    r.step(
        "POST",
        "/v1/invoices/{invoice}/void",
        {},
        path_refs={"invoice": vo},
    )
    ud = r.step(
        "POST",
        "/v1/invoices",
        {"customer": customer, "pending_invoice_items_behavior": "include"},
        binds_as="ud",
    )
    r.step(
        "POST",
        "/v1/invoices/{invoice}/mark_uncollectible",
        {},
        path_refs={"invoice": ud},
    )
    r.step(
        "POST",
        "/v1/invoiceitems",
        {
            "customer": customer,
            "amount": 1200,
            "currency": "usd",
            "description": "Uncollectible flow item",
        },
        binds_as="ii6",
    )
    uo = r.step(
        "POST",
        "/v1/invoices",
        {"customer": customer, "pending_invoice_items_behavior": "include"},
        binds_as="uo",
    )
    r.step(
        "POST",
        "/v1/invoices/{invoice}/finalize",
        {},
        path_refs={"invoice": uo},
    )
    r.step(
        "POST",
        "/v1/invoices/{invoice}/mark_uncollectible",
        {},
        path_refs={"invoice": uo},
        binds_as="uncollectible",
    )
    r.step(
        "POST",
        "/v1/invoices/{invoice}/mark_uncollectible",
        {},
        path_refs={"invoice": uo},
    )
    # --- send_invoice: the due-date requirement, /send, out-of-band pay ---
    r.step(
        "POST",
        "/v1/invoices",
        {
            "customer": customer,
            "collection_method": "send_invoice",
            "pending_invoice_items_behavior": "include",
        },
    )
    sd = r.step(
        "POST",
        "/v1/invoices",
        {
            "customer": customer,
            "collection_method": "send_invoice",
            "days_until_due": 7,
            "pending_invoice_items_behavior": "include",
        },
        binds_as="sd",
    )
    r.step(
        "POST",
        "/v1/invoices/{invoice}/send",
        {},
        path_refs={"invoice": sd},
    )
    r.step(
        "POST",
        "/v1/invoiceitems",
        {"customer": customer, "amount": 4400, "currency": "usd", "description": "Sent flow item"},
        binds_as="ii7",
    )
    sent = r.step(
        "POST",
        "/v1/invoices",
        {
            "customer": customer,
            "collection_method": "send_invoice",
            "days_until_due": 7,
            "auto_advance": True,
            "pending_invoice_items_behavior": "include",
        },
        binds_as="sent",
    )
    r.step(
        "POST",
        "/v1/invoices/{invoice}/finalize",
        {},
        path_refs={"invoice": sent},
        binds_as="sentopen",
    )
    r.step(
        "POST",
        "/v1/invoices/{invoice}/send",
        {},
        path_refs={"invoice": sent},
    )
    r.step(
        "POST",
        "/v1/invoices/{invoice}/pay",
        {"paid_out_of_band": True},
        path_refs={"invoice": sent},
        binds_as="oob",
    )
    # --- invoiceitem delete: pending deletes, swept refuses ---
    ii4 = r.step(
        "POST",
        "/v1/invoiceitems",
        {"customer": customer, "amount": 400, "currency": "usd", "description": "Never billed"},
        binds_as="ii4",
    )
    r.step(
        "DELETE",
        "/v1/invoiceitems/{invoiceitem}",
        path_refs={"invoiceitem": ii4},
    )
    r.step(
        "GET",
        "/v1/invoiceitems/{invoiceitem}",
        path_refs={"invoiceitem": ii4},
    )
    r.step(
        "DELETE",
        "/v1/invoiceitems/{invoiceitem}",
        path_refs={"invoiceitem": ii3},
    )
    r.step(
        "POST",
        "/v1/invoiceitems/{invoiceitem}",
        {"description": "gone"},
        path_refs={"invoiceitem": ii3},
    )
    # --- the missing-id 404s and the parameter spellings ---
    r.step("GET", "/v1/invoices/in_nope")
    r.step("POST", "/v1/invoices/in_nope", {"metadata": {"k": "v"}})
    r.step("DELETE", "/v1/invoices/in_nope")
    r.step("POST", "/v1/invoices/in_nope/pay", {})
    r.step("POST", "/v1/invoices/in_nope/finalize", {})
    r.step("POST", "/v1/invoices/in_nope/void", {})
    r.step("POST", "/v1/invoices/in_nope/send", {})
    r.step("POST", "/v1/invoices/in_nope/mark_uncollectible", {})
    r.step("POST", "/v1/invoices/in_nope/add_lines", {"lines": [{"amount": 1}]})
    r.step(
        "POST",
        "/v1/invoices",
        {"customer": customer, "pending_invoice_items_behavior": "bogus", "auto_advance": True},
    )
    r.step(
        "POST",
        "/v1/invoices",
        {"customer": customer, "days_until_due": 5},
    )
    r.step(
        "POST",
        "/v1/invoices",
        {"customer": customer, "nope": 1},
    )
    r.step(
        "POST",
        "/v1/invoices",
        {"customer": "cus_nope"},
    )


CLEANUP = {"customer": "/v1/customers"}
