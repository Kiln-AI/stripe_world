"""The checks a fixture must pass, at build time and in the tests (replay.md §7; architecture
§3.1): the consistency invariants of functional spec §3.4, the realism guards of §3.2, and the
fixture's own expectations. One module, so build-time and test-time checks cannot drift."""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import seahaven
from fixtures_src.replay.cards import ALL
from fixtures_src.replay.clock import plus

from seahaven_stripe_world import _time
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

# Instants that point forward by design (functional spec §3.4), by column or JSON key name: a
# period's end (an item's current period, an invoice item's or a line's period, a discount's
# `end`, a schedule phase's `end_date`), a trial's end and the billing anchor it sets, a scheduled
# cancellation, due dates, the next retry or finalization, when funds become available or a payout
# arrives, and a coupon's last redemption date. Every date inside a schedule's `phases` is forward
# too: its later phases have not started.
_FORWARD = frozenset(
    {
        "arrival_date",
        "automatically_finalizes_at",
        "available_on",
        "billing_cycle_anchor",
        "cancel_at",
        "current_period_end",
        "due_by",
        "due_date",
        "end",
        "end_date",
        "next_payment_attempt",
        "period_end",
        "redeem_by",
        "trial_end",
    }
)
# Canonical UTC text, the one form a TEXT timestamp column is written in (`_time`).
_ISO_TEXT = (
    "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z"
)
# A JSON key naming an instant, which stored JSON holds as Unix seconds (the API's form).
_TIME_KEY = re.compile(
    r"created|date|updated|start|end|billing_cycle_anchor|.+_(at|date|start|end|on|by)"
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


def _tables(db: seahaven.Db) -> list[str]:
    """Every ordinary table. The FTS virtual tables and their shadow tables hold no rows of their
    own: they index the tables they name."""
    return [
        str(row["name"])
        for row in db.rows(
            "SELECT name FROM pragma_table_list WHERE schema = 'main' AND type = 'table'"
            " AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def _is_past_instant(column: str, path: str, key: object) -> bool:
    """Whether the integer at `path` in JSON column `column` is an instant that must not be after
    NOW: a time-named key, not a forward one, and not inside a schedule's phases."""
    return (
        isinstance(key, str)
        and _TIME_KEY.fullmatch(key) is not None
        and key not in _FORWARD
        and column != "phases"
        and ".phases[" not in path
    )


def _json_integers_after(db: seahaven.Db, table: str, key: str, column: str, now_unix: int) -> Rows:
    """Every integer above `now_unix` inside `table.column`'s JSON objects and arrays, with its
    path and key. Only values that are JSON documents are walked: a column's plain text is not."""
    col = f'"{column}"'
    return db.rows(
        f'WITH docs AS MATERIALIZED (SELECT {key} AS id, {col} AS doc FROM "{table}"'
        f" WHERE typeof({col}) = 'text' AND substr({col}, 1, 1) IN ('{{', '[')"
        f" AND json_valid({col}))"
        " SELECT docs.id AS id, j.fullkey AS path, j.key AS key, j.value AS v"
        " FROM docs, json_tree(docs.doc) AS j"
        " WHERE j.type = 'integer' AND j.value > ? ORDER BY docs.id, j.fullkey",
        now_unix,
    )


def _nothing_after_now(db: seahaven.Db, now: str) -> Rows:
    """Every timestamp at or before NOW, except the forward ones: each TEXT value in canonical
    UTC form, and each time-named integer inside a JSON column, in every table."""
    now_unix = _time.to_unix(now)
    bad: Rows = []
    for table in _tables(db):
        columns = [
            str(col["name"])
            for col in db.rows("SELECT name FROM pragma_table_info(?) ORDER BY cid", table)
        ]
        key = "id" if "id" in columns else "rowid"
        for column in columns:
            if column not in _FORWARD:
                bad.extend(
                    {"table": table, "column": column, "id": row["id"], "value": row["v"]}
                    for row in db.rows(
                        f'SELECT {key} AS id, "{column}" AS v FROM "{table}"'
                        f' WHERE "{column}" GLOB ? AND "{column}" > ? ORDER BY {key}',
                        _ISO_TEXT,
                        now,
                    )
                )
            bad.extend(
                {
                    "table": table,
                    "column": f"{column}{row['path'][1:]}",
                    "id": row["id"],
                    "value": _time.from_unix(int(row["v"])),
                }
                for row in _json_integers_after(db, table, key, column, now_unix)
                if _is_past_instant(column, str(row["path"]), row["key"])
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
        if _denied_domain(str(row["email"]).lower().rsplit("@", 1)[-1])
    ]


def _denied_domain(domain: str) -> bool:
    """A denied domain or any subdomain of one (`mail.yahoo.com`)."""
    return any(domain == denied or domain.endswith(f".{denied}") for denied in DENIED_EMAIL_DOMAINS)


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
            " ON b.parent_subscription = a.parent_subscription AND b.x_seq > a.x_seq"
            " WHERE a.billing_reason = 'subscription_cycle'"
            " AND b.billing_reason = 'subscription_cycle'"
            " AND a.status <> 'void' AND b.status <> 'void'"
            " AND a.period_start < b.period_end AND b.period_start < a.period_end"
            " ORDER BY a.id, b.id"
        ),
    ),
    (
        # An invoice's own period looks back while its lines bill ahead, so only the lines show
        # a subscription item billed twice for one span: two of its non-proration lines that bill
        # something, on invoices not voided, whose periods overlap. A $0 line bills nothing: a
        # trial's `Free trial` line may overlap the first paid period when the trial ends early.
        "no overlapping item line periods",
        _sql(
            "WITH billed AS MATERIALIZED (SELECT i.id AS invoice, i.x_seq AS seq, l.key AS idx,"
            " json_extract(l.value, '$.parent.subscription_item_details.subscription_item')"
            " AS item, json_extract(l.value, '$.period.start') AS start_at,"
            " json_extract(l.value, '$.period.end') AS end_at"
            " FROM invoices i, json_each(i.lines) l WHERE i.status <> 'void'"
            " AND json_extract(l.value, '$.parent.subscription_item_details.subscription_item')"
            " IS NOT NULL"
            " AND COALESCE(json_extract(l.value,"
            " '$.parent.subscription_item_details.proration'), 0) = 0"
            " AND json_extract(l.value, '$.amount') <> 0)"
            " SELECT a.invoice AS a, b.invoice AS b, a.item AS item FROM billed a JOIN billed b"
            " ON b.item = a.item AND (b.seq > a.seq OR (b.seq = a.seq AND b.idx > a.idx))"
            " WHERE a.start_at < b.end_at AND b.start_at < a.end_at"
            " ORDER BY a.invoice, b.invoice, a.item"
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
