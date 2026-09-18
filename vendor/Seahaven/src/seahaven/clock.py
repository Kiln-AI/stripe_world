"""A frozen instant, and the SQLite date and time functions that read it.

Nothing inside a world may read the wall clock, and SQL is the door that is easy
to forget: `CURRENT_TIMESTAMP` and friends are parsed as zero-argument calls into
the same function table as `datetime()`, so overriding that table covers every
path, a `DEFAULT` clause and a trigger body included.

The overrides do not reimplement SQLite's date arithmetic. They substitute the
instant for the `'now'` time value and evaluate the *original* function on a
private helper connection that has no overrides, so modifiers, formats and corner
cases stay exactly SQLite's.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Self

import apsw

from seahaven.errors import WorldBug

if TYPE_CHECKING:  # names that live in apsw's stubs, not in the extension module
    from apsw import ScalarProtocol, SQLiteValue

__all__ = ["CANONICAL_FORMAT", "Clock", "register_clock_functions"]

# The one timestamp format Seahaven writes and compares: `2024-03-05T12:00:00.123Z`.
# Spelled here as SQLite's format string and in `Clock.iso()` as Python's, which
# is a duplication `test_the_instant_is_rendered_canonically` is what holds
# together: it asserts that both doors render the same instant the same way.
CANONICAL_FORMAT = "%Y-%m-%dT%H:%M:%fZ"

# Registered on connections that have SQLITE_DBCONFIG_TRUSTED_SCHEMA off, where
# INNOCUOUS is what keeps them callable from a DEFAULT clause or a trigger.
_FUNCTION_FLAGS = apsw.SQLITE_INNOCUOUS | apsw.SQLITE_DETERMINISTIC

# SQLite accepts `now` only exactly, never padded, and neither does this: an
# override must not be more permissive than the function it replaces.
_NOW = re.compile(r"now", re.IGNORECASE)


@dataclass(frozen=True)
class _DateFunction:
    """How one override evaluates itself on the helper connection.

    `time_values` are the argument positions that hold a time value, the only
    ones `'now'` is substituted in: `strftime`'s first argument is a format
    string, and a format of `'now'` means the literal text. `omitted_at` is where
    an absent time value belongs (`datetime()` means `datetime('now')`), or
    `None` for a function that has no optional one. `prefix` is passed ahead of
    the caller's arguments.
    """

    sql_name: str
    time_values: frozenset[int]
    omitted_at: int | None
    prefix: tuple[str, ...] = ()


# `datetime` renders through `strftime` with the canonical format rather than
# through SQLite's `datetime`, whose `2024-03-05 12:00:00` sorts *below* every
# canonical timestamp of the same day (a space is below `T`) and carries no
# milliseconds. A world stores canonical text, so a clock value that did not use
# it would make `created_at > CURRENT_TIMESTAMP` nonsense. SQLite still does all
# the parsing and the arithmetic; only the rendering is ours. `date` and `time`
# are unambiguous already, and the rest return numbers or durations.
#
# The one place the canonical rendering says more than it knows is the
# `'localtime'` modifier: `datetime('now', 'localtime')` is the instant shifted
# into the host's zone, and a `Z` is then stamped on a value that is not UTC.
# Plain SQLite is already host-dependent there (it reads the process timezone,
# which no fixture pins), so the modifier has no place in a world's SQL at all;
# what the canonical rendering adds is that the value now also *claims* to be
# UTC and will sort against stored timestamps as though it were.
# `test_localtime_is_not_the_instant` pins the behaviour, and a later phase's
# lint is where a world using it should be told so.
_FUNCTIONS = {
    "date": _DateFunction("date", frozenset({0}), 0),
    "time": _DateFunction("time", frozenset({0}), 0),
    "datetime": _DateFunction("strftime", frozenset({0}), 0, (CANONICAL_FORMAT,)),
    "julianday": _DateFunction("julianday", frozenset({0}), 0),
    "unixepoch": _DateFunction("unixepoch", frozenset({0}), 0),
    "timediff": _DateFunction("timediff", frozenset({0, 1}), None),
    "strftime": _DateFunction("strftime", frozenset({1}), 1),
}

# The zero-argument keywords, as the expression each evaluates to on the instant.
_CONSTANTS = {
    "current_timestamp": f"strftime('{CANONICAL_FORMAT}', ?)",
    "current_date": "date(?)",
    "current_time": "time(?)",
}


class Clock:
    """An instant that does not move. One per instance."""

    def __init__(self, now: datetime) -> None:
        if now.tzinfo is None:
            raise WorldBug("a clock instant must be timezone-aware")
        # Truncated to the precision `iso()` renders and SQL therefore sees.
        # Microseconds a clock cannot show would make two instants that are
        # identical at every door of the framework compare unequal.
        utc = now.astimezone(UTC)
        self._now = utc.replace(microsecond=utc.microsecond // 1000 * 1000)

    @classmethod
    def from_iso(cls, text: str) -> Self:
        """Parse a timestamp, canonical or any other ISO 8601 form Python reads."""
        try:
            return cls(datetime.fromisoformat(text))
        except ValueError as error:
            raise WorldBug(f"not a timestamp: {text!r}") from error

    @classmethod
    def wall(cls) -> Self:
        """Read the wall clock.

        The one constructor that does, for a blank instance with no `now` given.
        """
        return cls(datetime.now(UTC))

    def now(self) -> datetime:
        return self._now

    def iso(self) -> str:
        """The instant as canonical text: what world code writes to the database."""
        return f"{self._now:%Y-%m-%dT%H:%M:%S}.{self._now.microsecond // 1000:03d}Z"

    def __repr__(self) -> str:
        return f"Clock({self.iso()})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Clock) and other._now == self._now

    def __hash__(self) -> int:
        return hash(self._now)


def register_clock_functions(conn: apsw.Connection, clock: Clock) -> apsw.Connection:
    """Point every SQLite date and time function on `conn` at `clock`.

    Returns the private helper connection the overrides evaluate on. The caller
    owns it and closes it with the connection it serves.
    """
    helper = apsw.Connection(":memory:")
    instant = clock.iso()

    for name, function in _FUNCTIONS.items():
        conn.create_scalar_function(
            name, _override(helper, function, instant), -1, flags=_FUNCTION_FLAGS
        )
    for name, expression in _CONSTANTS.items():
        value = helper.execute(f"SELECT {expression}", (instant,)).get
        conn.create_scalar_function(name, _constant(value), 0, flags=_FUNCTION_FLAGS)

    return helper


def _constant(value: SQLiteValue) -> ScalarProtocol:
    def evaluate(*_args: SQLiteValue) -> SQLiteValue:
        return value

    return evaluate


def _override(helper: apsw.Connection, function: _DateFunction, instant: str) -> ScalarProtocol:
    """Wrap SQLite's own function so that its time value resolves to `instant`."""

    def is_now(position: int, value: SQLiteValue) -> bool:
        return (
            position in function.time_values
            and isinstance(value, str)
            and _NOW.fullmatch(value) is not None
        )

    def evaluate(*args: SQLiteValue) -> SQLiteValue:
        resolved: list[SQLiteValue] = [
            instant if is_now(position, value) else value for position, value in enumerate(args)
        ]
        if function.omitted_at is not None and len(resolved) <= function.omitted_at:
            resolved.append(instant)
        arguments = [*function.prefix, *resolved]
        placeholders = ", ".join("?" * len(arguments))
        return helper.execute(f"SELECT {function.sql_name}({placeholders})", arguments).get

    return evaluate
