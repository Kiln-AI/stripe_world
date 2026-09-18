"""The one rule for a name that becomes a path on an artifact that travels.

A world's name, a fixture id and a composite fixture's `file` all go through
`why_not_a_name`, so the clauses are exercised here once rather than three times;
`test_world.py`, `test_fixtures.py` and `test_composite_fixtures.py` each prove
their own caller applies it.

The reason string is asserted on, not only the refusal. A name that a person has
been using and that a version of Seahaven has accepted is about to stop working,
and the reason is the whole of what they get to work from.
"""

from collections.abc import Callable

import pytest

from seahaven.names import MAX_LENGTH, RESERVED_DEVICE_NAMES, why_not_a_name

# How a device name may be spelled and still be the device.
type Casing = Callable[[str], str]


@pytest.mark.parametrize(
    "name",
    [
        "payments",
        "my-world",
        "my_world",
        "my world",
        "World2",
        "v1.2.3",
        "projecttracker",
        # What `composition._file_name` mints for a node, which is a `file` this
        # rule has to keep accepting: the root's, a child's, and a nested one's.
        "state.sqlite",
        "state.child.sqlite",
        "state.shop__payments.sqlite",
        # Near-misses of the reserved set, which are ordinary names.
        "console",
        "com10",
        "com0",
        "aux-log",
        "x" * MAX_LENGTH,
    ],
)
def test_the_names_people_use_are_names(name: str) -> None:
    assert why_not_a_name(name) is None


def test_an_empty_name_is_refused() -> None:
    assert why_not_a_name("") == "it is empty"


@pytest.mark.parametrize("name", ["a/b", "a\\b", "a/", "/abs", "C:x", "c:", "."])
def test_a_name_that_is_a_path_on_any_platform_is_refused(name: str) -> None:
    """Windows' flavour, because the machine that reads a name is not the one that wrote it.

    `C:x` is the case that motivated the rule: a bare name to `PurePosixPath`, a
    drive-relative path to `PureWindowsPath`, and therefore a directory that walks
    out of its parent on the machine the fixture is copied to. Every one of these is
    refused further down as well -- the charset takes the separators and the colon,
    the dot clause takes the bare `.` -- so what this clause is for is the message,
    which should read as a path rather than as a stray character.
    """
    assert why_not_a_name(name) is not None
    assert "one path segment" in str(why_not_a_name(name))


@pytest.mark.parametrize(
    ("name", "offender"),
    [
        ("café", "é"),
        ("日本語", "日"),
        ("a\x00b", "\x00"),
        ("a!b", "!"),
        ("a$b", "$"),
        ("a,b", ","),
        ("a;b", ";"),
    ],
)
def test_a_character_outside_the_charset_is_refused_and_named(name: str, offender: str) -> None:
    """The accepted consequence: a non-ASCII name is no longer legal.

    The offending character is quoted, because the report this rule generates is
    "my world name stopped working" and a name is not read character by character
    by the person who typed it.
    """
    reason = why_not_a_name(name)
    assert reason is not None
    assert repr(offender) in reason
    assert "transliterated" in reason


@pytest.mark.parametrize("name", [" leading", "leading and trailing "])
def test_a_leading_or_trailing_space_is_refused(name: str) -> None:
    assert why_not_a_name(name) == (
        "it starts or ends with a space, and Windows silently strips a trailing one"
    )


@pytest.mark.parametrize("name", [".hidden", "..", "trailing.", "..."])
def test_a_leading_or_trailing_dot_is_refused(name: str) -> None:
    reason = why_not_a_name(name)
    assert reason is not None
    assert "starts or ends with a dot" in reason


@pytest.mark.parametrize("device", sorted(RESERVED_DEVICE_NAMES))
@pytest.mark.parametrize("spelling", [str.lower, str.upper, str.title])
def test_every_windows_device_name_is_refused(device: str, spelling: Casing) -> None:
    """The whole set, in three casings: `com2`-`com8` are as reserved as `com1`."""
    reason = why_not_a_name(spelling(device))
    assert reason is not None
    assert "reserves for a device" in reason


def test_the_reserved_set_is_the_twenty_two_names_windows_reserves() -> None:
    """Spelled out, because the sweep above parametrizes over the set itself.

    A slip in the comprehension's bounds would shrink the set and the sweep with
    it, and every remaining case would still pass.
    """
    windows_reserves = {
        "con",
        "prn",
        "aux",
        "nul",
        "com1",
        "com2",
        "com3",
        "com4",
        "com5",
        "com6",
        "com7",
        "com8",
        "com9",
        "lpt1",
        "lpt2",
        "lpt3",
        "lpt4",
        "lpt5",
        "lpt6",
        "lpt7",
        "lpt8",
        "lpt9",
    }

    assert windows_reserves == RESERVED_DEVICE_NAMES


@pytest.mark.parametrize("name", ["con.txt", "aux.sqlite", "COM1.tar.gz", "nul .txt"])
def test_a_windows_device_name_with_an_extension_is_refused(name: str) -> None:
    """Reserved with any suffix: `con.txt` opens the console, not a file.

    A trailing space in front of the dot is ignored by Windows too, so it cannot
    be the way past this.
    """
    reason = why_not_a_name(name)
    assert reason is not None
    assert "reserves for a device" in reason


def test_a_name_longer_than_the_limit_is_refused() -> None:
    assert why_not_a_name("x" * (MAX_LENGTH + 1)) == (
        f"it is {MAX_LENGTH + 1} characters, and the limit is {MAX_LENGTH}"
    )


def test_a_generated_name_is_checked_without_the_limit() -> None:
    """`max_length=None` is the one divergence: a name Seahaven mints, not one it accepts.

    A composite fixture's `file` is built from the node's path, so a limit applied
    where the sidecar is *read* and not where it is written would be `load`
    refusing a fixture `freeze` had just produced.
    """
    long_file = f"state.{'deep__' * 30}node.sqlite"

    assert len(long_file) > MAX_LENGTH
    assert why_not_a_name(long_file, max_length=None) is None
    assert why_not_a_name(long_file) is not None
    # Everything else still applies to it.
    assert why_not_a_name("../elsewhere.sqlite", max_length=None) is not None
