"""The frozen clock, and SQL reading it through SQLite's own date functions."""

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone

import apsw
import pytest

from seahaven.clock import CANONICAL_FORMAT, Clock, register_clock_functions
from seahaven.db import Db
from seahaven.errors import WorldBug
from tests.conftest import INSTANT

CANONICAL = f"strftime('{CANONICAL_FORMAT}', ?)"


@contextmanager
def _timezone(name: str) -> Iterator[None]:
    """Run the block with the process timezone set, as SQLite reads it.

    `name` is a POSIX `TZ` string, so nothing here needs the timezone database.
    """
    previous = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = previous
        time.tzset()


# Each overridden expression, and the expression SQLite itself evaluates on the
# instant to produce what the override must return.
EQUIVALENTS = [
    ("date('now')", "date(?)"),
    ("date()", "date(?)"),
    ("date('NOW')", "date(?)"),
    ("date('now', '+1 day')", "date(?, '+1 day')"),
    ("date('now', 'start of month')", "date(?, 'start of month')"),
    ("date('now', 'weekday 0')", "date(?, 'weekday 0')"),
    ("date('2021-05-06')", "date('2021-05-06')"),
    ("time('now')", "time(?)"),
    ("time()", "time(?)"),
    ("datetime('now')", CANONICAL),
    ("datetime()", CANONICAL),
    ("datetime('now', 'start of month')", f"strftime('{CANONICAL_FORMAT}', ?, 'start of month')"),
    ("datetime('2021-05-06 07:08:09')", "strftime('%Y-%m-%dT%H:%M:%fZ', '2021-05-06 07:08:09')"),
    ("julianday('now')", "julianday(?)"),
    ("julianday()", "julianday(?)"),
    ("unixepoch('now')", "unixepoch(?)"),
    ("timediff('now', '2020-01-01')", "timediff(?, '2020-01-01')"),
    ("timediff('2020-01-01', 'now')", "timediff('2020-01-01', ?)"),
    ("strftime('%Y-%m-%d %H:%M:%f', 'now')", "strftime('%Y-%m-%d %H:%M:%f', ?)"),
    ("strftime('%s')", "strftime('%s', ?)"),
    ("strftime('%Y', 'now', '+2 years')", "strftime('%Y', ?, '+2 years')"),
    ("current_timestamp", CANONICAL),
    ("current_date", "date(?)"),
    ("current_time", "time(?)"),
    # `'now'` as a *format* is the literal text, not a time value.
    ("strftime('now', '2020-01-01')", "strftime('now', '2020-01-01')"),
    # No more permissive than SQLite: padded `now` is not a time value either.
    ("date(' now ')", "date(' now ')"),
]


@pytest.fixture
def overridden(clock: Clock) -> Iterator[apsw.Connection]:
    """A connection reading the clock, closed with the helper it owns."""
    conn = apsw.Connection(":memory:")
    helper = register_clock_functions(conn, clock)
    try:
        yield conn
    finally:
        conn.close()
        helper.close()


@pytest.fixture
def plain() -> Iterator[apsw.Connection]:
    """SQLite with no overrides: where the reference values come from."""
    conn = apsw.Connection(":memory:")
    try:
        yield conn
    finally:
        conn.close()


@pytest.mark.parametrize(("overridden_sql", "reference_sql"), EQUIVALENTS)
def test_override_matches_sqlite_on_the_instant(
    overridden: apsw.Connection,
    plain: apsw.Connection,
    clock: Clock,
    overridden_sql: str,
    reference_sql: str,
) -> None:
    expected = plain.execute(
        f"SELECT {reference_sql}", (clock.iso(),) * reference_sql.count("?")
    ).get

    assert overridden.execute(f"SELECT {overridden_sql}").get == expected


def test_the_instant_is_rendered_canonically(overridden: apsw.Connection, clock: Clock) -> None:
    assert overridden.execute("SELECT current_timestamp").get == clock.iso()
    assert overridden.execute("SELECT datetime('now')").get == clock.iso()
    assert clock.iso() == "2024-03-05T12:00:00.123Z"


def test_a_default_clause_reads_the_clock(db: Db) -> None:
    # STRICT, and on a connection with TRUSTED_SCHEMA off: the schema may call
    # the override only because it is registered as innocuous.
    db.execute(
        "CREATE TABLE notes ("
        "  id TEXT NOT NULL PRIMARY KEY,"
        "  created_at TEXT NOT NULL DEFAULT (current_timestamp)"
        ") STRICT"
    )
    db.execute("INSERT INTO notes (id) VALUES ('a')")

    assert db.one("SELECT created_at FROM notes") == {"created_at": Clock(INSTANT).iso()}


