"""Scenario 11 (functional spec §12, the ledger-and-payouts slice): the
balance ledger every money movement leaves behind, and the payout surface.

Everything charges in **cad** — the recording account's own settlement
currency — so the recorded bt rows carry no FX conversion and the replay
diffs only what is genuinely account config (pricing, the settlement
schedule) rather than a currency transform. The payout success path is
deliberately absent: the recording account has no external account in any
currency and the key cannot add one (probed — `allowed_differences.py`'s
structural section carries the constraint), so the cassette pins the
account-independent refusals, the missing-id spellings and the empty lists,
and the unit suites carry the lifecycle.
"""

from tools_dev.scenarios._dsl import ref

SCENARIO = "11_ledger_payouts"
DESCRIPTION = (
    "The balance ledger: charge, partial-capture and refund rows in the "
    "account's own currency, the dispute withdrawal and its win-reversal, "
    "the inquiry that writes nothing, bt reads with expand[]=source, the "
    "source-filter dispute quirk, the computed balance, and the payout "
    "surface's recorded refusals."
)

_EMAIL = "s11@conformance.stripeapi.invalid"


def record(r):
    customer = r.step(
        "POST",
        "/v1/customers",
        {"email": _EMAIL, "description": "scenario 11"},
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
    # A confirmed cad charge and its ledger row.
    pi_ok = r.step(
        "POST",
        "/v1/payment_intents",
        {
            "amount": 4400,
            "currency": "cad",
            "customer": customer,
            "payment_method": visa,
            "confirm": True,
            "payment_method_types": ["card"],
            "description": "scenario 11 charge",
        },
        binds_as="pi_ok",
    )
    r.step(
        "GET",
        "/v1/charges/{charge}",
        path_refs={"charge": ref(pi_ok, "latest_charge")},
        binds_as="charge_ok",
    )
    # A manual hold, uncaptured (no ledger row yet), then a partial capture.
    pi_hold = r.step(
        "POST",
        "/v1/payment_intents",
        {
            "amount": 6500,
            "currency": "cad",
            "customer": customer,
            "payment_method": visa,
            "confirm": True,
            "capture_method": "manual",
            "payment_method_types": ["card"],
        },
        binds_as="pi_hold",
    )
    r.step(
        "GET",
        "/v1/charges/{charge}",
        path_refs={"charge": ref(pi_hold, "latest_charge")},
    )
    r.step(
        "POST",
        "/v1/payment_intents/{intent}/capture",
        {"amount_to_capture": 5000},
        path_refs={"intent": pi_hold},
    )
    r.step(
        "GET",
        "/v1/charges/{charge}",
        path_refs={"charge": ref(pi_hold, "latest_charge")},
    )
    # A partial refund of the first charge and its ledger row.
    refund = r.step(
        "POST",
        "/v1/refunds",
        {"charge": ref(pi_ok, "latest_charge"), "amount": 1400},
        binds_as="refund",
    )
    r.step("GET", "/v1/balance_transactions", {"source": refund})
    # The chargeback dispute: withdrawal at creation, reversal on the win.
    dispute_card = r.step(
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_visa_createDispute"}},
        binds_as="dispute_card",
    )
    pi_dispute = r.step(
        "POST",
        "/v1/payment_intents",
        {
            "amount": 3000,
            "currency": "cad",
            "customer": customer,
            "payment_method": dispute_card,
            "confirm": True,
            "payment_method_types": ["card"],
        },
        binds_as="pi_dispute",
    )
    dispute = r.step(
        "GET",
        "/v1/charges/{charge}/dispute",
        path_refs={"charge": ref(pi_dispute, "latest_charge")},
        binds_as="dispute",
    )
    r.step(
        "POST",
        "/v1/disputes/{dispute}",
        {"evidence": {"uncategorized_text": "winning_evidence"}},
        path_refs={"dispute": dispute},
    )
    # The inquiry dispute: no ledger rows at all, before and after review.
    inquiry_card = r.step(
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_visa_createDisputeInquiry"}},
        binds_as="inquiry_card",
    )
    pi_inquiry = r.step(
        "POST",
        "/v1/payment_intents",
        {
            "amount": 2100,
            "currency": "cad",
            "customer": customer,
            "payment_method": inquiry_card,
            "confirm": True,
            "payment_method_types": ["card"],
        },
        binds_as="pi_inquiry",
    )
    inquiry = r.step(
        "GET",
        "/v1/charges/{charge}/dispute",
        path_refs={"charge": ref(pi_inquiry, "latest_charge")},
        binds_as="inquiry",
    )
    r.step(
        "POST",
        "/v1/disputes/{dispute}",
        {"evidence": {"uncategorized_text": "uncategorized"}},
        path_refs={"dispute": inquiry},
    )
    # The source-filter quirk: a dispute id never matches, though the
    # withdrawal row's own field names it.
    r.step("GET", "/v1/balance_transactions", {"source": ref(dispute, "id")})
    r.step("GET", "/v1/balance_transactions", {"source": ref(inquiry, "id")})
    # The charge's own row, listed late enough that live settlement has
    # written it (the async window the confirm-time read sits inside), and
    # the dispute's withdrawal read through the dispute body, where it has
    # lived since creation. Retrieve and expand both flavors: the
    # polymorphic source inflation.
    charge_bt = r.step(
        "GET",
        "/v1/balance_transactions",
        {"source": ref(pi_ok, "latest_charge")},
        binds_as="charge_bt",
    )
    r.step(
        "GET",
        "/v1/balance_transactions/{id}",
        path_refs={"id": ref(charge_bt, "data[0].id")},
    )
    r.step(
        "GET",
        "/v1/balance_transactions/{id}",
        {"expand": ["source"]},
        path_refs={"id": ref(charge_bt, "data[0].id")},
    )
    r.step(
        "GET",
        "/v1/balance_transactions/{id}",
        {"expand": ["source"]},
        path_refs={"id": ref(dispute, "balance_transactions[0].id")},
    )
    r.step(
        "GET",
        "/v1/balance_transactions",
        {"payout": "po_nope"},
    )
    r.step("GET", "/v1/balance_transactions", {"type": "bogus_type"})
    r.step("GET", "/v1/balance_transactions", {"currency": "usd"})
    # The computed balance: the account's whole ledger, not the scenario's.
    r.step("GET", "/v1/balance")
    # The payout surface: the recorded refusals and the empty reads. A plain
    # create is deliberately absent — the recording account refuses every
    # payout for lack of an external account, where this world's succeeds,
    # so the step could never replay (the constraint is the structural
    # declaration; these refusals all bind before any balance is read).
    r.step("POST", "/v1/payouts", {"amount": -100, "currency": "cad"})
    r.step("POST", "/v1/payouts", {"amount": 0, "currency": "cad"})
    r.step("POST", "/v1/payouts", {"currency": "cad"})
    r.step("POST", "/v1/payouts", {"amount": 1000, "currency": "xxd"})
    r.step("GET", "/v1/payouts")
    r.step("GET", "/v1/payouts", {"status": "paid"})
    r.step("GET", "/v1/payouts/po_nope")
    r.step("POST", "/v1/payouts/po_nope/cancel")
    r.step("POST", "/v1/payouts/po_nope/reverse")
    r.step("GET", "/v1/balance_transactions/txn_nope")


CLEANUP = {"customer": "/v1/customers"}
