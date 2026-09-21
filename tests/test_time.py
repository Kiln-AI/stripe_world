"""`_time`: the one ISO ↔ Unix-second conversion, and the canonical pattern."""

import re

import pytest

from seahaven_stripe_world._time import ISO_PATTERN, from_unix, to_unix


def test_round_trip_preserves_the_second() -> None:
    for seconds in (0, 1_756_728_000, 1_700_000_000, 951_782_400, -1):
        assert to_unix(from_unix(seconds)) == seconds


def test_a_pre_epoch_fractional_second_floors_toward_the_past() -> None:
    """`int()` truncates toward zero and would round a pre-epoch instant *up*;
    the millisecond is dropped toward the past, like every other one."""
    assert to_unix("1969-12-31T23:59:59.123Z") == -1
    assert from_unix(-1) == "1969-12-31T23:59:59.000Z"


def test_known_instants_convert_exactly() -> None:
    # The fixture instant, and one with a nonzero millisecond part that the
    # Unix-second form must truncate away rather than round up.
    assert from_unix(1_788_271_200) == "2026-09-01T14:00:00.000Z"
    assert to_unix("2026-09-01T14:00:00.000Z") == 1_788_271_200
    assert from_unix(1_756_735_200) == "2025-09-01T14:00:00.000Z"
    assert to_unix("2025-09-01T14:00:00.123Z") == 1_756_735_200  # .123 dropped


def test_the_pattern_accepts_only_the_canonical_form() -> None:
    canonical = "2026-06-01T09:00:00.000Z"
    assert re.match(ISO_PATTERN, canonical)
    for not_canonical in (
        "2026-06-01T09:00:00Z",  # no milliseconds
        "2026-06-01T09:00:00.00Z",  # two digits
        "2026-06-01 09:00:00.000Z",  # space, not T
        "2026-06-01T09:00:00.000+00:00",  # offset, not Z
        "2026-06-01T09:00:00.000z",  # lowercase z
    ):
        assert not re.match(ISO_PATTERN, not_canonical), not_canonical


def test_to_unix_refuses_what_is_not_canonical() -> None:
    for bad in ("2026-06-01T09:00:00Z", "2026-06-01T09:00:00.000+00:00", "yesterday", ""):
        with pytest.raises(ValueError):
            to_unix(bad)


def test_to_unix_refuses_impossible_dates() -> None:
    """A month of 13 matches the shape but not the calendar; a silent rescue
    would hide a bug in whatever wrote the column."""
    with pytest.raises(ValueError):
        to_unix("2026-13-01T09:00:00.000Z")


def test_the_epoch_is_representable() -> None:
    assert from_unix(0) == "1970-01-01T00:00:00.000Z"
    assert to_unix("1970-01-01T00:00:00.000Z") == 0
