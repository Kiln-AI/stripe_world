"""The disputes half of Phase 9: the lifecycle the magic dispute cards drive,
the evidence submission with its three magic strings, `/close`, and the
charge-scoped reads and aliases.

Every wire shape and refusal here is pinned by live probe at
`2026-08-26.dahlia` (Phase 9, 2026-09-20): disputes are born *inside* the
charge attempt (`charge.dispute.created` + `charge.dispute.funds_withdrawn`
fire between `charge.succeeded` and `payment_intent.succeeded`), the
chargeback card lands `needs_response` with `is_charge_refundable: false`
while the inquiry card lands `warning_needs_response` refundable, evidence
submission moves the dispute to `under_review` (inquiries:
`warning_under_review`), and `/close` answers `lost` synchronously.

**The one headline declared difference**: live test mode settles the magic
strings asynchronously — `winning_evidence` answers `under_review` and flips
to `won` seconds later, `losing_evidence` likewise to `lost`. A frozen clock
cannot wait out issuer review, and `won`/`lost` must be reachable (the
refund gate, `is_charge_refundable` and the dispute eval all need them), so
the settle is collapsed into the submitting call. The submit response's
`status` / `is_charge_refundable` therefore differ from the recording by
exactly one hop — two scenario-scoped allow-list entries carry it.

`balance_transactions` is derived from the ledger (data_model §4's ruling):
the chargeback withdrawal row at creation, the reversal added on a win — both
recorded in cassette 05's settled dispute bodies, which also correct the
functional spec's fee reading: the 1500 received fee is kept on a win and no
separate countered-fee row exists. Inquiries write nothing until escalated
(recorded: the warning-track bodies carry no rows); the escalation pulls the
funds then. Stripe's automatic evidence enrichment from the customer record
is not modeled — submitted evidence is stored verbatim.
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import seahaven

from stripeapi import _ids, _json, _seq, _time
from stripeapi.billing import ledger
from stripeapi.dispatch.params import Param, ParamSpec
from stripeapi.dispatch.resource import ListFilter, ResourceSpec, register
from stripeapi.resources import _lookup, balance_transactions, charges, events
from stripeapi.serialize.fields import FieldMap, presence_sets, serializer_for
from stripeapi.spec import spec_document
from stripeapi.stripe_errors import invalid_request

if TYPE_CHECKING:
    from stripeapi.dispatch.response import Request

__all__ = [
    "EVIDENCE_TEXT_FIELDS",
    "FIELDS",
    "SPEC",
    "charge_dispute",
    "charge_dispute_close",
    "charge_dispute_update",
    "close",
    "maybe_create_dispute",
    "update",
]

CH = ("ch_",)
PI = ("pi_",)

# --- the wire facts per dispute flavor (probed, Phase 9) -----------------------------

#: reason -> the `payment_method_details.card` copy a fresh dispute carries.
#: `10.4` is Visa's card-absent-fraud code, `13.1` merchandise-not-received;
#: the inquiry track answers the bare network code `10`.
_CARD_DETAILS: dict[tuple[str, str], dict[str, object]] = {
    ("fraudulent", "chargeback"): {
        "brand": "visa",
        "case_type": "chargeback",
        "network": "visa",
        "network_reason_code": "10.4",
    },
    ("product_not_received", "chargeback"): {
        "brand": "visa",
        "case_type": "chargeback",
        "network": "visa",
        "network_reason_code": "13.1",
    },
    ("fraudulent", "inquiry"): {
        "brand": "visa",
        "case_type": "inquiry",
        "network": "visa",
        "network_reason_code": "10",
    },
}

_DEFAULT_CARD_DETAILS: dict[str, object] = {
    "brand": "visa",
    "case_type": "chargeback",
    "network": "visa",
    "network_reason_code": "10.4",
}

#: The evidence object's text fields, read from the committed spec so the
#: ParamSpec and `dispute_evidence` cannot drift. The nine file-holding
#: fields (`*_documentation`, `customer_communication`, …) and
#: `enhanced_evidence` are cut: this world has no file storage, and a string
#: sent where Stripe wants a file id is the recorded 400 — answering
#: `parameter_unknown` is the declared scope cut.
EVIDENCE_TEXT_FIELDS: tuple[str, ...] = tuple(
    sorted(
        name
        for name, prop in spec_document()["components"]["schemas"]["dispute_evidence"][
            "properties"
        ].items()
        if prop.get("type") == "string"
    )
)

_EVIDENCE_SHAPE = Param(
    name="evidence",
    kind="object",
    shape=tuple(Param(name=name, kind="string", max_length=5_000) for name in EVIDENCE_TEXT_FIELDS),
)

#: `uncategorized_text`'s magic strings (magic-card-table §4, quoted verbatim
#: from docs.stripe.com/testing) and what each settles to.
_MAGIC_STRINGS = {
    "winning_evidence": "won",
    "losing_evidence": "lost",
    "escalate_inquiry_evidence": "needs_response",
}

# --- the ParamSpecs -----------------------------------------------------------------

DISPUTE_LIST = ParamSpec(
    op_id="GetDisputes",
    paginated=True,
)

DISPUTE_RETRIEVE = ParamSpec(
    op_id="GetDisputesDispute",
    path=("dispute",),
)

DISPUTE_UPDATE = ParamSpec(
    op_id="PostDisputesDispute",
    path=("dispute",),
    body=(_EVIDENCE_SHAPE, Param(name="submit", kind="boolean")),
    metadata=True,
)

DISPUTE_CLOSE = ParamSpec(
    op_id="PostDisputesDisputeClose",
    path=("dispute",),
)

CHARGE_DISPUTE_RETRIEVE = ParamSpec(
    op_id="GetChargesChargeDispute",
    path=("charge",),
)

CHARGE_DISPUTE_UPDATE = ParamSpec(
    op_id="PostChargesChargeDispute",
    path=("charge",),
    body=(_EVIDENCE_SHAPE, Param(name="submit", kind="boolean")),
    metadata=True,
)

CHARGE_DISPUTE_CLOSE = ParamSpec(
    op_id="PostChargesChargeDisputeClose",
    path=("charge",),
)

# --- the serializer -----------------------------------------------------------------

always_present, omit_when_none = presence_sets("dispute")

FIELDS = FieldMap(
    object="dispute",
    table="disputes",
    columns={
        "id": "id",
        "created": "created",
        "amount": "amount",
        "charge": "charge",
        "currency": "currency",
        "enhanced_eligibility_types": "enhanced_eligibility_types",
        "evidence": "evidence",
        "evidence_details": "evidence_details",
        "is_charge_refundable": "is_charge_refundable",
        "metadata": "metadata",
        "payment_intent": "payment_intent",
        "payment_method_details": "payment_method_details",
        "reason": "reason",
        "status": "status",
    },
    timestamps=frozenset({"created"}),
    json_columns=frozenset(
        {
            "enhanced_eligibility_types",
            "evidence",
            "evidence_details",
            "metadata",
            "payment_method_details",
        }
    ),
    booleans=frozenset({"is_charge_refundable"}),
    constants={"livemode": False},
    derived={
        # The ledger's rows for this dispute, full objects always (the field
        # is `array<ref:balance_transaction>`, never ids): the withdrawal at
        # creation or escalation, the reversal on a win (data_model §4).
        "balance_transactions": lambda ctx, row: [
            balance_transactions.serialize(ctx, bt)
            for bt in ctx.db.rows(
                "SELECT * FROM balance_transactions WHERE source = ? ORDER BY x_seq",
                row["id"],
            )
        ],
    },
    always_present=always_present,
    omit_when_none=omit_when_none,
)

_serialize = serializer_for(FIELDS)


def serialize(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
    return _serialize(ctx, row)


#: The evidence object's full 28-key null-padded shape, built once from the
#: committed spec — `enhanced_evidence: {}` included, exactly as the recorded
#: fresh body carries it (Stripe's customer-record enrichment of
#: `customer_name` / `customer_email_address` is not modeled; submitted
#: fields are stored verbatim).
def _blank_evidence() -> dict[str, Any]:
    blank: dict[str, Any] = {}
    for name, prop in spec_document()["components"]["schemas"]["dispute_evidence"][
        "properties"
    ].items():
        blank[name] = {} if "$ref" in prop else None
    return blank


def _evidence_details(row: Mapping[str, Any]) -> dict[str, Any]:
    loaded = _json.loads(row["evidence_details"])
    return loaded if isinstance(loaded, dict) else {}


def compute_due_by(created_iso: str) -> int:
    """`due_by` = the last second of the UTC day eight days out — the shape
    of the single recorded observation (created Sunday 2026-09-20T15:19:15Z,
    `due_by` 2026-09-28T23:59:59Z, probed Phase 9). Stripe's true rule blends
    network deadlines and business days; one day-of-week cannot pin it, so
    the deterministic reading is declared rather than presented as Stripe's."""
    created_unix = _time.to_unix(created_iso)
    day = created_unix // 86_400
    return (day + 9) * 86_400 - 1


