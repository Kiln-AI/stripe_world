"""Seeded identifiers and seeded SQL randomness: the same inputs replay the same stream."""

import random
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import apsw
import pytest

from seahaven import ids
from seahaven.errors import WorldBug
from seahaven.ids import (
    BUILD_STREAM,
    CONTROL_STREAM,
    INSPECTION_STREAM,
    INSTANCE_STREAM,
    Ids,
    instance_seed,
    register_random_functions,
)


def uuids(source: str, caller_seed: int | None = None, count: int = 5) -> list[str]:
    ids = Ids(instance_seed(source, caller_seed))
    return [ids.uuid() for _ in range(count)]


def test_the_same_seed_replays_the_same_stream() -> None:
    assert uuids("agency") == uuids("agency")

    first, second = Ids(instance_seed("agency")), Ids(instance_seed("agency"))
    assert [first.random.random() for _ in range(3)] == [second.random.random() for _ in range(3)]


def test_different_caller_seeds_diverge() -> None:
    assert uuids("agency", 1) != uuids("agency", 2)
    assert uuids("agency", None) != uuids("agency", 0)


def test_the_source_is_mixed_in() -> None:
    assert uuids("agency", 7) != uuids("startup", 7)


def test_no_caller_seed_is_not_seed_zero() -> None:
    """The default seed is a value of its own, not the falsy one a caller might pass."""
    assert instance_seed("agency", 0) != instance_seed("agency", None)
    assert len(instance_seed("agency")) == 32


def test_a_negative_seed_is_refused() -> None:
    with pytest.raises(WorldBug, match="must not be negative"):
        instance_seed("agency", -1)


def test_a_seed_wider_than_eight_bytes_is_refused() -> None:
    with pytest.raises(WorldBug, match="8 bytes"):
        instance_seed("agency", 2**64)


def test_a_seed_of_the_wrong_type_is_refused() -> None:
    with pytest.raises(WorldBug, match="an int or None, not str"):
        instance_seed("agency", "seed")  # ty: ignore[invalid-argument-type]


def test_a_bytes_seed_is_refused() -> None:
    """`bytes` was accepted once; the caller's seed is an int or nothing now."""
    with pytest.raises(WorldBug, match="an int or None, not bytes"):
        instance_seed("agency", b"sixteen bytes!!!")  # ty: ignore[invalid-argument-type]


def test_uuids_are_version_four_shaped() -> None:
    for text in uuids("agency"):
        parsed = uuid.UUID(text)
        assert parsed.version == 4
        assert parsed.variant == uuid.RFC_4122
        assert str(parsed) == text


@pytest.fixture
def seeded() -> Iterator[apsw.Connection]:
    """A connection whose `random()` and `randomblob()` draw from a known seed."""
    with seeded_connection(instance_seed("agency")) as conn:
        yield conn


@pytest.fixture
def plain() -> Iterator[apsw.Connection]:
    """SQLite with no overrides: where the reference behaviour comes from."""
    conn = apsw.Connection(":memory:")
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def seeded_connection(seed: bytes, stream: bytes = INSTANCE_STREAM) -> Iterator[apsw.Connection]:
    """A connection carrying the overrides, hardened the way `db.py` hardens one."""
    conn = apsw.Connection(":memory:")
    # Off, as it is on every connection `db.py` opens: a function a DEFAULT
    # clause or a trigger may call has to be registered as innocuous.
    conn.config(apsw.SQLITE_DBCONFIG_TRUSTED_SCHEMA, 0)
    register_random_functions(conn, seed, stream)
    try:
        yield conn
    finally:
        conn.close()


def rolls(seed: bytes, count: int = 5) -> list[tuple[int, bytes]]:
    """`count` draws of each function from a connection seeded with `seed`."""
    with seeded_connection(seed) as conn:
        return [conn.execute("SELECT random(), randomblob(8)").get for _ in range(count)]


def test_sql_randomness_replays_from_the_instance_seed() -> None:
    assert rolls(instance_seed("agency")) == rolls(instance_seed("agency"))


def test_sql_randomness_diverges_with_the_seed() -> None:
    assert rolls(instance_seed("agency", 1)) != rolls(instance_seed("agency", 2))
    assert rolls(instance_seed("agency")) != rolls(instance_seed("startup"))


