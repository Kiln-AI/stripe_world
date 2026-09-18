"""SQLite's own net diff of a whole episode: the oracle `functional_spec.md` §3.6 was defined from.

The fold is a definition, and a definition needs something outside itself to be
checked against. That something is the session extension asked the question the
fold asks: one session per node, open for the whole episode, whose changeset is
the net difference between where that node started and where it ended.

The framework records per call and keeps no such session, so the test opens its
own, on every node's own connection and attached to the tables that node tracks
-- which it reads off `instance._runtime`, an internal, because the oracle has to
record exactly what the log records. Opened after the instance is made, so the
startup hooks are starting state here as they are in the log.

Test code, deliberately not importable from `seahaven`.
"""

from collections.abc import Iterator
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass

import apsw

from seahaven.changes import open_session, render_log
from seahaven.instances import Instance, NodeRuntime
from tests.fold_support import NetChange, in_published_order

__all__ = ["Oracle", "recording"]


@dataclass(frozen=True)
class Oracle:
    """A session per node recording one instance, and the net diff they have to say."""

    instance: Instance
    sessions: list[tuple[NodeRuntime, apsw.Session]]

    def net_diff(self) -> list[NetChange]:
        """Every row the episode changed, once each, as the fold's own shape.

        Rendered with `render_log`, which is the framework's renderer and not
        part of what is under test: what is under test is the fold's arithmetic
        against SQLite's, and both sides have to be in one vocabulary to be
        compared at all. `i=None` because a cumulative changeset belongs to no
        call, and `NetChange` has no ordinal to carry it into. Sorted with the
        fold's own order, so a comparison against `fold(...)` is order-blind on
        the framework's side and order-sensitive on the consumer's.
        """
        nets = []
        for runtime, session in self.sessions:
            changeset = session.changeset()
            if not changeset:
                continue
            for record in render_log(
                changeset, runtime.db.conn, {}, i=None, world=runtime.node.path
            ):
                nets.append(
                    NetChange(
                        world=record.world,
                        table=record.table,
                        op=record.op,
                        key=record.key,
                        before=record.before,
                        after=record.after,
                    )
                )
        return in_published_order(nets)


@contextmanager
def recording(instance: Instance) -> Iterator[Oracle]:
    """Record every write to every node of `instance` for the length of the block."""
    with ExitStack() as stack:
        sessions = [
            (runtime, stack.enter_context(closing(open_session(runtime.db.conn, runtime.tracked))))
            for runtime in instance._runtime.values()
        ]
        yield Oracle(instance=instance, sessions=sessions)