# --- creation (driven by the charge attempt) ----------------------------------------


def _withdrawal_fee_details(currency: str, fee: int) -> list[dict[str, Any]]:
    """The recorded `Dispute fee` detail line (cassette 05): a flat stripe_fee
    entry with no application."""
    return [
        {
            "amount": fee,
            "application": None,
            "currency": currency,
            "description": "Dispute fee",
            "type": "stripe_fee",
        }
    ]


def record_withdrawal(ctx: seahaven.Ctx, dispute_row: Mapping[str, Any]) -> None:
    """The dispute's ledger withdrawal — `type: adjustment`,
    `reporting_category: dispute`, the negative amount, the flat received fee
    (all recorded, Phase 11). Idempotence is structural: called exactly once
    per dispute, at creation for the chargeback track and at escalation for
    an inquiry."""
    fee = ledger.ledger_spec(ctx).dispute_received_fee
    ledger.record(
        ctx,
        type_="adjustment",
        amount=-dispute_row["amount"],
        fee=fee,
        currency=dispute_row["currency"],
        source_id=dispute_row["id"],
        description=f"Chargeback withdrawal for {dispute_row['charge']}",
        fee_details=_withdrawal_fee_details(dispute_row["currency"], fee),
        reporting_category="dispute",
    )


def record_reversal(ctx: seahaven.Ctx, dispute_row: Mapping[str, Any]) -> None:
    """The win's reversal row — the funds back, fee-free,
    `reporting_category: dispute_reversal` (recorded, cassette 05's settled
    dispute: the 1500 received fee is kept, which corrects the functional
    spec's refunded-countered-fee reading)."""
    ledger.record(
        ctx,
        type_="adjustment",
        amount=dispute_row["amount"],
        fee=0,
        currency=dispute_row["currency"],
        source_id=dispute_row["id"],
        description=f"Chargeback reversal for {dispute_row['charge']}",
        fee_details=[],
        reporting_category="dispute_reversal",
    )