def test_sql_randomness_is_a_stream_and_not_a_constant(seeded: apsw.Connection) -> None:
    """Not registered deterministic, so SQLite may not evaluate it once and reuse it.

    Both shapes matter: the repeated call in one statement is the one a
    DETERMINISTIC flag would collapse, and the separate statements are the one a
    constant would.
    """
    assert seeded.execute("SELECT random() <> random()").get
    assert seeded.execute("SELECT random()").get != seeded.execute("SELECT random()").get
    # The shape a DETERMINISTIC flag actually collapses: SQLite factors a
    # constant call out of the loop and evaluates it once for the whole
    # statement, so this would count one value rather than five.
    counted = "WITH n(i) AS (VALUES (1), (2), (3), (4), (5)) SELECT count(DISTINCT random()) FROM n"
    assert seeded.execute(counted).get == 5


def test_no_sql_stream_is_the_one_ctx_ids_draws_from() -> None:
    """`ctx.ids` takes the instance seed itself; every SQL door derives from it.

    An agent that can draw `random()` must not be able to read out the
    identifiers world code is going to mint, which is what one seed handed to
    both streams would let it do. That an agent's draws do not *shift* those
    identifiers is a property of a whole instance, and
    `test_run_sql.py::test_drawing_random_in_sql_does_not_shift_the_worlds_ids`
    is where it is pinned.
    """
    seed = instance_seed("agency")
    mirror = Ids(seed)
    from_ids = [mirror.random.randbytes(8) for _ in range(5)]

    for stream in (INSTANCE_STREAM, INSPECTION_STREAM, CONTROL_STREAM, BUILD_STREAM):
        with seeded_connection(seed, stream) as conn:
            assert [conn.execute("SELECT randomblob(8)").get for _ in range(5)] != from_ids


def test_a_door_replays_its_own_stream_and_echoes_no_other(seeded: apsw.Connection) -> None:
    """The label is what separates one instance's connections from each other.

    Reopening a door replays it from the start rather than continuing it: two
    connections have no deterministic order to continue one stream in. The other
    other doors, on the same seed, draw something else entirely -- otherwise an
    eval reading through `inspect()` would see the bytes the world just wrote,
    and the build that applied the DDL would have spent them first.
    """
    seed = instance_seed("agency")
    drawn = [seeded.execute("SELECT random()").get for _ in range(3)]

    with seeded_connection(seed, INSTANCE_STREAM) as reopened:
        assert [reopened.execute("SELECT random()").get for _ in range(3)] == drawn
        # And the door it replays is where it was left, not back at its start.
        assert seeded.execute("SELECT random()").get not in drawn

    for other in (INSPECTION_STREAM, CONTROL_STREAM, BUILD_STREAM):
        with seeded_connection(seed, other) as door:
            assert [door.execute("SELECT random()").get for _ in range(3)] != drawn


def test_random_is_a_signed_64_bit_integer(seeded: apsw.Connection) -> None:
    """SQLite's own range, which excludes the one value whose `abs()` is itself."""
    drawn = [seeded.execute("SELECT random()").get for _ in range(2000)]

    assert all(isinstance(value, int) for value in drawn)
    assert all(-(2**63) < value < 2**63 for value in drawn)
    # Both signs, and the whole width: a draw folded into 32 bits, or made
    # unsigned, fails here rather than only showing up in a world months later.
    assert min(drawn) < -(2**62)
    assert max(drawn) > 2**62


def test_random_folds_away_the_one_value_whose_abs_is_itself() -> None:
    """`-2**63` is what SQLite's own `randomFunc` goes out of its way to avoid.

    No seed can be asked for that draw -- it is one in `2**63` -- so the draw is
    handed to the override directly. The fold is not cosmetic: `abs(-2**63)` is
    `-2**63` again in SQLite's integers, and a world that took `abs(random())`
    for a magnitude would get a negative one.
    """

    class OneDraw(random.Random):
        def __init__(self, value: int) -> None:
            super().__init__(0)
            self._value = value

        def randbytes(self, n: int) -> bytes:
            return self._value.to_bytes(n, "big", signed=True)

    assert ids._random(OneDraw(-(2**63)))() == 0
    # Every other negative draw keeps its magnitude, as SQLite's fold does.
    assert ids._random(OneDraw(-1))() == -(2**63 - 1)
    assert ids._random(OneDraw(12345))() == 12345


