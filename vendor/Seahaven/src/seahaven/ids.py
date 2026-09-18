"""The one source of randomness inside an instance, in Python and in SQL.

An instance is meant to replay: the same fixture and the same caller seed give
the same identifiers, so a test can assert on an id and two runs can be compared
change log to change log. That rules out `uuid.uuid4`, which reads the OS entropy
pool. World code draws from `ctx.ids` instead.

SQL is the other door. `random()` and `randomblob()` are overridden on every
connection Seahaven opens over an instance's file -- the one that builds it from
the world's DDL included -- the way `clock.py` overrides the date and time
functions, so that SQL an agent wrote and a `DEFAULT` clause a world wrote are
both seeded rather than denied. Each connection draws from a stream of its own,
named by the door it is, so no two of them and not `ctx.ids` either hand out the
same values from the one instance seed.
"""

import hashlib
import math
import random
import re
import uuid
import weakref
from typing import TYPE_CHECKING

import apsw

from seahaven.errors import WorldBug

if TYPE_CHECKING:  # names that live in apsw's stubs, not in the extension module
    from apsw import ScalarProtocol, SQLiteValue

__all__ = [
    "BUILD_STREAM",
    "CONTROL_STREAM",
    "INSPECTION_STREAM",
    "INSTANCE_STREAM",
    "Ids",
    "instance_seed",
    "register_random_functions",
]

# What `seed=None` means: a named constant rather than a bare literal, because
# the default seed is part of the reproducibility contract.
DEFAULT_CALLER_SEED = b"default"


def instance_seed(source: str, caller_seed: int | None = None) -> bytes:
    """Derive an instance's seed from what it came from and what the caller asked for.

    `source` is the fixture id, or the world name for a blank instance, so the
    same caller seed against two fixtures gives two streams.
    """
    return hashlib.sha256(source.encode("utf-8") + b"\0" + _seed_bytes(caller_seed)).digest()


def _seed_bytes(caller_seed: int | None) -> bytes:
    match caller_seed:
        case None:
            return DEFAULT_CALLER_SEED
        case int():
            if caller_seed < 0:
                raise WorldBug(f"a seed must not be negative: {caller_seed}")
            try:
                return caller_seed.to_bytes(8, "big")
            except OverflowError as error:
                raise WorldBug(f"a seed must fit in 8 bytes: {caller_seed}") from error
        case _:
            raise WorldBug(f"a seed must be an int or None, not {type(caller_seed).__name__}")


class Ids:
    """Seeded identifiers and randomness, world-agnostic.

    Product-shaped keys (`ENG-13`, a sequential invoice number) are the world's
    own business, built on `random` or on its tables; this is the stream they
    draw from.
    """

    def __init__(self, seed: bytes) -> None:
        self.random = random.Random(int.from_bytes(seed))

    def uuid(self) -> str:
        """A UUIDv4-shaped identifier drawn from the seeded stream."""
        return str(uuid.UUID(int=self.random.getrandbits(128), version=4))


# The door each of an instance's connections opens, as the label its stream
# carries. A stream is derived from the instance seed by `instance_seed`'s shape
# -- sha256 over NUL-separated inputs -- while `ctx.ids` takes the instance seed
# itself, so a run is still determined by the seed and the fixture alone and no
# two of these hand out the same bytes. That is what stops an agent's
# `SELECT random()` shifting the identifiers world code mints after it, or
# reading out what they will be, and what stops the read-only doors echoing the
# writable one.
#
# `BUILD_STREAM` is the odd one: its connection is gone before the instance is
# open, and it exists so that a schema file that seeds rows draws bytes no later
# door redraws. Every door draws from the start of its stream each time it is
# opened, so a build that drew from `INSTANCE_STREAM` would hand a schema-seeded
# row exactly the bytes the world's own first draw is about to take.
INSTANCE_STREAM = b"instance"
INSPECTION_STREAM = b"inspection"
CONTROL_STREAM = b"control"
BUILD_STREAM = b"build"

# Registered on connections that have SQLITE_DBCONFIG_TRUSTED_SCHEMA off, where
# INNOCUOUS is what keeps them callable from a DEFAULT clause or a trigger.
# Deliberately not DETERMINISTIC: SQLite is free to evaluate a deterministic
# function once and reuse the value, and `random()` has to be asked every time.
_RANDOM_FUNCTION_FLAGS = apsw.SQLITE_INNOCUOUS


