"""The balance ledger: every money movement's `balance_transaction` row, the
computed `/v1/balance` read, and the payout lifecycle's ledger effects.

`components/billing_engine.md` §"billing/ledger.py" is this module's design.
Two rules carry it:

- **`net = amount - fee`, always** — Stripe's own formula (spec3.json on
  `balance_transaction.net`), stored as a column and enforced by a CHECK so
  the arithmetic is auditable in SQL.
- **No denormalized balance anywhere.** `/v1/balance` is computed from the
  rows on every read (architecture §6.4): the "ledger sums correctly"
  invariant is a tautology rather than a check of two copies.

Wire facts pinned by recording and probe (Phase 11, 2026-09-20): the charge bt
is `type: charge` / `reporting_category: charge`; the refund bt is
`type: refund` / `reporting_category: refund` with `fee: 0` and the
`REFUND FOR CHARGE (…)` description; both dispute rows are `type: adjustment`
under `dispute` / `dispute_reversal`, the withdrawal carrying the flat 1500
dispute fee which a win does **not** refund (cassette 05's settled dispute —
the recording that corrects the functional spec's two-fee reading); payouts
draw the available balance the moment they are created.

Settlement is deterministic under a frozen clock: `available_on` is midnight
UTC of the creation day plus `settlement_business_days` (a declared world
constant — the recordings ground the day-floored-midnight shape and the fresh
row's `pending` status, at the recording account's own +7-day delay, not the
2), so a fresh row is `pending` and a fixture's backdated row is `available`
— exactly the split `/v1/balance` reports. The dispute withdrawals are the
declared exception: the recorded rows answer `available` with
`available_on == created` (cassettes 05 and 11), where this world holds them
pending under the same T+2 — the `_bt_settlement_status` / `**.available_on`
allow-list entries carry the difference.
"""

from functools import cache
from typing import Any

import seahaven

from seahaven_stripe_world import _ids, _json, _seq, _time
from seahaven_stripe_world.billing._money import FeeSchedule
from seahaven_stripe_world.resources import _lookup, events
from seahaven_stripe_world.serialize.fields import instance_livemode
from seahaven_stripe_world.spec import spec_document
from seahaven_stripe_world.stripe_errors import invalid_request

__all__ = [
    "LedgerSpec",
    "available_cents",
    "available_on",
    "bt_status",
    "cancel_payout",
    "create_payout",
    "fail_payout",
    "ledger_spec",
    "net",
    "read_balance",
    "record",
    "reverse_payout",
    "settle_payout",
]


@cache
def _bt_types() -> frozenset[str]:
    """The 50-value `balance_transaction.type` enum, read from the committed
    spec: the DDL's CHECK and this set are the same fact twice, so the test
    that regexes one out of `sqlite_master` catches a drift."""
    props = spec_document()["components"]["schemas"]["balance_transaction"]["properties"]
    return frozenset(props["type"]["enum"])


#: Alias for `record`'s call-time check: an unknown `type_` is a `WorldBug`
#: for the billing-engine reason — world code would be inventing a type
#: Stripe has not.
def _check_type(type_: str) -> None:
    if type_ not in _bt_types():
        raise seahaven.WorldBug(f"unknown balance_transaction.type {type_!r}")


#: `type` values whose rows leave the balance the moment they are written:
#: a payout's debit is available-hitting immediately (the funds are committed
#: to the bank), while charge/refund/dispute money waits out settlement.
_IMMEDIATE_TYPES = frozenset(("payout", "payout_cancel", "payout_failure"))


class LedgerSpec:
    """The account-level constants the ledger derives from. Plain attributes
    rather than a frozen dataclass: `ctx.state["account"]["ledger"]` is the
    instance-config override seam (the same shape dunning's `policy(ctx)`
    reads), and a dict-to-attrs read is all the machinery it needs."""

    def __init__(
        self,
        *,
        fees: FeeSchedule | None = None,
        settlement_business_days: int = 2,
        dispute_received_fee: int = 1500,
    ) -> None:
        self.fees = fees if fees is not None else FeeSchedule()
        self.settlement_business_days = settlement_business_days
        self.dispute_received_fee = dispute_received_fee