def test_randomblob_returns_the_bytes_it_was_asked_for(seeded: apsw.Connection) -> None:
    assert len(seeded.execute("SELECT randomblob(17)").get) == 17
    assert seeded.execute("SELECT typeof(randomblob(4))").get == "blob"
    # Two draws of the same size are two different blobs.
    assert seeded.execute("SELECT randomblob(16) <> randomblob(16)").get


@pytest.mark.parametrize(
    "argument",
    [0, -1, 1, 5, 3.9, -3.9, 2.0, "4", "  7  ", "3.9", "abc", "", "12abc", "0x10", " +9", "-5"],
)
def test_randomblob_reads_its_argument_as_sqlite_does(
    seeded: apsw.Connection, plain: apsw.Connection, argument: object
) -> None:
    """Held against SQLite's own function, because `_int64` is a reading of it.

    `randomblob` coerces rather than refusing: SQLite reads the argument as an
    int64 and raises anything below 1 to 1, so every one of these has a length
    and none of them is an error.
    """
    reference = plain.execute("SELECT length(randomblob(?))", (argument,)).get

    assert seeded.execute("SELECT length(randomblob(?))", (argument,)).get == reference


@pytest.mark.parametrize("argument", [None, b"6", 9223372036854775807, "99999999999999999999999"])
def test_randomblob_reads_the_argument_shapes_a_parameter_cannot_spell(
    seeded: apsw.Connection, plain: apsw.Connection, argument: object
) -> None:
    """NULL, a blob, and the two lengths that are refused rather than drawn.

    Split from the case above because these either return no length to compare
    or raise on both connections; what is checked is that both say the same
    thing.
    """
    plain.limit(apsw.SQLITE_LIMIT_LENGTH, 1000)
    seeded.limit(apsw.SQLITE_LIMIT_LENGTH, 1000)
    sql = "SELECT length(randomblob(?))"

    try:
        reference = plain.execute(sql, (argument,)).get
    except apsw.TooBigError:
        with pytest.raises(apsw.TooBigError):
            seeded.execute(sql, (argument,))
        return

    assert seeded.execute(sql, (argument,)).get == reference


def test_a_default_clause_and_a_trigger_may_draw(seeded: apsw.Connection) -> None:
    """Registered as innocuous, on a connection with TRUSTED_SCHEMA off.

    Without the flag SQLite refuses the call from the schema, which is what stops
    a DEFAULT clause reaching a wall clock -- and would stop a world stamping a
    seeded token on a row it inserts.
    """
    seeded.execute(
        "CREATE TABLE tokens ("
        "  id INTEGER NOT NULL PRIMARY KEY,"
        "  token BLOB NOT NULL DEFAULT (randomblob(8)),"
        "  roll INTEGER"
        ") STRICT"
    )
    seeded.execute(
        "CREATE TRIGGER roll AFTER INSERT ON tokens BEGIN"
        "  UPDATE tokens SET roll = random() WHERE id = NEW.id;"
        " END"
    )
    seeded.execute("INSERT INTO tokens (id) VALUES (1), (2)")

    rows = seeded.execute("SELECT length(token), hex(token), roll FROM tokens ORDER BY id")
    drawn = rows.fetchall()

    assert [length for length, _token, _roll in drawn] == [8, 8]
    # A draw per row, from the schema as from a statement: the two rows share
    # neither their token nor their roll.
    assert len({token for _length, token, _roll in drawn}) == 2
    assert len({roll for _length, _token, roll in drawn}) == 2


def test_the_number_of_arguments_is_sqlites_own(seeded: apsw.Connection) -> None:
    """A wrong arity is SQLite's error, not a Python one leaking out of the override."""
    for wrong in ("SELECT random(1)", "SELECT randomblob()", "SELECT randomblob(1, 2)"):
        with pytest.raises(apsw.SQLError, match="wrong number of arguments"):
            seeded.execute(wrong)