# SQLite's own PRNG is the better mechanism and is not reachable, so these
# overrides draw from a seeded `random.Random` instead. `sqlite3_randomness` is
# seeded once per *process*, from the default VFS's `xRandomness`, and every
# connection then draws from that one shared state: seeding it through a VFS
# would make the instances of one process share a stream and interleave it,
# which is the opposite of what an instance's seed promises. Per-connection
# seeding exists only as `SQLITE_TESTCTRL_PRNG_SEED`, a test-control API that
# APSW does not expose and that is not a supportable surface to reach for.
def register_random_functions(conn: apsw.Connection, seed: bytes, stream: bytes) -> None:
    """Point `random()` and `randomblob()` on `conn` at a stream of its own.

    `seed` is the instance's and `stream` names the door this connection is, one
    of the four labels above. The seed says which run this is and the label
    which door, so a door replays value for value across two runs of one seed
    and no two doors of one instance hand out the same bytes. A door draws from
    the start of its stream every time it is opened: two connections have no
    deterministic order to continue one stream in.
    """
    draws = random.Random(int.from_bytes(_stream_seed(seed, stream)))
    conn.create_scalar_function("random", _random(draws), 0, flags=_RANDOM_FUNCTION_FLAGS)
    conn.create_scalar_function(
        "randomblob", _randomblob(conn, draws), 1, flags=_RANDOM_FUNCTION_FLAGS
    )


def _stream_seed(seed: bytes, stream: bytes) -> bytes:
    return hashlib.sha256(seed + b"\0" + stream).digest()


def _random(draws: random.Random) -> ScalarProtocol:
    # `*_args` because apsw types a scalar function as one taking any number of
    # them; SQLite passes none, since `random` is registered as taking none.
    def evaluate(*_args: SQLiteValue) -> int:
        value = int.from_bytes(draws.randbytes(8), "big", signed=True)
        # SQLite's own `randomFunc` folds a negative draw onto `-(r & LARGEST_INT64)`,
        # so that `random()` never returns the one value whose `abs()` is itself.
        return -(value & _LARGEST_INT64) if value < 0 else value

    return evaluate


def _randomblob(conn: apsw.Connection, draws: random.Random) -> ScalarProtocol:
    # A weak reference and not the connection itself. A strong one is a cycle --
    # the connection holds its function table, the function holds the connection
    # -- and it is not a cycle the collector can break, because APSW's connection
    # does not walk its function table for it: `gc.collect()` leaves it, and a
    # `Db` that is dropped without `close()` then leaks a live SQLite connection
    # for the life of the process. Pinned by
    # `test_db.py::test_a_dropped_database_does_not_leak_its_connection`.
    connection = weakref.ref(conn)

    def evaluate(*args: SQLiteValue) -> bytes:
        # Exactly one argument, because `randomblob` is registered as taking one.
        (size,) = args
        # SQLite's own `randomBlob` reads the argument as an int64, raises a
        # length below 1 to 1, and refuses a length over the connection's
        # SQLITE_LIMIT_LENGTH *before* it allocates. The limit is read here for
        # the same reason: `sandbox.run_statement` lowers it to `MAX_VALUE_BYTES`
        # for the length of an agent's statement, and a `randomblob(1000000000)`
        # that drew its bytes first would be a gigabyte allocated in Python
        # before SQLite ever saw the value.
        length = max(_int64(size), 1)
        alive = connection()
        if alive is None:
            # Unreachable: SQLite calls a function only on the connection it is
            # registered on, which is alive for as long as it can do so.
            raise WorldBug("randomblob drew on a connection that is gone")
        if length > alive.limit(apsw.SQLITE_LIMIT_LENGTH):
            raise apsw.TooBigError("string or blob too big")
        return draws.randbytes(length)

    return evaluate


_LARGEST_INT64 = 2**63 - 1
_SMALLEST_INT64 = -(2**63)

# What `sqlite3Atoi64` reads out of text: SQLite's own whitespace, a sign and
# decimal digits, with whatever follows ignored. No hex and no exponent, so
# `'0x10'` reads as the `0` and `'1e3'` as the `1`.
_LEADING_INTEGER = re.compile(r"[ \t\n\v\f\r]*([+-]?[0-9]+)")


def _int64(value: SQLiteValue) -> int:
    """A value as `sqlite3_value_int64` reads it, which is how `randomblob` reads its own.

    SQLite coerces rather than refusing, so `randomblob('12abc')` is twelve bytes
    and `randomblob(NULL)` is one. `test_randomblob_reads_its_argument_as_sqlite_does`
    is what holds this against SQLite's own function rather than against this
    reading of it.
    """
    match value:
        case int():
            return value
        case float():
            # Clamped rather than wrapped and truncated towards zero, which is
            # `doubleToInt64`. No double lies between either bound and the int64
            # beyond it, so comparing against the bounds themselves is exact.
            return math.trunc(min(max(value, _SMALLEST_INT64), _LARGEST_INT64))
        case str():
            return _atoi64(value)
        case None:
            return 0
        case _:
            # A blob. Decoded as latin-1 because only ASCII digits, signs and
            # whitespace are read, and any other byte ends the parse either way.
            return _atoi64(bytes(value).decode("latin-1"))


def _atoi64(text: str) -> int:
    """Text as `sqlite3Atoi64` reads it: a leading integer, or `0`, saturating."""
    found = _LEADING_INTEGER.match(text)
    if found is None:
        return 0
    return min(max(int(found[1]), _SMALLEST_INT64), _LARGEST_INT64)