def test_a_trigger_reads_the_clock(db: Db) -> None:
    db.execute("CREATE TABLE notes (id TEXT NOT NULL PRIMARY KEY, touched_at TEXT) STRICT")
    db.execute(
        "CREATE TRIGGER stamp AFTER INSERT ON notes BEGIN"
        "  UPDATE notes SET touched_at = datetime('now') WHERE id = NEW.id;"
        " END"
    )
    db.execute("INSERT INTO notes (id) VALUES ('a')")

    assert db.one("SELECT touched_at FROM notes") == {"touched_at": Clock(INSTANT).iso()}


def test_clock_values_compare_against_stored_timestamps(db: Db, clock: Clock) -> None:
    before = Clock(INSTANT - timedelta(seconds=1)).iso()
    after = Clock(INSTANT + timedelta(milliseconds=1)).iso()
    db.execute("CREATE TABLE notes (id TEXT NOT NULL PRIMARY KEY, created_at TEXT NOT NULL) STRICT")
    db.executemany(
        "INSERT INTO notes (id, created_at) VALUES (?, ?)",
        [("before", before), ("at", clock.iso()), ("after", after)],
    )

    later = db.rows("SELECT id FROM notes WHERE created_at > CURRENT_TIMESTAMP ORDER BY id")

    # Exactly the rows after the instant: a row written *at* it is not after it.
    assert later == [{"id": "after"}]
    assert db.rows("SELECT id FROM notes WHERE created_at <= datetime('now') ORDER BY id") == [
        {"id": "at"},
        {"id": "before"},
    ]


def test_localtime_is_not_the_instant(overridden: apsw.Connection, clock: Clock) -> None:
    """The documented wart: `'localtime'` leaves the canonical `Z` on local time."""
    if not hasattr(time, "tzset"):
        pytest.skip("the process timezone cannot be set on this platform")

    # A POSIX offset rather than a zone name: `tzset` parses it without the
    # timezone database, which a slim image may not carry.
    with _timezone("XXX8"):
        local = overridden.execute("SELECT datetime('now', 'localtime')").get

    # Still canonical text, and still stamped `Z` -- on a value that is not UTC.
    # A world's SQL has no business calling it; this pins what it does if it does.
    assert local == "2024-03-05T04:00:00.123Z"
    assert local != clock.iso()


def test_clock_requires_an_aware_instant() -> None:
    with pytest.raises(WorldBug, match="timezone-aware"):
        Clock(datetime(2024, 3, 5, 12, 0))


def test_an_instant_is_kept_at_the_precision_it_is_rendered_at() -> None:
    finer = Clock(INSTANT.replace(microsecond=123_456))

    # Two instants nothing in the framework can tell apart are the same clock.
    assert finer == Clock(INSTANT)
    assert hash(finer) == hash(Clock(INSTANT))
    assert finer.iso() == Clock(INSTANT).iso()
    assert Clock.from_iso(finer.iso()) == finer
    assert finer.now().microsecond == 123_000


def test_clock_normalises_to_utc_and_compares_by_instant() -> None:
    elsewhere = Clock(datetime(2024, 3, 5, 14, 0, 0, 123000, tzinfo=timezone(timedelta(hours=2))))

    assert elsewhere == Clock(INSTANT)
    assert hash(elsewhere) == hash(Clock(INSTANT))
    assert elsewhere.now() == INSTANT
    assert elsewhere.now().tzinfo == UTC
    assert Clock(INSTANT) != INSTANT


def test_clock_round_trips_through_canonical_text() -> None:
    assert Clock.from_iso(Clock(INSTANT).iso()) == Clock(INSTANT)
    assert repr(Clock(INSTANT)) == "Clock(2024-03-05T12:00:00.123Z)"


def test_from_iso_refuses_text_that_is_not_a_timestamp() -> None:
    with pytest.raises(WorldBug, match="not a timestamp"):
        Clock.from_iso("the fifth of March")


def test_wall_truncates_to_milliseconds() -> None:
    wall = Clock.wall()

    assert wall.now().microsecond % 1000 == 0
    assert wall.now().tzinfo == UTC
    assert abs((wall.now() - datetime.now(UTC)).total_seconds()) < 5