def maybe_create_dispute(
    ctx: seahaven.Ctx, charge_row: Mapping[str, Any], pm_row: Mapping[str, Any]
) -> None:
    """Write the dispute a dispute-tagged payment method opens, flip the
    charge's `disputed` flag, and emit the recorded pair — called from the
    confirm path *after* `charge.succeeded` and *before* the intent's own
    event, the recorded order (Phase 9 cassette 05)."""
    tags: dict[str, str] = {}
    for token in (pm_row["x_behavior"] or "").split(","):
        if "=" in token:
            key, value = token.split("=", 1)
            tags[key] = value
    reason = tags.get("dispute")
    if reason is None:
        return
    track = tags.get("dispute_track", "chargeback")
    id_ = _ids.stripe_id(ctx, "du_")
    evidence = _blank_evidence()
    evidence_details = {
        "due_by": compute_due_by(ctx.clock.iso()),
        "enhanced_eligibility": {},
        "has_evidence": False,
        "past_due": False,
        "submission_count": 0,
    }
    ctx.db.execute(
        "INSERT INTO disputes (id, x_seq, created, amount, charge, currency,"
        " enhanced_eligibility_types, evidence, evidence_details, is_charge_refundable,"
        " metadata, payment_intent, payment_method_details, reason, status)"
        " VALUES (?, ?, ?, ?, ?, ?, '[]', ?, ?, ?, '{}', ?, ?, ?, ?)",
        id_,
        _seq.next_seq(ctx, "disputes"),
        ctx.clock.iso(),
        # The captured amount — equal to the charge amount on the automatic
        # path, and the partial-capture ceiling on the manual path (probed:
        # the dispute is born at capture with the captured funds; the
        # partial case itself is the captured-funds rule, unrecorded).
        charge_row["amount_captured"],
        charge_row["id"],
        charge_row["currency"],
        _json.dumps(evidence),
        _json.dumps(evidence_details),
        int(track == "inquiry"),
        charge_row["payment_intent"],
        _json.dumps(
            {
                "card": _CARD_DETAILS.get((reason, track), _DEFAULT_CARD_DETAILS),
                "type": "card",
            }
        ),
        reason,
        "warning_needs_response" if track == "inquiry" else "needs_response",
    )
    ctx.db.execute("UPDATE charges SET disputed = 1 WHERE id = ?", charge_row["id"])
    if track != "inquiry":
        # The chargeback pulls its funds immediately; an inquiry withdraws
        # nothing (that is what keeps it refundable, recorded) — the
        # escalation writes the withdrawal instead.
        record_withdrawal(
            ctx, _lookup.require_row(ctx, "disputes", "dispute", id_, param="dispute")
        )
    body = serialize(ctx, _lookup.require_row(ctx, "disputes", "dispute", id_, param="dispute"))
    events.emit_event(ctx, type="charge.dispute.created", obj=body)
    events.emit_event(ctx, type="charge.dispute.funds_withdrawn", obj=body)


