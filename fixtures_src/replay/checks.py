"""The checks a fixture must pass, at build time and in the tests (replay.md §7; architecture
§3.1): the consistency invariants of functional spec §3.4, the realism guards of §3.2, and the
fixture's own expectations. One module, so build-time and test-time checks cannot drift."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import seahaven
from fixtures_src.replay.cards import ALL
from fixtures_src.replay.clock import plus

from seahaven_stripe_world.billing import ledger, magic_cards

__all__ = [
    "DENIED_EMAIL_DOMAINS",
    "DENIED_NAMES",
    "Expectation",
    "check_fixture",
    "violations",
]


@dataclass(frozen=True)
class Expectation:
    name: str
    sql: str  # SELECT count(*) AS n ...
    low: int
    high: int  # equal to low for an exact count


DENIED_EMAIL_DOMAINS = (
    "gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com", "live.com",
    "icloud.com", "me.com", "aol.com", "proton.me", "example.com", "example.org", "example.net",
)  # fmt: skip
DENIED_NAMES = (
    "acme", "contoso", "northwind", "initech", "globex", "hooli", "umbrella corp",
    "wayne enterprises", "stark industries",
)  # fmt: skip

# Timestamp columns that point forward by design (functional spec §3.4).
_FORWARD = frozenset(
    {
        "current_period_end",
        "trial_end",
        "due_date",
        "next_payment_attempt",
        "automatically_finalizes_at",
        "available_on",
        "arrival_date",
        "redeem_by",
        "cancel_at",
        "end_date",
    }
)
_PAST_COLUMNS = (
    "created",
    "canceled_at",
    "ended_at",
    "voided_at",
    "effective_at",
    "released_at",
    "completed_at",
    "date",
)

Rows = list[dict[str, Any]]
Check = Callable[[seahaven.Db, str], Rows]


def _sql(query: str) -> Check:
    def run(db: seahaven.Db, now: str) -> Rows:
        return db.rows(query.replace(":now", "?"), *([now] * query.count(":now")))

    return run


def _bt_status(db: seahaven.Db, now: str) -> Rows:
    return [
        row
        for row in db.rows("SELECT id, available_on, status FROM balance_transactions ORDER BY id")
        if row["status"] != ledger.bt_status(row["available_on"], now)
    ]


def _payouts_overdrew(db: seahaven.Db, _now: str) -> Rows:
    bad: Rows = []
    for payout in db.rows(
        "SELECT id, x_seq, created FROM balance_transactions"
        " WHERE type = 'payout' AND balance_type = 'payments' ORDER BY x_seq"
    ):
        total = db.one(
            "SELECT COALESCE(SUM(net), 0) AS total FROM balance_transactions"
            " WHERE balance_type = 'payments' AND currency = (SELECT currency FROM"
            " balance_transactions WHERE id = ?) AND x_seq <= ? AND available_on <= ?",
            payout["id"],
            payout["x_seq"],
            payout["created"],
        )
        if total is not None and int(total["total"]) < 0:
            bad.append({"id": payout["id"], "balance_after": int(total["total"])})
    return bad


def _nothing_after_now(db: seahaven.Db, now: str) -> Rows:
    bad: Rows = []
    tables = db.rows(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE '%fts%'"
        " AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    for table in tables:
        name = str(table["name"])
        columns = [
            str(col["name"])
            for col in db.rows("SELECT name FROM pragma_table_info(?) ORDER BY cid", name)
        ]
        for column in columns:
            if column in _PAST_COLUMNS and column not in _FORWARD:
                bad.extend(
                    {"table": name, "column": column, "id": row["id"], "value": row["v"]}
                    for row in db.rows(
                        f'SELECT rowid AS id, "{column}" AS v FROM "{name}"'
                        f' WHERE "{column}" > ? ORDER BY rowid',
                        now,
                    )
                )
    return bad


def _events_window(db: seahaven.Db, now: str) -> Rows:
    return db.rows(
        "SELECT id, created FROM events WHERE created < ? ORDER BY id", plus(now, days=-30)
    )


def _card_fingerprints(db: seahaven.Db, _now: str) -> Rows:
    allowed = {magic_cards.fingerprint(card.number) for card in ALL}
    return [
        row
        for row in db.rows(
            "SELECT id, json_extract(rail, '$.fingerprint') AS fp FROM payment_methods"
            " WHERE type = 'card' ORDER BY id"
        )
        if row["fp"] not in allowed
    ]


def _email_domains(db: seahaven.Db, _now: str) -> Rows:
    return [
        row
        for row in db.rows("SELECT id, email FROM customers WHERE email IS NOT NULL ORDER BY id")
        if str(row["email"]).lower().rsplit("@", 1)[-1] in DENIED_EMAIL_DOMAINS
    ]


def _cliche_names(db: seahaven.Db, _now: str) -> Rows:
    rows = db.rows(
        "SELECT id, name FROM customers WHERE name IS NOT NULL"
        " UNION ALL SELECT id, name FROM products ORDER BY id"
    )
    return [row for row in rows if any(word in str(row["name"]).lower() for word in DENIED_NAMES)]


INVARIANTS: tuple[tuple[str, Check], ...] = (
    (
        "net is amount - fee",
        _sql("SELECT id FROM balance_transactions WHERE net <> amount - fee ORDER BY id"),
    ),
    ("balance status agrees with bt_status at NOW", _bt_status),
    ("no payout overdrew", _payouts_overdrew),
    (
        "no over-refund",
        _sql(
            "SELECT c.id FROM charges c WHERE (SELECT COALESCE(SUM(amount), 0) FROM refunds r"
            " WHERE r.charge = c.id AND r.status = 'succeeded') > c.amount_captured ORDER BY c.id"
        ),
    ),
    (
        "lines sum to subtotal",
        _sql(
            "SELECT id FROM invoices WHERE subtotal <> (SELECT"
            " COALESCE(SUM(json_extract(l.value, '$.amount')), 0)"
            " FROM json_each(invoices.lines) l) ORDER BY id"
        ),
    ),
    (
        "no overlapping cycle periods",
        _sql(
            "SELECT a.id AS a, b.id AS b FROM invoices a JOIN invoices b"
            " ON a.parent_subscription = b.parent_subscription AND a.id < b.id"
            " WHERE a.billing_reason = 'subscription_cycle'"
            " AND b.billing_reason = 'subscription_cycle'"
            " AND a.status <> 'void' AND b.status <> 'void'"
            " AND a.period_start < b.period_end AND b.period_start < a.period_end ORDER BY a.id"
        ),
    ),
    (
        "causality: charge after its payment intent",
        _sql(
            "SELECT c.id FROM charges c JOIN payment_intents p ON p.id = c.payment_intent"
            " WHERE c.created < p.created ORDER BY c.id"
        ),
    ),
    (
        "causality: refund and dispute after their charge",
        _sql(
            "SELECT r.id FROM refunds r JOIN charges c ON c.id = r.charge"
            " WHERE r.created < c.created"
            " UNION ALL SELECT d.id FROM disputes d JOIN charges c ON c.id = d.charge"
            " WHERE d.created < c.created ORDER BY 1"
        ),
    ),
    (
        "causality: balance transaction after its source",
        _sql(
            "SELECT bt.id FROM balance_transactions bt WHERE bt.created < COALESCE("
            " (SELECT created FROM charges WHERE id = bt.source),"
            " (SELECT created FROM refunds WHERE id = bt.source),"
            " (SELECT created FROM disputes WHERE id = bt.source),"
            " (SELECT created FROM payouts WHERE id = bt.source), bt.created) ORDER BY bt.id"
        ),
    ),
    (
        "customer balance is the latest ending balance",
        _sql(
            "SELECT c.id FROM customers c WHERE c.balance <> COALESCE((SELECT ending_balance"
            " FROM customer_balance_transactions t WHERE t.customer = c.id"
            " ORDER BY t.x_seq DESC LIMIT 1), 0) ORDER BY c.id"
        ),
    ),
    (
        "next invoice sequence follows the highest number",
        _sql(
            "SELECT c.id FROM customers c WHERE c.next_invoice_sequence <> 1 + COALESCE((SELECT"
            " MAX(CAST(substr(i.number, instr(i.number, '-') + 1) AS INTEGER)) FROM invoices i"
            " WHERE i.customer = c.id AND i.number IS NOT NULL), 0) ORDER BY c.id"
        ),
    ),
    (
        "subscription status agrees with its latest invoice",
        _sql(
            "SELECT s.id, s.status FROM subscriptions s LEFT JOIN invoices i"
            " ON i.id = s.latest_invoice WHERE"
            " (s.status IN ('past_due', 'unpaid') AND COALESCE(i.status, '') NOT IN"
            " ('open', 'draft'))"
            " OR (s.status = 'trialing' AND s.trial_end <= :now)"
            " OR (s.status IN ('canceled', 'incomplete_expired') AND s.ended_at IS NULL)"
            " ORDER BY s.id"
        ),
    ),
    ("nothing after NOW", _nothing_after_now),
    ("events cover the last 30 days", _events_window),
    ("cards are known test cards", _card_fingerprints),
    ("no webmail or example email domains", _email_domains),
    ("no sample-data cliche names", _cliche_names),
)


def violations(db: seahaven.Db, *, now: str, expect: Sequence[Expectation] = ()) -> list[str]:
    """Every failed invariant, guard and expectation, each naming up to five offending rows."""
    failures: list[str] = []
    for name, check in INVARIANTS:
        bad = check(db, now)
        if bad:
            failures.append(f"{name}: {len(bad)} rows, e.g. {bad[:5]}")
    for expectation in expect:
        row = db.one(expectation.sql)
        n = int(row["n"]) if row is not None else 0
        if not expectation.low <= n <= expectation.high:
            span = (
                str(expectation.low)
                if expectation.low == expectation.high
                else f"{expectation.low}..{expectation.high}"
            )
            failures.append(f"expectation {expectation.name}: {n}, wanted {span}")
    return failures


def check_fixture(db: seahaven.Db, *, now: str, expect: Sequence[Expectation] = ()) -> None:
    """Raise AssertionError naming every failed check (`db` is `inst.inspect()`)."""
    failures = violations(db, now=now, expect=expect)
    if failures:
        raise AssertionError("fixture checks failed:\n- " + "\n- ".join(failures))