def ledger_spec(ctx: seahaven.Ctx) -> LedgerSpec:
    """The instance's ledger config: `ctx.state["account"]["ledger"]` keys
    over the defaults, so a harness can reprice an instance without a schema
    change."""
    account = ctx.state.get("account")
    config = account.get("ledger") if isinstance(account, dict) else None
    if not isinstance(config, dict):
        return LedgerSpec()
    fees = config.get("fees")
    return LedgerSpec(
        fees=FeeSchedule(**fees) if isinstance(fees, dict) else None,
        settlement_business_days=int(config.get("settlement_business_days", 2)),
        dispute_received_fee=int(config.get("dispute_received_fee", 1500)),
    )


# --- pure helpers ---------------------------------------------------------------------


def net(amount: int, fee: int) -> int:
    """`amount - fee`, the spec's own formula (data_model's CHECK enforces it
    at rest; this is the write-side spelling)."""
    return amount - fee


def available_on(created_iso: str, type_: str, spec: LedgerSpec) -> str:
    """The settlement instant: midnight UTC of the creation day plus the
    settlement window — except payout rows, which are immediate. What the
    cassettes ground is the day-floored-midnight shape and the fresh row's
    `pending` status (cassette 11's charge and refund rows carry it at the
    recording account's own +7-day delay, not our 2); the recorded dispute
    withdrawals answer `available` with `available_on == created`, which this
    world's T+2-pending ruling diverges from by declaration
    (`_bt_settlement_status` / `**.available_on`). Live per-country variation
    is an account property this world's declared constant stands in for."""
    created = _time.to_unix(created_iso)
    if type_ in _IMMEDIATE_TYPES:
        return created_iso
    day = (created // 86_400) * 86_400
    return _time.from_unix(day + spec.settlement_business_days * 86_400)


def bt_status(available_on_iso: str, now_iso: str) -> str:
    """`available` once the settlement instant has passed, `pending` before."""
    return "available" if _time.to_unix(available_on_iso) <= _time.to_unix(now_iso) else "pending"


# --- the one writer ------------------------------------------------------------------


def record(
    ctx: seahaven.Ctx,
    *,
    type_: str,
    amount: int,
    fee: int,
    currency: str,
    source_id: str | None,
    description: str | None,
    available_on_iso: str | None = None,
    fee_details: list[dict[str, Any]] | None = None,
    reporting_category: str | None = None,
) -> dict[str, Any]:
    """Mint a `txn_` id and write ONE balance_transactions row; returns it
    re-read. `type_` is checked against the 50-value set; anything else is a
    `WorldBug` (billing_engine's rule: never invent a type Stripe has not)."""
    _check_type(type_)
    now_iso = ctx.clock.iso()
    settles = (
        available_on_iso
        if available_on_iso is not None
        else available_on(now_iso, type_, ledger_spec(ctx))
    )
    id_ = _ids.stripe_id(ctx, "txn_", timestamp=now_iso, version_digit="3")
    ctx.db.execute(
        "INSERT INTO balance_transactions (id, x_seq, created, amount, available_on,"
        " balance_type, currency, description, fee, fee_details, net, reporting_category,"
        " source, status, type) VALUES (?, ?, ?, ?, ?, 'payments', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        id_,
        _seq.next_seq(ctx, "balance_transactions"),
        now_iso,
        amount,
        settles,
        currency,
        description,
        fee,
        _json.dumps(fee_details if fee_details is not None else []),
        net(amount, fee),
        reporting_category if reporting_category is not None else type_,
        source_id,
        bt_status(settles, now_iso),
        type_,
    )
    return _lookup.require_row(ctx, "balance_transactions", "balance transaction", id_, param="id")


# --- the computed balance ------------------------------------------------------------


def read_balance(ctx: seahaven.Ctx) -> dict[str, Any]:
    """`GET /v1/balance`: the sums over the ledger, split on `available_on`
    against the instance clock. `source_types` is `{card: amount}` for every
    entry — all money this world moves arrives by card — and the optional
    buckets (`instant_available`, `connect_reserved`, `issuing`, the
    prefunding block) are omitted while empty, which the spec's required set
    allows and the recorded body's zeros never exercise on this side."""
    now_iso = ctx.clock.iso()
    out: dict[str, Any] = {"object": "balance", "livemode": instance_livemode(ctx)}
    for field, comparator in (("available", "<="), ("pending", ">")):
        rows = ctx.db.rows(
            "SELECT currency, SUM(net) AS total FROM balance_transactions"
            f" WHERE balance_type = 'payments' AND available_on {comparator} ?"
            " GROUP BY currency ORDER BY currency",
            now_iso,
        )
        out[field] = [
            {
                "amount": int(row["total"]),
                "currency": row["currency"],
                "source_types": {"card": int(row["total"])},
            }
            for row in rows
        ]
    return out


def available_cents(ctx: seahaven.Ctx, currency: str) -> int:
    """The payoutable balance in one currency: the sum of settled net, over
    the payments balance only — invariant I6's own SQL (a prefunding row, the
    day one exists, must not become payoutable)."""
    row = ctx.db.one(
        "SELECT SUM(net) AS total FROM balance_transactions"
        " WHERE currency = ? AND balance_type = 'payments' AND available_on <= ?",
        currency,
        ctx.clock.iso(),
    )
    # An aggregate always answers one row; `or 0` covers the empty-table NULL.
    return int(row["total"] or 0) if row is not None else 0


# --- the payout lifecycle ------------------------------------------------------------


#: Mirrors refunds._CURRENCY_SYMBOLS (importing it would be a cycle —
#: refunds imports this module); `test_the_currency_symbol_tables_are_in_sync`
#: holds the two together.
_CURRENCY_SYMBOLS = {
    "usd": "$",
    "eur": "€",
    "gbp": "£",
    "cad": "$",
    "aud": "$",
    "nzd": "$",
    "hkd": "$",
    "sgd": "$",
}


def _money(currency: str, amount: int) -> str:
    """`$40.00` — the recorded message shape (refunds' over-refund refusal,
    Phase 9, and the payout's `balance_insufficient` refusal beside it)."""
    symbol = _CURRENCY_SYMBOLS.get(currency.lower(), currency.upper())
    return f"{symbol}{amount // 100}.{amount % 100:02d}"


def _payout_bt(
    ctx: seahaven.Ctx, payout: dict[str, Any], *, type_: str, bt_amount: int
) -> dict[str, Any]:
    """The ledger row a payout transition writes: fee-free, available
    immediately, sourced by the payout. `bt_amount` is signed by the caller —
    a create debits (`-amount`), a cancel/failure returns the funds
    (`+amount`), a reversal pulls them back in (the reversing payout's own
    negated amount)."""
    return record(
        ctx,
        type_=type_,
        amount=bt_amount,
        fee=0,
        currency=payout["currency"],
        source_id=payout["id"],
        description=None,
        reporting_category="payout",
    )


def _sweep(ctx: seahaven.Ctx, payout_id: str, currency: str, *, clear: bool) -> None:
    """(Un)mark the currency's settled rows as paid out by this payout —
    `x_payout` answers `GET /v1/balance_transactions?payout=…` (data_model
    rule 9). A payout's own debit is never swept (it is the withdrawal, not
    one of the funds); a cancel/failure reversal row CAN be — the returned
    funds are available again. Clearing returns the rows to the pool: they
    were never paid out."""
    if clear:
        ctx.db.execute(
            "UPDATE balance_transactions SET x_payout = NULL WHERE x_payout = ?", payout_id
        )
    else:
        ctx.db.execute(
            "UPDATE balance_transactions SET x_payout = ? WHERE currency = ?"
            " AND balance_type = 'payments' AND available_on <= ?"
            " AND x_payout IS NULL AND type != 'payout'",
            payout_id,
            currency,
            ctx.clock.iso(),
        )


def create_payout(
    ctx: seahaven.Ctx,
    *,
    amount: int,
    currency: str,
    method: str = "standard",
    destination: str | None,
    description: str | None,
    statement_descriptor: str | None,
    metadata_text: str = "{}",
) -> dict[str, Any]:
    """Write the payout row (born `pending` — a frozen clock never advances it
    to `in_transit` or `paid`; `settle_payout` is the explicit transition),
    its debit bt, and the sweep; emits `payout.created`. Refuses with
    `balance_insufficient` when the amount exceeds the settled balance — the
    message is the documented shape, unpinned by cassette (the recording
    account cannot reach it; see the phase's structural declaration)."""
    if amount > available_cents(ctx, currency):
        raise invalid_request(
            f"Payout amount ({_money(currency, amount)}) is greater than your available "
            f"balance ({_money(currency, max(0, available_cents(ctx, currency)))}).",
            code="balance_insufficient",
        )
    now_iso = ctx.clock.iso()
    # arrival_date: the deterministic default when the caller sent none — the
    # settlement window, the same constant `available_on` draws on. Declared
    # ruling (live arrival dates follow the destination's banking schedule,
    # which no frozen clock and no stub destination can model).
    spec = ledger_spec(ctx)
    arrival = _time.from_unix(
        (_time.to_unix(now_iso) // 86_400) * 86_400 + spec.settlement_business_days * 86_400
    )
    id_ = _ids.stripe_id(ctx, "po_", timestamp=now_iso)
    ctx.db.execute(
        "INSERT INTO payouts (id, x_seq, created, amount, arrival_date, automatic,"
        " currency, description, destination, metadata, method, reconciliation_status,"
        " source_type, statement_descriptor, status, type)"
        " VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, 'completed', 'bank_account', ?, 'pending',"
        " 'bank_account')",
        id_,
        _seq.next_seq(ctx, "payouts"),
        now_iso,
        amount,
        arrival,
        currency,
        description,
        destination if destination is not None else _ids.stripe_id(ctx, "ba_", timestamp=now_iso),
        metadata_text,
        method,
        statement_descriptor,
    )
    row = _lookup.require_row(ctx, "payouts", "payout", id_, param="payout")
    bt = _payout_bt(ctx, row, type_="payout", bt_amount=-row["amount"])
    ctx.db.execute("UPDATE payouts SET balance_transaction = ? WHERE id = ?", bt["id"], id_)
    _sweep(ctx, id_, currency, clear=False)
    row = _lookup.require_row(ctx, "payouts", "payout", id_, param="payout")
    events.emit_event(ctx, type="payout.created", obj=_serialize_payout(ctx, row))
    return row


def _serialize_payout(ctx: seahaven.Ctx, row: dict[str, Any]) -> dict[str, Any]:
    from seahaven_stripe_world.resources import payouts as payouts_resource

    return payouts_resource.serialize(ctx, row)


def _require_payout(ctx: seahaven.Ctx, payout_id: str) -> dict[str, Any]:
    return _lookup.require_row(ctx, "payouts", "payout", payout_id, param="payout")


def _refuse_unless(payout: dict[str, Any], allowed: tuple[str, ...], action: str) -> None:
    if payout["status"] not in allowed:
        # The wrong-state family every recorded Stripe surface answers with;
        # the payout spelling itself is unrecorded (this account cannot mint
        # one) and declared in the allow-list's structural section.
        raise invalid_request(
            f"This payout could not be {action} because it has a status of "
            f"{payout['status']}. Only a payout with one of the following statuses "
            f"may be {action}: {', '.join(allowed)}."
        )


def cancel_payout(ctx: seahaven.Ctx, payout_id: str) -> dict[str, Any]:
    """`pending` → `canceled`: the reversal bt (`payout_cancel`) returns the
    funds, `failure_balance_transaction` carries it (the spec's own wording:
    the field holds the reversing row for a fail *or* cancel), the sweep
    clears, `payout.canceled` fires."""
    row = _require_payout(ctx, payout_id)
    _refuse_unless(row, ("pending",), "canceled")
    bt = _payout_bt(ctx, row, type_="payout_cancel", bt_amount=row["amount"])
    ctx.db.execute(
        "UPDATE payouts SET status = 'canceled', failure_balance_transaction = ? WHERE id = ?",
        bt["id"],
        row["id"],
    )
    _sweep(ctx, row["id"], row["currency"], clear=True)
    fresh = _require_payout(ctx, row["id"])
    events.emit_event(ctx, type="payout.canceled", obj=_serialize_payout(ctx, fresh))
    return fresh


def fail_payout(ctx: seahaven.Ctx, payout_id: str, *, failure_code: str) -> dict[str, Any]:
    """`pending`/`in_transit` → `failed` with the caller's code — the state
    the magic payout-failure bank values drive live. A `paid` payout is not
    failable: unwinding one is `reverse_payout`'s job, and double-crediting
    against funds that already reached the bank would leave the ledger
    incoherent (no `reversed_by`, two crediting rows). No routed endpoint
    reaches this at all (destinations are stubs here); it is the
    fixture/test surface the same way `settle_payout` is."""
    row = _require_payout(ctx, payout_id)
    _refuse_unless(row, ("pending", "in_transit"), "failed")
    bt = _payout_bt(ctx, row, type_="payout_failure", bt_amount=row["amount"])
    ctx.db.execute(
        "UPDATE payouts SET status = 'failed', failure_balance_transaction = ?,"
        " failure_code = ?, failure_message = ? WHERE id = ?",
        bt["id"],
        failure_code,
        f"Payout failed by {failure_code.replace('_', ' ')}.",
        row["id"],
    )
    _sweep(ctx, row["id"], row["currency"], clear=True)
    fresh = _require_payout(ctx, row["id"])
    events.emit_event(ctx, type="payout.failed", obj=_serialize_payout(ctx, fresh))
    return fresh


def settle_payout(ctx: seahaven.Ctx, payout_id: str) -> dict[str, Any]:
    """`pending` → `paid` — the bank-arrival transition a frozen clock cannot
    wait out. FIXTURE-GENERATOR AND TEST ONLY: it is not routed, for the same
    reason `advance_cycle` is not (`billing/subscription_lifecycle.py`'s
    ruling); the tools an agent can call never move time."""
    row = _require_payout(ctx, payout_id)
    _refuse_unless(row, ("pending", "in_transit"), "paid")
    ctx.db.execute("UPDATE payouts SET status = 'paid' WHERE id = ?", row["id"])
    fresh = _require_payout(ctx, row["id"])
    events.emit_event(ctx, type="payout.paid", obj=_serialize_payout(ctx, fresh))
    return fresh


def reverse_payout(ctx: seahaven.Ctx, payout_id: str) -> dict[str, Any]:
    """`POST /v1/payouts/{payout}/reverse`: a `paid` payout is reversed by a
    NEW payout with the matching negative amount (docs.stripe.com/connect/
    payout-reversals: "payout reversals are considered debits"), cross-linked
    through `original_payout` / `reversed_by` and carrying its own `payout`
    bt. The reversing payout is born `paid` — the funds return inside the
    call, the dispute-settle collapse's ruling — and the documented event
    order fires: `payout.updated` for the original, then the reversal's
    `payout.created` / `payout.paid`."""
    row = _require_payout(ctx, payout_id)
    _refuse_unless(row, ("paid",), "reversed")
    now_iso = ctx.clock.iso()
    id_ = _ids.stripe_id(ctx, "po_", timestamp=now_iso)
    ctx.db.execute(
        "INSERT INTO payouts (id, x_seq, created, amount, arrival_date, automatic,"
        " currency, description, destination, metadata, method, original_payout,"
        " reconciliation_status, source_type, status, type)"
        " VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, '{}', 'standard', ?, 'not_applicable',"
        " 'bank_account', 'paid', 'bank_account')",
        id_,
        _seq.next_seq(ctx, "payouts"),
        now_iso,
        -row["amount"],
        now_iso,
        row["currency"],
        f"Reversal of payout {row['id']}",
        row["destination"],
        row["id"],
    )
    reversal = _require_payout(ctx, id_)
    bt = _payout_bt(ctx, reversal, type_="payout", bt_amount=-reversal["amount"])
    ctx.db.execute("UPDATE payouts SET balance_transaction = ? WHERE id = ?", bt["id"], id_)
    ctx.db.execute("UPDATE payouts SET reversed_by = ? WHERE id = ?", id_, row["id"])
    original = _require_payout(ctx, row["id"])
    events.emit_event(ctx, type="payout.updated", obj=_serialize_payout(ctx, original))
    reversal = _require_payout(ctx, id_)
    events.emit_event(ctx, type="payout.created", obj=_serialize_payout(ctx, reversal))
    events.emit_event(ctx, type="payout.paid", obj=_serialize_payout(ctx, reversal))
    return reversal