# --- the handlers --------------------------------------------------------------------


def _require_dispute(ctx: seahaven.Ctx, id_: str) -> dict[str, Any]:
    return _lookup.require_row(ctx, "disputes", "dispute", id_, param="dispute")


def _refuse_closed(row: Mapping[str, Any]) -> None:
    if row["status"] in ("won", "lost"):
        raise invalid_request("This dispute is already closed")


def _emit_update_pair(ctx: seahaven.Ctx, dispute_body: dict[str, Any], charge_id: str) -> None:
    """The recorded pair every dispute mutation closes with: the dispute's
    own event, then the charge's (Phase 9 cassette 05)."""
    events.emit_event(ctx, type="charge.dispute.updated", obj=dispute_body)
    events.emit_event(
        ctx,
        type="charge.updated",
        obj=charges.serialize(
            ctx, _lookup.require_row(ctx, "charges", "charge", charge_id, param="charge")
        ),
    )


def _apply_update(ctx: seahaven.Ctx, req: Request, row: Mapping[str, Any]) -> dict[str, Any]:
    """The evidence/metadata update both the canonical and charge-scoped
    paths share (recorded, Phase 9)."""
    _refuse_closed(row)
    evidence = _json.loads(row["evidence"])
    if not isinstance(evidence, dict):
        raise seahaven.WorldBug(f"dispute {row['id']}: stored evidence is not an object")
    submitted = req.params.get("evidence")
    # `submit` is a declared parameter whose VALUE is deliberately unread:
    # every recording (magic strings and plain evidence alike) moved the
    # dispute under review on the evidence alone — the parameter's absent-
    # means-false default never changed anything — and inventing a
    # `submit: false` branch would be speculating past the recordings.
    if isinstance(submitted, dict):
        evidence.update(submitted)
    details = _evidence_details(row)
    details["submission_count"] = int(details.get("submission_count", 0)) + (
        1 if submitted is not None else 0
    )
    details["has_evidence"] = any(
        value is not None for key, value in evidence.items() if key != "enhanced_evidence"
    )
    magic = None
    if isinstance(submitted, dict):
        text = submitted.get("uncategorized_text")
        if isinstance(text, str) and text in _MAGIC_STRINGS:
            magic = _MAGIC_STRINGS[text]
    status = row["status"]
    refundable = row["is_charge_refundable"]
    if magic == "won":
        # The synchronous settle (module docstring): the funds_reinstated /
        # closed pair fires with the win, is_charge_refundable opens, and
        # the refund gate with it.
        status = "won"
        refundable = 1
    elif magic == "lost":
        status = "lost"
        refundable = 0
    elif magic == "needs_response":
        # An escalated inquiry is a chargeback now: the funds it did not
        # pull as an inquiry are pulled by the escalation.
        status = "needs_response"
        refundable = 0
    elif submitted is not None:
        # Plain evidence: submitting moves the dispute under review (probed
        # for both tracks) and it stays there — no settle, recorded.
        status = "warning_under_review" if row["status"].startswith("warning") else "under_review"
    metadata_text = row["metadata"]
    if req.metadata is not None:
        metadata_text = _json.dumps(dict(req.metadata.apply(_json.loads(metadata_text) or {})))
    ctx.db.execute(
        "UPDATE disputes SET evidence = ?, evidence_details = ?, status = ?,"
        " is_charge_refundable = ?, metadata = ? WHERE id = ?",
        _json.dumps(evidence),
        _json.dumps(details),
        status,
        refundable,
        metadata_text,
        row["id"],
    )
    # The escalated inquiry pulls the funds the inquiry never did (the
    # Phase 9 ruling); the withdrawal happens exactly once — a
    # chargeback-track dispute already wrote it at creation.
    already_withdrew = ctx.db.one("SELECT id FROM balance_transactions WHERE source = ?", row["id"])
    if magic == "needs_response" and not already_withdrew:
        record_withdrawal(ctx, row)
    if magic == "won":
        record_reversal(ctx, row)
    body = serialize(ctx, _require_dispute(ctx, row["id"]))
    _emit_update_pair(ctx, body, row["charge"])
    if magic == "won":
        events.emit_event(ctx, type="charge.dispute.funds_reinstated", obj=body)
        events.emit_event(ctx, type="charge.dispute.closed", obj=body)
    elif magic == "lost":
        events.emit_event(ctx, type="charge.dispute.closed", obj=body)
    return body


