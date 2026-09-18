"""The state document: the framework's envelope, and the format that fills `state`.

`inst.state()` answers one JSON-able dict. Everything in it above `state` is the
**envelope** -- `format`, the Seahaven and world versions, the composition, the
fixture, the episode, the seed, the clock, the startup keywords and the call
count -- and it is the framework's, identical under every format. `state` is the
**formatter's** output, and `format` names its shape and nothing else.

That split is the compatibility contract (`functional_spec.md` §10) made
structural: a format can neither omit provenance nor misspell it, because it
never writes any. A formatter is `(world, instance | None) -> dict`, and the
`None` is the environment before its first `reset`, where a world is connected
and no instance exists yet.

Three formats are built in. `seahaven.state/1` is the whole change log, for the
caller that reads `state()` once at the end of an episode.
`seahaven.state+last_step/1` is the last call's records alone, for the caller
that reads it after every step and would otherwise save the whole log each time.
`seahaven.state+calls/1` adds the call log, for the consumer that cannot line
its own trace up with the document.

`path` is the join key across a document: `composition[p]` describes a node,
`fixture.nodes[p]` the file it started from, and a log record's `world` names
one.
"""

import copy
import re
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import seahaven
from seahaven.errors import WorldBug

if TYPE_CHECKING:  # both modules import this one; the annotations are all that is needed here
    from seahaven.instances import Instance
    from seahaven.world import World

__all__ = [
    "BUILTIN_FORMATS",
    "BUILTIN_PREFIX",
    "SEAHAVEN_STATE_CALLS_V1",
    "SEAHAVEN_STATE_LAST_STEP_V1",
    "SEAHAVEN_STATE_V1",
    "Formatter",
    "check_format_name",
    "document",
    "envelope",
]

# The value of `state`, and nothing else: the framework writes the envelope.
type Formatter = Callable[["World", "Instance | None"], dict[str, Any]]

SEAHAVEN_STATE_V1 = "seahaven.state/1"
SEAHAVEN_STATE_LAST_STEP_V1 = "seahaven.state+last_step/1"
SEAHAVEN_STATE_CALLS_V1 = "seahaven.state+calls/1"

# Reserved for the framework's own formats, so that a world cannot register a
# name a later Seahaven release might publish.
BUILTIN_PREFIX = "seahaven."

# `<family>/<major>`: the family names the shape and the major is what a reader
# keys on, which is why it is a positive integer and not a free string. Its own
# rule and not `names.why_not_a_name`, because a format name is never a path and
# `/` is the one character it must contain.
_NAME = re.compile(r"^[A-Za-z0-9_.+-]+/[1-9][0-9]*$")


def check_format_name(name: str) -> None:
    """Refuse anything that is not `<family>/<major>`."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise WorldBug(
            f"not a state format name: {name!r}; a format is spelled <family>/<major>, such as "
            f"{SEAHAVEN_STATE_V1!r}: a family of letters, digits, '_', '.', '+' or '-', then '/', "
            f"then a positive integer"
        )


def envelope(world: World, instance: Instance | None, format: str) -> dict[str, Any]:
    """The provenance every document carries, in `functional_spec.md` §3.1 order.

    `instance` is `None` before the first `reset` over OpenEnv: the world is
    known and the episode is not, so everything the instance would have answered
    is `null` and `call_count` is 0. A blank instance is still told apart from no
    instance, because a blank instance has a `now` and a `composition`.
    """
    return {
        "format": format,
        # Informational: a reader keys on `format`, never on this.
        "seahaven_version": seahaven.__version__,
        "world": {"name": world.name, "version": world.version},
        "composition": _composition(instance) if instance is not None else None,
        "fixture": (
            {"id": instance.fixture, "nodes": _fixture_nodes(instance)}
            if instance is not None and instance.fixture is not None
            else None
        ),
        "episode_id": instance.episode_id if instance is not None else None,
        "seed": instance.caller_seed if instance is not None else None,
        "now": instance.clock.iso() if instance is not None else None,
        # Copied, not handed out: a caller that edits the document it was given
        # must not edit the instance, and every later read with it.
        "startup": copy.deepcopy(instance.startup) if instance is not None else None,
        "call_count": instance.call_count if instance is not None else 0,
    }


def document(
    world: World, instance: Instance | None, format: str, formatter: Formatter
) -> dict[str, Any]:
    """The whole answer to `state()`: the envelope, and `state` from the formatter.

    The one place the two are joined, so the envelope is written the same way for
    the environment before a `reset` as for an instance.
    """
    body = formatter(world, instance)
    if not isinstance(body, dict):
        raise WorldBug(
            f"state format {format!r} returned {type(body).__name__}; a formatter returns the "
            f"value of 'state', which is a dict, and the framework writes the envelope around it"
        )
    return {**envelope(world, instance, format), "state": body}


def state_v1(world: World, instance: Instance | None) -> dict[str, Any]:
    """`seahaven.state/1`: the whole change log, every node's records in one list."""
    records = instance.change_log() if instance is not None else []
    return {"db": {"log": [record.to_dict() for record in records]}}


def state_last_step_v1(world: World, instance: Instance | None) -> dict[str, Any]:
    """`seahaven.state+last_step/1`: the records of the most recent call, and no others.

    Scoped by the call counter rather than by when `state()` was last read, so
    two reads between calls answer the same document and the per-step documents
    of an episode concatenate into the whole log. A record with no ordinal --
    an `inst.bulk()` write -- belongs to no call and is never in it.
    """
    if instance is None:
        return {"db": {"log": []}}
    last = instance.call_count - 1
    return {
        "db": {"log": [record.to_dict() for record in instance.change_log() if record.i == last]}
    }


def state_calls_v1(world: World, instance: Instance | None) -> dict[str, Any]:
    """`seahaven.state+calls/1`: `seahaven.state/1` plus the call log.

    One entry per dispatched call in dispatch order, so a log record's `i` is its
    index in `calls`. The call log is kept for every instance whether or not this
    format is ever read, so this answers on any of them.
    """
    body = state_v1(world, instance)
    body["calls"] = (
        [record.to_dict() for record in instance.call_log()] if instance is not None else []
    )
    return body


def _composition(instance: Instance) -> dict[str, dict[str, Any]]:
    """Every node of the instance, keyed by canonical path, root first.

    `NodeReport`'s fields less `path`, which is the key. `Instance.composition()`
    answers root first and a dict keeps insertion order, so that is the order the
    keys are written in; a reader is told not to rely on it.
    """
    return {
        report.path: {
            "world": report.world,
            "world_version": report.world_version,
            "scope": report.scope,
            "aliases": list(report.aliases),
            "schema_hash": report.schema_hash,
            "frozen_world_version": report.frozen_world_version,
        }
        for report in instance.composition()
    }


def _fixture_nodes(instance: Instance) -> dict[str, dict[str, str]]:
    """Each node's starting file, keyed by path: the sidecar's hashes, root included."""
    files = instance.fixture_files or {}
    return {path: {"file_sha256": file_sha256} for path, file_sha256 in files.items()}


BUILTIN_FORMATS: Mapping[str, Formatter] = MappingProxyType(
    {
        SEAHAVEN_STATE_V1: state_v1,
        SEAHAVEN_STATE_LAST_STEP_V1: state_last_step_v1,
        SEAHAVEN_STATE_CALLS_V1: state_calls_v1,
    }
)
