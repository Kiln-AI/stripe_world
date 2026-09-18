"""The net diff of an episode: the fold of its change log, per `functional_spec.md` §3.6.

The fold is defined by the spec so that any reader, in any language, computes the
same one from a saved document. This is that definition as code, written as a
consumer would write it: over the published records and their published values,
with a sort of its own rather than the framework's. The framework does not ship
it. `tests/test_fold.py` checks it against SQLite's own cumulative changeset,
which `tests/fold_oracle.py` supplies and which the definition was written from.

Test code, deliberately not importable from `seahaven`: neither the fold nor its
oracle is public API in this release.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal

from seahaven.changes import LogRecord

__all__ = ["NetChange", "fold", "in_published_order"]

type Columns = dict[str, Any]


@dataclass(frozen=True)
class NetChange:
    """One row's net difference over a whole episode."""

    world: str
    table: str
    op: Literal["insert", "update", "delete"]
    key: Columns
    before: Columns | None
    after: Columns | None


def fold(log: Iterable[LogRecord]) -> list[NetChange]:
    """The net diff of a change log: group by row, compose in order, drop what cancelled out."""
    groups: dict[tuple[Any, ...], NetChange | None] = {}
    for record in log:
        group = (record.world, record.table, tuple(record.key.items()))
        # `None` reads the same whether the group is new or folded back to
        # untouched, which is what the spec means by "starting from untouched".
        groups[group] = _compose(groups.get(group), record)
    return in_published_order(net for net in groups.values() if net is not None)


def _compose(current: NetChange | None, record: LogRecord) -> NetChange | None:
    incoming = NetChange(
        world=record.world,
        table=record.table,
        op=record.op,
        key=record.key,
        before=record.before,
        after=record.after,
    )
    if current is None:
        return incoming
    match current.op, record.op:
        case "insert", "update":
            return replace(current, after={**_columns(current.after), **_columns(record.after)})
        case "insert", "delete":
            return None
        case "update", "update":
            return _reduced(
                replace(
                    current,
                    before={**_columns(record.before), **_columns(current.before)},
                    after={**_columns(current.after), **_columns(record.after)},
                )
            )
        case "update", "delete":
            # The delete carries the row as it stood after the update; the
            # update's own `before` puts back what it had changed.
            return replace(incoming, before={**_columns(record.before), **_columns(current.before)})
        case "delete", "insert":
            return _difference(current, incoming)
        case _:  # pragma: no cover - SQLite cannot produce these pairs
            raise AssertionError(f"{current.op} cannot be followed by {record.op}")


def _reduced(net: NetChange) -> NetChange | None:
    """An update with every column that ended where it started dropped, or nothing at all."""
    before, after = _columns(net.before), _columns(net.after)
    changed = [name for name in after if before.get(name) != after[name]]
    if not changed:
        return None
    return replace(
        net,
        before={name: before[name] for name in changed},
        after={name: after[name] for name in changed},
    )


def _difference(deleted: NetChange, inserted: NetChange) -> NetChange | None:
    """A delete then an insert of the same key: an update over what differs, or nothing."""
    return _reduced(
        replace(
            deleted,
            op="update",
            before=_without(deleted.before, deleted.key),
            after=_without(inserted.after, deleted.key),
        )
    )


def _without(columns: Columns | None, key: Mapping[str, Any]) -> Columns:
    return {name: value for name, value in _columns(columns).items() if name not in key}


def _columns(columns: Columns | None) -> Columns:
    return columns or {}


def in_published_order(nets: Iterable[NetChange]) -> list[NetChange]:
    """`functional_spec.md` §3.3's order: node path, then table, then the key's values."""
    return sorted(
        nets,
        key=lambda net: (net.world, net.table, tuple(_rank(v) for v in net.key.values())),
    )


def _rank(value: Any) -> tuple[int, Any]:
    """SQLite's cross-type order over a published key value: NULL, numbers, text."""
    match value:
        case None:
            return (0, None)
        case int() | float():
            return (1, value)
        case _:
            return (2, value)