def update(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/disputes/{dispute}`."""
    row = _require_dispute(ctx, req.path_params["dispute"])
    return _apply_update(ctx, req, row)


def charge_dispute_update(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/charges/{charge}/dispute` — the alias of the update."""
    row = _charge_dispute_row(ctx, req.path_params["charge"])
    return _apply_update(ctx, req, row)


def _charge_dispute_row(ctx: seahaven.Ctx, charge_id: str) -> dict[str, Any]:
    """The dispute a charge-scoped path names, with the charge looked up
    first (recorded 404s: `No such charge: 'ch_…'` on a bad parent;
    `No dispute for charge: ch_…` on a clean one)."""
    _lookup.require_row(ctx, "charges", "charge", charge_id, param="charge")
    row = ctx.db.one("SELECT * FROM disputes WHERE charge = ?", charge_id)
    if row is None:
        raise invalid_request(f"No dispute for charge: {charge_id}", status=404)
    return row


def charge_dispute(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`GET /v1/charges/{charge}/dispute`."""
    return serialize(ctx, _charge_dispute_row(ctx, req.path_params["charge"]))


def _apply_close(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
    """`/close`: `lost`, synchronously (recorded), with the terminal pair."""
    _refuse_closed(row)
    ctx.db.execute(
        "UPDATE disputes SET status = 'lost', is_charge_refundable = 0 WHERE id = ?", row["id"]
    )
    body = serialize(ctx, _require_dispute(ctx, row["id"]))
    events.emit_event(ctx, type="charge.dispute.closed", obj=body)
    events.emit_event(
        ctx,
        type="charge.updated",
        obj=charges.serialize(
            ctx, _lookup.require_row(ctx, "charges", "charge", row["charge"], param="charge")
        ),
    )
    return body


def close(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/disputes/{dispute}/close`."""
    row = _require_dispute(ctx, req.path_params["dispute"])
    return _apply_close(ctx, row)


def charge_dispute_close(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/charges/{charge}/dispute/close` — the alias of the close."""
    row = _charge_dispute_row(ctx, req.path_params["charge"])
    return _apply_close(ctx, row)


# --- the engine-served rest ----------------------------------------------------------

SPEC = register(
    ResourceSpec(
        object="dispute",
        table="disputes",
        id_prefix="du_",
        collection_url="/v1/disputes",
        serializer=serialize,
        columns=tuple(FIELDS.columns),
        list_filters=(
            ListFilter(
                name="charge",
                column="charge",
                kind="exact",
                id_prefixes=CH,
                references="charge",
            ),
            ListFilter(
                name="payment_intent",
                column="payment_intent",
                kind="exact",
                id_prefixes=PI,
                references="payment_intent",
            ),
            ListFilter(name="created", column="created", kind="range"),
        ),
        delete=None,
        metadata=False,
    )
)
