"""One rule for the names that become paths on artifacts that travel.

Three names reach a filesystem and then outlive the process that chose them: a
world's name, which is a directory under the working root; a fixture id, which is
a directory that is published, copied and read on another machine; and a
composite fixture's `file`, the name of one node's state file inside the fixture
directory. All three are identifiers rather than display strings, and the rule
below is written once here rather than three times, because a name minted under
the loosest of three rules and read under the strictest is an artifact that does
not open where it is taken.

The rule is narrower than any one filesystem's, deliberately: it is what Windows,
macOS and Linux all carry unchanged and hand back byte for byte. That excludes a
non-ASCII name such as `café` or `日本語`, which is the accepted cost --
`why_not_a_name` says so in the reason it returns, because "my world name stopped
working" is the report that arrives otherwise.
"""

import re
from pathlib import PureWindowsPath

__all__ = [
    "CHARACTER_RULE",
    "MAX_LENGTH",
    "NAME_RULE",
    "RESERVED_DEVICE_NAMES",
    "why_not_a_name",
]

# The same limit `composition.TOOL_NAME` puts on a tool name, on purpose: one
# number for the identifiers a person types into a world.
MAX_LENGTH = 128

_OUTSIDE_THE_CHARSET = re.compile(r"[^A-Za-z0-9 ._-]")

# Reserved by Windows in any case and *with any extension* -- `con.txt` opens the
# console rather than a file -- so the comparison is against the part before the
# first dot. The console's other names (`CONIN$`, `CONOUT$`) carry a `$`, which
# the charset already refuses, so this set is the whole of what can be spelled.
RESERVED_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{digit}" for digit in range(1, 10)}
    | {f"lpt{digit}" for digit in range(1, 10)}
)

# The rule as a sentence, for the messages that have to state it. The reasons
# below name the clause that was broken and leave the enumeration to this, so that
# a refusal says each thing once. `CHARACTER_RULE` is the part that holds for a
# name Seahaven mints rather than accepts, where there is no length limit.
CHARACTER_RULE = (
    "made of letters, digits, space, '.', '-' and '_', not starting or ending with a space or a "
    "dot, and not a name Windows reserves for a device"
)
NAME_RULE = f"1 to {MAX_LENGTH} characters, {CHARACTER_RULE}"


def why_not_a_name(value: str, *, max_length: int | None = MAX_LENGTH) -> str | None:
    """Why `value` cannot be one of these names, or `None` if it can be.

    A reason rather than an exception, because the callers raise different types
    -- a `WorldBug` for a name a person chose, pydantic's `ValueError` for one
    read out of a sidecar -- and each names its own subject.

    `max_length=None` is for a name Seahaven mints rather than accepts. The limit
    bounds what a person types; applied to a generated name it would be a bound on
    something else entirely (for `file`, on how deep a composition may nest), and
    one that `freeze` does not apply, so `load` would refuse a fixture Seahaven
    itself wrote.
    """
    if not value:
        return "it is empty"
    if max_length is not None and len(value) > max_length:
        return f"it is {len(value)} characters, and the limit is {max_length}"
    # Windows' flavour, not the running platform's and not both: it reads both
    # separators and a drive letter, so it refuses every spelling `PurePosixPath`
    # would and the drive-relative `C:x` besides. Every name this refuses is
    # refused below as well -- the charset takes the separators and the colon, the
    # dot clause takes the bare `.` -- so this clause is here for the message, and
    # runs first so that `a/b` and `C:x` read as paths rather than as a stray
    # character.
    if value != PureWindowsPath(value).name:
        return (
            "it is not one path segment: a separator or a Windows drive letter makes it a path, "
            "and '.' is the directory itself"
        )
    outside = _OUTSIDE_THE_CHARSET.search(value)
    if outside is not None:
        return (
            f"it contains {outside.group()!r}, which a name may not; an accented or non-Latin "
            f"name has to be transliterated, because the name becomes a directory on every "
            f"machine the artifact reaches"
        )
    if value.startswith(" ") or value.endswith(" "):
        return "it starts or ends with a space, and Windows silently strips a trailing one"
    if value.startswith(".") or value.endswith("."):
        return (
            "it starts or ends with a dot: a leading dot hides the directory and is how Seahaven "
            "marks its own, and Windows silently strips a trailing one"
        )
    # Windows ignores trailing spaces in a device name too, so `'con .txt'` is the
    # console as much as `'con.txt'` is.
    device = value.split(".", 1)[0].rstrip(" ").lower()
    if device in RESERVED_DEVICE_NAMES:
        return (
            f"{device!r} is a name Windows reserves for a device, in any case and with any suffix"
        )
    return None
