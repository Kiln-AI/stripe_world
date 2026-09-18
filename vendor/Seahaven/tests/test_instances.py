"""Instances: creation, the lock, the gate, the working directory, and destroy.

Everything here runs against a real instance of a real world, made the way a
caller makes one, because that is the only place the pieces meet.
"""

import atexit
import dataclasses
import errno
import logging
import os
import stat as stat_module
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any

import apsw
import pytest
from pydantic import Field

from seahaven import control, instances
from seahaven.call import Call, Handler
from seahaven.ctx import Ctx
from seahaven.db import Db
from seahaven.errors import ArgumentError, DbError, UnknownTool, WorldBug
from seahaven.fixtures import STATE_NAME
from seahaven.ids import instance_seed
from seahaven.instances import Instance, default_concurrency, set_concurrency
from seahaven.tool import Tool
from seahaven.world import World
from tests.conftest import INSTANT_ISO, NOTES_SCHEMA, WAIT, Boom, Caller, build_world


def add(instance: Instance, id: str, body: str = "a body") -> None:
    instance.call("execute", sql=f"INSERT INTO notes VALUES ('{id}', '{body}', 0)")


# A world whose schema seeds a reference row. That DML is the world's, it runs
# while the blank file is built, and `randomblob` and `CURRENT_TIMESTAMP` in it
# read the host unless the build carries the instance's seed and clock.
SEEDING_SCHEMA = """
CREATE TABLE plans (
    id TEXT PRIMARY KEY,
    token BLOB NOT NULL,
    made_at TEXT NOT NULL
) STRICT;
INSERT INTO plans (id, token, made_at) VALUES ('free', randomblob(8), CURRENT_TIMESTAMP);
"""


def seeded_plan(instance: Instance) -> dict[str, Any]:
    """The row a `SEEDING_SCHEMA` world's schema wrote for itself."""
    row = instance.inspect().one("SELECT token, made_at FROM plans")
    assert row is not None
    return row


def frozen_fixture(world: World, fixture_id: str = "start", notes: int = 1) -> str:
    """A world with one fixture in it, made the only way fixtures are made."""
    with world.instance(None, now=INSTANT_ISO) as instance:
        for number in range(notes):
            add(instance, f"n{number}")
        instance.freeze(fixture_id, "Some notes.")
    return fixture_id


def control_tool(fn: Any, name: str | None = None) -> Tool:
    """A framework control tool, built the way `control.py` builds its own two.

    The real builder, so these tests hold the dispatch path to the shape the
    framework's own control tools have: the instance is the first parameter and
    is bound away before the signature becomes the argument model, and `dispatch`
    passes the live instance back to the function.
    """
    tool = control._control_tool(fn)
    return tool if name is None else dataclasses.replace(tool, name=name)


def contents(directory: Path) -> list[str]:
    """What a directory holds, for a directory that may not be there at all."""
    return sorted(child.name for child in directory.iterdir()) if directory.is_dir() else []


def instance_dirs(world: World) -> list[Path]:
    work_dir = world.work_dir
    assert work_dir is not None
    return [path for path in work_dir.iterdir()] if work_dir.is_dir() else []


def test_a_blank_instance_starts_at_the_wall_clock(world: World) -> None:
    before = datetime.now(UTC)

    with world.instance(None) as instance:
        started = instance.clock.now()

    assert before.replace(microsecond=before.microsecond // 1000 * 1000) <= started
    assert started <= datetime.now(UTC)
    # Truncated to the precision the canonical format renders.
    assert started.microsecond % 1000 == 0


@pytest.mark.parametrize("given", [INSTANT_ISO, datetime(2024, 3, 5, 12, 0, 0, 123000, tzinfo=UTC)])
def test_an_explicit_now_is_the_instances_clock(world: World, given: str | datetime) -> None:
    with world.instance(None, now=given) as instance:
        assert instance.clock.iso() == INSTANT_ISO
        assert instance.call("now") == {"python": INSTANT_ISO, "sql": INSTANT_ISO}


def test_a_row_the_schema_seeds_is_stamped_with_the_instances_clock(tmp_path: Path) -> None:
    """The end of the thread: `now=` reaches the DML a schema file runs on itself.

    The clock is derived before the blank file is built rather than after, so a
    world that seeds reference rows dates them at the instant its caller named
    instead of at whatever the host's clock said while the build ran.
    """
    world = build_world(tmp_path, SEEDING_SCHEMA)

    with world.instance(None, now=INSTANT_ISO) as instance:
        assert seeded_plan(instance)["made_at"] == INSTANT_ISO


def test_two_blank_instances_of_a_seeding_world_seed_the_same_row(tmp_path: Path) -> None:
    world = build_world(tmp_path, SEEDING_SCHEMA)

    def seeded() -> dict[str, Any]:
        with world.instance(None, now=INSTANT_ISO, seed=11) as instance:
            return seeded_plan(instance)

    first = seeded()
    assert seeded() == first
    # And the seed is what it came from, not a constant the build wrote.
    with world.instance(None, now=INSTANT_ISO, seed=12) as other:
        assert seeded_plan(other)["token"] != first["token"]


def test_a_seeded_row_does_not_hold_the_bytes_the_world_draws_first(tmp_path: Path) -> None:
    """The build is a door of its own. See `ids.BUILD_STREAM`."""
    world = build_world(tmp_path, SEEDING_SCHEMA)

    with world.instance(None, now=INSTANT_ISO, seed=11) as instance:
        # Hex, because a tool result may not carry bytes.
        drawn = instance.call("rows", sql="SELECT hex(randomblob(8)) AS drawn")

        assert drawn != [{"drawn": seeded_plan(instance)["token"].hex().upper()}]


def test_a_fixture_carries_the_rows_its_schema_seeded_at_the_pinned_instant(
    tmp_path: Path,
) -> None:
    """Freeze and create back: the seeded row is data in the frozen file by then.

    A blank instance built on the wall clock would date the row at whatever the
    host said when the fixture was minted, and every instance made from that
    fixture would replay that accident for good.
    """
    world = build_world(tmp_path, SEEDING_SCHEMA)
    with world.instance(None, now=INSTANT_ISO, seed=11) as origin:
        frozen = seeded_plan(origin)
        origin.freeze("seeded", "The plans the schema writes for itself.")

    assert frozen["made_at"] == INSTANT_ISO
    with world.instance("seeded") as replayed:
        assert seeded_plan(replayed) == frozen


def test_now_with_a_fixture_is_refused(world: World) -> None:
    """The fixture carries the clock; `reset(now=)` for a fixture is post-V1."""
    fixture = frozen_fixture(world)

    with pytest.raises(WorldBug, match="blank instances only"):
        world.instance(fixture, now="2030-01-01T00:00:00.000Z")

    assert instance_dirs(world) == []


def test_an_instance_from_a_fixture_is_a_writable_copy_at_the_fixtures_clock(world: World) -> None:
    fixture = frozen_fixture(world)

    with world.instance(fixture) as instance:
        assert instance.fixture == fixture
        assert instance.clock.iso() == INSTANT_ISO
        # The fixture's file is read-only; the copy is not, or nothing could run.
        # Asserted on the mode rather than only by writing: root may write to a
        # read-only file, and CI must not be the only place this is true.
        assert instance.state_path.stat().st_mode & 0o200
        add(instance, "written")
        assert instance.inspect().rows("SELECT count(*) AS c FROM notes") == [{"c": 2}]

    assert (world.fixtures_dir / fixture / STATE_NAME).stat().st_mode & 0o222 == 0


@pytest.mark.parametrize("bad", ["", ".", "..", "sub/start", "/start", ".hidden"])
def test_a_fixture_id_that_is_not_a_directory_name_is_refused(world: World, bad: str) -> None:
    """The id rule is applied before the filesystem is touched: an id cannot become a path."""
    frozen_fixture(world)

    with pytest.raises(WorldBug, match="not a fixture id"):
        world.instance(bad)

    assert instance_dirs(world) == []


def test_an_id_that_climbs_out_of_the_fixtures_directory_is_refused(world: World) -> None:
    """Even when the path it names really is a fixture."""
    frozen_fixture(world)
    elsewhere = world.fixtures_dir.parent / "elsewhere"
    elsewhere.mkdir()

    with pytest.raises(WorldBug, match="not a fixture id"):
        world.instance("../elsewhere")


def test_an_unknown_fixture_names_the_directory_and_the_way_to_change_it(world: World) -> None:
    with pytest.raises(WorldBug) as raised:
        world.instance("nothing-like-it")

    assert "World(fixtures_dir=...)" in str(raised.value)
    assert str(world.fixtures_dir) in str(raised.value)


def test_a_fixture_frozen_from_another_schema_is_refused(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    fixture = frozen_fixture(world)
    changed = build_world(
        tmp_path, NOTES_SCHEMA.replace("body TEXT NOT NULL", "body TEXT"), name="changed"
    )

    with pytest.raises(WorldBug, match="regenerate it"):
        changed.instance(fixture)

    assert instance_dirs(changed) == []


def test_a_modified_fixture_is_refused_before_it_is_copied(world: World) -> None:
    fixture = frozen_fixture(world)
    state = world.fixtures_dir / fixture / STATE_NAME
    state.chmod(0o644)
    state.write_bytes(state.read_bytes() + b"tampered")

    with pytest.raises(WorldBug, match="fork it"):
        world.instance(fixture)

    assert instance_dirs(world) == []


def test_the_same_seed_gives_the_same_ids_in_two_instances(world: World) -> None:
    fixture = frozen_fixture(world)

    with world.instance(fixture, seed=7) as first, world.instance(fixture, seed=7) as second:
        assert first.call("mint") == second.call("mint")
        assert first.id != second.id  # identity is not data


def test_two_fixtures_give_two_streams_for_one_seed(world: World) -> None:
    """The fixture id is half the derivation, so one seed is not one stream."""
    frozen_fixture(world, "one")
    frozen_fixture(world, "two")

    with world.instance("one", seed=7) as first, world.instance("two", seed=7) as second:
        assert first.call("mint") != second.call("mint")


@pytest.mark.parametrize("seed", [None, 0, 7])
def test_the_instance_seed_is_the_derived_one(world: World, seed: int | None) -> None:
    with world.instance(None, seed=seed) as instance:
        assert instance.seed == instance_seed(world.name, seed)
        assert instance.ctx.instance.seed == instance.seed


def test_an_unknown_startup_argument_is_refused_before_anything_is_copied(tmp_path: Path) -> None:
    world = build_world(tmp_path)

    @world.instance_startup
    def seed(ctx: Ctx, *, owner: str = "nobody") -> None:
        ctx.state["owner"] = owner

    with pytest.raises(WorldBug, match=r"unknown reset argument\(s\): \['onwer'\]"):
        world.instance(None, onwer="alice")

    assert instance_dirs(world) == []


def test_a_hook_taking_kwargs_accepts_anything(tmp_path: Path) -> None:
    world = build_world(tmp_path)

    @world.instance_startup
    def seed(ctx: Ctx, **kwargs: Any) -> None:
        ctx.state.update(kwargs)

    with world.instance(None, anything="at all") as instance:
        assert instance.ctx.state == {"anything": "at all"}


def test_hooks_run_in_order_each_with_only_what_it_accepts(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    seen: list[tuple[str, dict[str, Any]]] = []

    @world.instance_startup
    def first(ctx: Ctx, *, owner: str) -> None:
        seen.append(("first", {"owner": owner}))

    @world.instance_startup
    def second(ctx: Ctx, *, plan: str = "free") -> None:
        seen.append(("second", {"plan": plan}))

    with world.instance(None, owner="alice"):
        pass

    assert seen == [("first", {"owner": "alice"}), ("second", {"plan": "free"})]


def test_hooks_run_inside_one_transaction(tmp_path: Path) -> None:
    """Atomic together: a later hook's seed rows commit with an earlier hook's, or not at all."""
    world = build_world(tmp_path)
    in_transaction: list[bool] = []
    committed_early: list[Any] = []

    @world.instance_startup
    def first(ctx: Ctx) -> None:
        ctx.db.execute("INSERT INTO notes VALUES ('seeded', 'one', 0)")
        in_transaction.append(ctx.db.in_transaction)

    @world.instance_startup
    def second(ctx: Ctx) -> None:
        in_transaction.append(ctx.db.in_transaction)
        # Inside the same transaction, so this hook reads what the first wrote.
        assert ctx.db.rows("SELECT id FROM notes") == [{"id": "seeded"}]
        # And nothing outside the connection can: a transaction per hook would
        # have committed the row before this one started.
        other = apsw.Connection(ctx.db.conn.filename, flags=apsw.SQLITE_OPEN_READONLY)
        try:
            committed_early.extend(other.execute("SELECT id FROM notes").fetchall())
        finally:
            other.close()

    with world.instance(None) as instance:
        assert in_transaction == [True, True]
        assert committed_early == []
        assert not instance.db.in_transaction
        assert instance.inspect().rows("SELECT id FROM notes") == [{"id": "seeded"}]


def test_a_hook_that_raises_aborts_creation_and_leaves_nothing(tmp_path: Path) -> None:
    world = build_world(tmp_path)

    @world.instance_startup
    def seed(ctx: Ctx) -> None:
        ctx.db.execute("INSERT INTO notes VALUES ('seeded', 'one', 0)")
        raise RuntimeError("the hook could not")

    with pytest.raises(RuntimeError, match="the hook could not"):
        world.instance(None)

    assert instance_dirs(world) == []


def test_what_a_hook_puts_in_state_is_there_for_every_call(tmp_path: Path) -> None:
    world = build_world(tmp_path)

    @world.instance_startup
    def seed(ctx: Ctx, *, owner: str = "nobody") -> None:
        ctx.state["owner"] = owner

    @world.tool
    def whose(ctx: Ctx) -> dict[str, str]:
        return {"owner": ctx.state["owner"]}

    with world.instance(None, owner="alice") as instance:
        assert instance.call("whose") == {"owner": "alice"}
        assert instance.call("whose") == {"owner": "alice"}


def test_tools_lists_the_worlds_tools_with_their_schemas(instance: Instance) -> None:
    listed = instance.tools()

    assert {tool["name"] for tool in listed} == {
        name for name, tool in instance.world.tools.items() if not tool.control
    }
    assert sorted(listed[0]) == ["description", "input_schema", "name"]


def test_tools_never_lists_a_control_tool(world: World) -> None:
    def peek(live: Instance, ctx: Ctx) -> dict[str, int]:
        """Look inside."""
        return {"seen": 1}

    world.tool(control_tool(peek))

    with world.instance(None) as instance:
        assert "peek" not in {tool["name"] for tool in instance.tools()}
        assert instance.call("peek") == {"seen": 1}


def test_a_call_runs_through_the_middleware_chain(world: World) -> None:
    seen: list[str] = []

    @world.middleware
    def record(ctx: Ctx, call: Call, next_: Handler) -> Any:
        seen.append(call.name)
        return next_(ctx, call)

    with world.instance(None) as instance:
        instance.call("now")

    assert seen == ["now"]


def test_a_tool_registered_after_the_instance_is_callable_on_it(world: World) -> None:
    """The registry is read at call time, not held from creation."""
    with world.instance(None) as instance:

        @world.tool
        def late(ctx: Ctx) -> dict[str, bool]:
            """Registered after the instance existed."""
            return {"late": True}

        assert instance.call("late") == {"late": True}


def test_an_unknown_tool_name_is_a_tool_error(instance: Instance) -> None:
    with pytest.raises(UnknownTool) as raised:
        instance.call("no_such_tool")

    assert raised.value.code == "unknown_tool"


def test_a_tool_error_reaches_the_caller(instance: Instance) -> None:
    with pytest.raises(Boom) as raised:
        instance.call("write_then_fail", sql="INSERT INTO notes VALUES ('n1', 'b', 0)")

    assert raised.value.code == "boom"


def test_every_call_logs_one_line_with_the_outcome(
    instance: Instance, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="seahaven.instances")

    instance.call("now")
    with pytest.raises(Boom):
        instance.call("write_then_fail", sql="INSERT INTO notes VALUES ('n1', 'b', 0)")
    with pytest.raises(ValueError):
        instance.call("crash")
    with pytest.raises(UnknownTool):
        instance.call("nope")

    lines = [
        record.getMessage() for record in caplog.records if record.name == "seahaven.instances"
    ]
    assert len(lines) == 4
    assert all(
        instance.id in line and instance.world.name in line and " ms" in line for line in lines
    )
    assert "call now on instance" in lines[0] and ": ok in" in lines[0]
    assert ": boom in" in lines[1]
    assert ": ValueError in" in lines[2]
    assert ": unknown_tool in" in lines[3]


def test_nothing_works_on_a_destroyed_instance(world: World) -> None:
    instance = world.instance(None)
    instance.destroy()

    for attempt in (
        lambda: instance.call("now"),
        instance.change_log,
        instance.inspect,
        lambda: instance.freeze("start", "Empty."),
        lambda: instance.bulk().__enter__(),
    ):
        with pytest.raises(WorldBug, match="has been destroyed"):
            attempt()


def test_inspect_opens_one_handle_and_keeps_it(instance: Instance) -> None:
    assert instance.inspect() is instance.inspect()


def test_inspect_sees_committed_writes_and_refuses_to_write(instance: Instance) -> None:
    add(instance, "n1")

    handle = instance.inspect()

    assert handle.rows("SELECT id FROM notes") == [{"id": "n1"}]
    with pytest.raises(DbError):
        handle.execute("INSERT INTO notes VALUES ('n2', 'b', 0)")


def test_inspect_does_not_see_a_write_that_has_not_committed(instance: Instance) -> None:
    handle = instance.inspect()

    with instance.bulk() as ctx:
        ctx.db.execute("INSERT INTO notes VALUES ('n1', 'uncommitted', 0)")
        assert handle.rows("SELECT id FROM notes") == []

    assert handle.rows("SELECT id FROM notes") == [{"id": "n1"}]


def test_a_control_tool_bypasses_the_chain(world: World) -> None:
    seen: list[str] = []

    @world.middleware
    def record(ctx: Ctx, call: Call, next_: Handler) -> Any:
        seen.append(call.name)
        return next_(ctx, call)

    def peek(live: Instance, ctx: Ctx) -> dict[str, bool]:
        """Look inside."""
        return {"peeked": True}

    world.tool(control_tool(peek))

    with world.instance(None) as instance:
        assert instance.call("peek") == {"peeked": True}
        instance.call("now")

    assert seen == ["now"]


def test_a_control_tool_validates_its_arguments(world: World) -> None:
    def peek(live: Instance, ctx: Ctx, limit: int) -> dict[str, int]:
        """Look inside, up to a point."""
        return {"limit": limit}

    world.tool(control_tool(peek))

    with world.instance(None) as instance:
        assert instance.call("peek", limit=3) == {"limit": 3}
        with pytest.raises(ArgumentError):
            instance.call("peek", limit="three")


def test_a_control_tool_is_called_with_exactly_the_validated_arguments(world: World) -> None:
    """What `validate` returns is what the function is called with, and what its context says.

    The same property `test_a_tool_is_called_with_exactly_the_validated_arguments`
    pins for the chain, because control dispatch does it in its own code. Both
    halves need pinning: an aliased parameter is not callable at all under the
    wire name, and a default is in neither the wire mapping nor the call's.
    """
    seen: list[Any] = []

    def peek(
        live: Instance, ctx: Ctx, limit: Annotated[int, Field(alias="max")] = 5
    ) -> dict[str, int]:
        """Look inside, up to a point."""
        assert ctx.call is not None
        seen.append(dict(ctx.call.arguments))
        return {"limit": limit}

    world.tool(control_tool(peek))

    with world.instance(None) as instance:
        # The wire name reaches the parameter it aliases, and the default the
        # tool declared reaches it too.
        assert instance.call("peek", max=7) == {"limit": 7}
        assert instance.call("peek") == {"limit": 5}

    assert seen == [{"limit": 7}, {"limit": 5}]


@pytest.mark.parametrize(
    ("result", "refusal"),
    [({"raw": b"bytes"}, "bytes"), ({"tags": {"b", "a"}}, "sets")],
)
def test_a_control_tools_result_is_held_to_the_rules_every_result_is(
    world: World, result: dict[str, Any], refusal: str
) -> None:
    """A control tool skips the chain, not the serialiser: it is no way around determinism."""

    def peek(live: Instance, ctx: Ctx) -> dict[str, Any]:
        """Answer with something no world may return."""
        return result

    world.tool(control_tool(peek))

    with world.instance(None) as instance, pytest.raises(WorldBug, match=refusal):
        instance.call("peek")


def test_a_control_tool_may_read_the_instance_it_is_called_on(world: World) -> None:
    """The re-entrancy case: the lock is held, and `inspect()` takes it again."""

    def peek_rows(live: Instance, ctx: Ctx, sql: str) -> list[dict[str, Any]]:
        """A thin wrapper over `Instance.inspect()`, whose open takes the lock again.

        Not what the framework's own control tools do -- they read through
        `Instance._control_db()` and deliberately not through this handle
        (`test_control.py`'s
        `test_the_control_handle_is_its_own_connection_opened_once_and_closed_with_the_instance`).
        What is being probed here is the re-entrancy any control tool needs: a
        world may write one that asks the instance for something, and asking is
        what takes the lock a second time on the same thread.
        """
        return live.inspect().rows(sql)

    world.tool(control_tool(peek_rows))

    with world.instance(None) as instance:
        add(instance, "n1")

        assert instance.call("peek_rows", sql="SELECT id FROM notes") == [{"id": "n1"}]


def test_bulk_commits_once_at_the_end(instance: Instance) -> None:
    handle = instance.inspect()

    with instance.bulk() as ctx:
        for number in range(100):
            ctx.db.execute(f"INSERT INTO notes VALUES ('n{number}', 'body', 0)")

    assert handle.rows("SELECT count(*) AS c FROM notes") == [{"c": 100}]


def test_bulk_rolls_back_when_the_block_raises(instance: Instance) -> None:
    with pytest.raises(RuntimeError), instance.bulk() as ctx:
        ctx.db.execute("INSERT INTO notes VALUES ('n1', 'body', 0)")
        raise RuntimeError("changed my mind")

    assert instance.inspect().rows("SELECT id FROM notes") == []


def test_bulk_yields_the_root_nodes_context_with_no_call(instance: Instance) -> None:
    """The instance's own state and store, with no call -- and a live `ctx.worlds`.

    Not the template context itself: a `bulk()` block reaches every node through
    `ctx.worlds.<name>.db`, which only a context bound to the block's activation
    can do.
    """
    with instance.bulk() as ctx:
        assert (ctx.db, ctx.state, ctx.ids) == (
            instance.ctx.db,
            instance.ctx.state,
            instance.ctx.ids,
        )
        assert ctx.call is None


def test_leaving_the_block_destroys_the_instance(world: World) -> None:
    with world.instance(None) as instance:
        directory = instance.dir
        assert directory.is_dir()

    assert instance.closed
    assert not directory.exists()


def test_the_block_destroys_even_when_it_raised(world: World) -> None:
    with pytest.raises(RuntimeError), world.instance(None) as instance:
        raise RuntimeError("boom")

    assert instance.closed
    assert not instance.dir.exists()


def test_destroy_is_idempotent(world: World) -> None:
    instance = world.instance(None)

    instance.destroy()
    instance.destroy()

    assert instance.closed


def test_destroy_removes_the_instance_directory_and_nothing_above_it(world: World) -> None:
    first = world.instance(None)
    second = world.instance(None)

    first.destroy()

    assert not first.dir.exists()
    assert second.dir.is_dir()
    second.destroy()


def test_destroy_waits_for_a_call_in_flight(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    inside = threading.Event()
    finish = threading.Event()
    destroyed = threading.Event()

    @world.tool
    def slow(ctx: Ctx) -> dict[str, bool]:
        """Hold the instance lock until the test lets go."""
        ctx.db.execute("INSERT INTO notes VALUES ('written', 'by the slow call', 0)")
        inside.set()
        assert finish.wait(WAIT)
        return {"done": True}

    instance = world.instance(None)
    caller = Caller(lambda: instance.call("slow"))
    caller.start()
    assert inside.wait(WAIT)

    destroyer = Caller(lambda: (instance.destroy(), destroyed.set()))
    destroyer.start()
    assert not destroyed.wait(0.2)  # the call still holds the lock

    finish.set()
    caller.finish()
    destroyer.finish()
    assert destroyed.is_set()
    assert instance.closed
    assert not instance.dir.exists()


def test_a_call_that_starts_after_a_destroy_is_refused(world: World) -> None:
    instance = world.instance(None)
    instance.destroy()

    with pytest.raises(WorldBug, match="has been destroyed"):
        instance.call("now")


def test_the_gate_bounds_how_many_calls_run_at_once(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    running = threading.Semaphore(0)
    release = threading.Event()

    @world.tool
    def slow(ctx: Ctx) -> dict[str, bool]:
        """Occupy a slot of the gate until the test lets go."""
        running.release()
        assert release.wait(WAIT)
        return {"done": True}

    set_concurrency(1)
    with world.instance(None) as first, world.instance(None) as second:
        callers = [Caller(partial(live.call, "slow")) for live in (first, second)]
        for caller in callers:
            caller.start()
        assert running.acquire(timeout=WAIT)  # one call got in
        assert not running.acquire(timeout=0.2)  # the other is queued behind the gate

        release.set()
        assert running.acquire(timeout=WAIT)
        for caller in callers:
            caller.finish()


def test_concurrency_zero_removes_the_gate(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    running = threading.Semaphore(0)
    release = threading.Event()

    @world.tool
    def slow(ctx: Ctx) -> dict[str, bool]:
        """Occupy a slot of the gate until the test lets go."""
        running.release()
        assert release.wait(WAIT)
        return {"done": True}

    set_concurrency(0)
    with world.instance(None) as first, world.instance(None) as second:
        callers = [Caller(partial(live.call, "slow")) for live in (first, second)]
        for caller in callers:
            caller.start()
        assert running.acquire(timeout=WAIT)
        assert running.acquire(timeout=WAIT)  # both, with no gate to queue behind

        release.set()
        for caller in callers:
            caller.finish()


def test_a_control_tool_passes_an_exhausted_gate(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    inside = threading.Event()
    release = threading.Event()

    @world.tool
    def slow(ctx: Ctx) -> dict[str, bool]:
        """Hold the only slot of the gate."""
        inside.set()
        assert release.wait(WAIT)
        return {"done": True}

    def peek(live: Instance, ctx: Ctx) -> dict[str, bool]:
        """Look inside."""
        return {"peeked": True}

    world.tool(control_tool(peek))

    set_concurrency(1)
    with world.instance(None) as first, world.instance(None) as second:
        caller = Caller(lambda: first.call("slow"))
        caller.start()
        assert inside.wait(WAIT)

        # Timed, not just awaited: a control call that queued would still answer
        # eventually, once the call holding the only slot gave up.
        started = time.perf_counter()
        assert second.call("peek") == {"peeked": True}
        assert second.tools() is not None
        assert time.perf_counter() - started < WAIT / 5

        release.set()
        caller.finish()


def test_the_gate_is_released_after_a_call_that_raised(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    set_concurrency(1)

    with world.instance(None) as instance:
        for _ in range(3):
            with pytest.raises(ValueError):
                instance.call("crash")

        assert instance.call("now")["python"] == instance.clock.iso()


def test_a_negative_concurrency_is_refused() -> None:
    with pytest.raises(WorldBug, match="must not be negative"):
        set_concurrency(-1)


def test_the_default_concurrency_follows_the_cpu_count() -> None:
    assert 1 <= default_concurrency() <= 16


def test_the_gates_size_can_be_read_back() -> None:
    """`concurrency()` answers what `set_concurrency` was given, `0` included.

    The accessor exists so that a caller wanting the size does not have to read
    `_gate._initial_value`; the assertion that `0` really stops gating is
    `test_concurrency_zero_removes_the_gate` above, and this one is only that
    the number is reported.
    """
    assert instances.concurrency() == default_concurrency()
    set_concurrency(3)
    assert instances.concurrency() == 3
    set_concurrency(0)
    assert instances.concurrency() == 0
    with pytest.raises(WorldBug):
        set_concurrency(-1)
    assert instances.concurrency() == 0, "a refused resize leaves the size alone"


def test_the_reported_size_travels_with_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whatever gate is in place is the gate whose size is reported.

    The size used to be recorded in a module-level variable of its own, which
    `set_concurrency` wrote alongside the gate. Anything that put a gate in
    place by another route -- a test substituting an instrumented one -- moved
    the gate and not the record, and the accessor then described a gate that was
    no longer there. `_Gate.size` derives the size from the semaphore's own
    bound, so there is no second place for it to be wrong; this asserts that
    rather than leaving it to the reading.
    """
    monkeypatch.setattr(instances, "_gate", instances._Gate(1))
    assert instances.concurrency() == 1


@pytest.fixture
def temp_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A private system temporary directory, so the default working directory is testable."""
    root = tmp_path / "tmp"
    root.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(root))
    # The root is per user: `<tempdir>` is shared by everyone on the machine.
    yield root / f"seahaven-{os.getuid()}"


def test_the_default_working_directory_is_per_process_and_per_world(
    tmp_path: Path, temp_root: Path
) -> None:
    world = build_world(tmp_path, work_dir=None, name="notesworld")

    with world.instance(None) as instance:
        assert instance.dir.parent == pid_dir(temp_root, os.getpid()) / "notesworld"
        assert instance.state_path.is_file()


def test_a_configured_working_directory_is_used_exactly_as_given(tmp_path: Path) -> None:
    world = build_world(tmp_path, work_dir=tmp_path / "mine")

    with world.instance(None) as instance:
        assert instance.dir.parent == tmp_path / "mine"


def pid_dir(temp_root: Path, pid: int) -> Path:
    """The working directory a process of this pid uses, namespace prefix and all."""
    return temp_root / instances._process_dirname(pid)


def dead_pid() -> int:
    """The pid of a process that has certainly finished."""
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    return finished.pid


def test_the_sweep_removes_a_dead_process_and_leaves_everything_else(
    tmp_path: Path, temp_root: Path
) -> None:
    world = build_world(tmp_path, work_dir=None)
    dead = pid_dir(temp_root, dead_pid())
    mine = pid_dir(temp_root, os.getpid())
    unnumbered = temp_root / f"{instances._dirname_prefix()}not-a-pid"
    for directory in (dead, mine, unnumbered):
        (directory / "an-instance").mkdir(parents=True)

    swept = world._instances().sweep_stale_processes()

    assert swept == 1
    assert not dead.exists()
    assert mine.is_dir()
    assert unnumbered.is_dir()


def test_the_sweep_judges_an_entry_without_following_it(
    tmp_path: Path, temp_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symlink named for a dead pid is not that process's working directory.

    `<tempdir>` is world-writable and the names under the root are predictable,
    so another user can plant one, pointing it at whatever they would like this
    process to delete. Judged by following it, it is a directory and is handed to
    `rmtree`; judged by what it is, it is not a directory and is not even
    offered. `rmtree` refuses a top-level symlink itself, so the target survives
    either way -- which is why what is *offered* has to be asserted rather than
    what is left standing, and why the check is defence in depth rather than the
    only thing between the planted name and the target.
    """
    world = build_world(tmp_path, work_dir=None)
    dead = pid_dir(temp_root, dead_pid())
    (dead / "an-instance").mkdir(parents=True)
    target = tmp_path / "somebody-elses-data"
    (target / "a-file").mkdir(parents=True)
    planted = pid_dir(temp_root, dead_pid())
    planted.symlink_to(target)
    offered: list[str] = []
    real_rmtree = instances.shutil.rmtree

    def rmtree(path: Any, *, dir_fd: int | None = None, ignore_errors: bool = False) -> None:
        offered.append(str(path))
        real_rmtree(path, dir_fd=dir_fd, ignore_errors=ignore_errors)

    monkeypatch.setattr(instances.shutil, "rmtree", rmtree)

    swept = world._instances().sweep_stale_processes()

    assert swept == 1
    assert not dead.exists()
    assert planted.is_symlink(), "the planted name was removed"
    assert (target / "a-file").is_dir(), "the target of the planted name was followed"
    assert offered == [dead.name], "a name that is not a directory was offered for removal"


def test_an_entry_the_sweep_cannot_answer_for_is_not_counted_as_swept(
    tmp_path: Path, temp_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gone is gone; anything the filesystem will not answer for is still there.

    `rmtree` is asked to ignore its errors, so the count is taken afterwards by
    looking. When the look itself is refused -- a directory that has become
    unreadable, an entry on a filesystem that has gone away -- the honest answer
    is that this process does not know it removed anything, and the `INFO` line
    it logs should not claim otherwise. Staged, because there is no portable way
    to make a real `stat` fail this way under a root this process owns.
    """
    world = build_world(tmp_path, work_dir=None)
    dead = pid_dir(temp_root, dead_pid())
    (dead / "an-instance").mkdir(parents=True)
    removed = False
    real_stat = instances.os.stat
    real_rmtree = instances.shutil.rmtree

    def rmtree(path: Any, *, dir_fd: int | None = None, ignore_errors: bool = False) -> None:
        nonlocal removed
        real_rmtree(path, dir_fd=dir_fd, ignore_errors=ignore_errors)
        removed = True

    def stat(name: Any, *, dir_fd: int | None = None, follow_symlinks: bool = True) -> Any:
        if removed and name == dead.name:
            raise PermissionError(errno.EACCES, "not answerable", str(name))
        return real_stat(name, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(instances.shutil, "rmtree", rmtree)
    monkeypatch.setattr(instances.os, "stat", stat)

    swept = world._instances().sweep_stale_processes()

    assert not dead.exists(), "the directory really was removed"
    assert swept == 0, "an entry the sweep cannot answer for was counted as swept"


def test_a_directory_another_sweep_removed_first_does_not_end_this_one(
    tmp_path: Path, temp_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two processes sweep one root: each one's first `create` does, and they overlap.

    An entry listed a moment ago can be gone before it is judged, because the
    other sweep did this one's work. Stepping over it is the whole answer;
    treating it as the root having failed abandons every entry not yet reached
    and reports nothing for the ones already removed. Staged rather than raced --
    the other process removes an entry this one has listed and not yet reached --
    so that it means the same thing on every machine. The listing order is fixed
    for the same reason: the entry that disappears has to be one with another
    entry behind it, or ending the sweep there and stepping over it look alike.
    """
    world = build_world(tmp_path, work_dir=None)
    names = [instances._process_dirname(dead_pid()) for _ in range(4)]
    for name in names:
        (temp_root / name / "an-instance").mkdir(parents=True)
    real_listdir = instances.os.listdir
    monkeypatch.setattr(
        instances.os, "listdir", lambda fd: sorted(real_listdir(fd), key=names.index)
    )
    removed_by_the_other_sweep = names[2]
    offered: list[str] = []
    real_rmtree = instances.shutil.rmtree

    def rmtree(path: Any, *, dir_fd: int | None = None, ignore_errors: bool = False) -> None:
        offered.append(str(path))
        real_rmtree(path, dir_fd=dir_fd, ignore_errors=ignore_errors)
        if len(offered) == 1:
            real_rmtree(temp_root / removed_by_the_other_sweep, ignore_errors=True)

    monkeypatch.setattr(instances.shutil, "rmtree", rmtree)

    swept = world._instances().sweep_stale_processes()

    assert contents(temp_root) == [], "the sweep ended at the entry that had gone"
    assert removed_by_the_other_sweep not in offered, "the vanished entry was judged anyway"
    assert swept == 3, "the count is what this sweep removed, not what any sweep removed"


def test_a_name_in_digits_this_code_never_writes_is_not_a_pid(
    tmp_path: Path, temp_root: Path
) -> None:
    """`str.isdigit` says yes to digits `int` reads differently, or not at all.

    Both names are reached through `world.instance()`, which is where the damage
    would land: a superscript digit makes `int` raise `ValueError`, which is not
    an `OSError` and so is caught by neither handler in the sweep -- the sweep
    dies and takes the caller's instance with it -- and an Arabic-Indic digit
    makes `int` answer an ordinary number, so a name this code never wrote is
    judged as somebody's pid and swept the moment that pid is dead. Nobody else
    can plant either name under an owner-checked `0o700` root; they are still
    not pids, and the sweep steps over them as it does a foreign namespace.
    """
    world = build_world(tmp_path, work_dir=None)
    dead = dead_pid()
    unreadable = temp_root / f"{instances._dirname_prefix()}\u00b2"
    # The same dead pid the sweep would remove, written in digits this code
    # never uses: `int` reads it as that pid, `str.isdecimal` still says yes,
    # and only `str.isascii` tells them apart.
    misread = temp_root / (
        instances._dirname_prefix() + "".join(chr(0x0660 + int(d)) for d in str(dead))
    )
    for planted in (unreadable, misread):
        (planted / "an-instance").mkdir(parents=True)
    really_dead = pid_dir(temp_root, dead)
    (really_dead / "an-instance").mkdir(parents=True)

    with world.instance(None) as instance:
        assert instance.call("now")  # the instance was made, not killed by the sweep

    assert not really_dead.exists(), "the sweep did not run"
    assert unreadable.is_dir(), "a name int() cannot read was judged anyway"
    assert misread.is_dir(), "a name int() reads as another pid was swept as that pid"


def test_the_sweep_runs_on_the_first_instance_of_the_process(
    tmp_path: Path, temp_root: Path
) -> None:
    world = build_world(tmp_path, work_dir=None)
    dead = pid_dir(temp_root, dead_pid())
    (dead / "an-instance").mkdir(parents=True)

    with world.instance(None):
        assert not dead.exists()

    # And not again: the second instance of the process sweeps nothing.
    other = pid_dir(temp_root, dead_pid())
    (other / "an-instance").mkdir(parents=True)
    with world.instance(None):
        assert other.is_dir()


def test_a_world_with_its_own_working_directory_never_sweeps(
    tmp_path: Path, temp_root: Path
) -> None:
    """A directory the caller named is theirs, and so is everything beside it."""
    world = build_world(tmp_path, work_dir=tmp_path / "mine")
    dead = pid_dir(temp_root, dead_pid())
    (dead / "an-instance").mkdir(parents=True)

    with world.instance(None):
        pass

    assert world._instances().sweep_stale_processes() == 0
    assert dead.is_dir()


def test_a_world_with_its_own_working_directory_does_not_spend_the_process_sweep(
    tmp_path: Path, temp_root: Path
) -> None:
    """The sweep is the process's one chance, and a world that never sweeps must not use it up.

    The realistic ordering, not an exotic one: a test suite configures
    `work_dir` for every world it builds, so a world that sweeps nothing going
    first is the normal case rather than the corner.
    """
    configured = build_world(tmp_path / "configured", work_dir=tmp_path / "mine", name="configured")
    defaulted = build_world(tmp_path / "defaulted", work_dir=None, name="defaulted")
    dead = pid_dir(temp_root, dead_pid())
    (dead / "an-instance").mkdir(parents=True)

    with configured.instance(None):
        assert dead.is_dir(), "a world with its own working directory swept somebody else's"

    with defaulted.instance(None):
        assert not dead.exists(), "the sweep was spent by a world that does not sweep"


def test_the_sweep_counts_what_it_removed_and_not_what_it_tried(
    tmp_path: Path,
    temp_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`rmtree` is asked to ignore errors, so a directory still standing was not swept."""
    caplog.set_level(logging.INFO, logger="seahaven.instances")
    world = build_world(tmp_path, work_dir=None)
    stubborn = pid_dir(temp_root, dead_pid())
    (stubborn / "an-instance").mkdir(parents=True)
    monkeypatch.setattr(instances.shutil, "rmtree", lambda *_args, **_kwargs: None)

    assert world._instances().sweep_stale_processes() == 0
    assert stubborn.is_dir()
    assert [
        record.getMessage() for record in caplog.records if "swept" in record.getMessage()
    ] == []


@pytest.fixture(params=[0o022, 0o077], ids=["umask-022", "umask-077"])
def umask(request: pytest.FixtureRequest) -> Iterator[int]:
    """Run the test under this umask, whatever the machine's own is.

    `mkdir`'s mode argument is masked by it, so a mode that is not asserted under
    two of them is not asserted at all: one umask makes the vacuous test pass and
    the other makes the correct code fail.
    """
    previous = os.umask(request.param)
    try:
        yield request.param
    finally:
        os.umask(previous)


def test_the_default_working_directory_is_private_to_this_user(
    tmp_path: Path, temp_root: Path, umask: int
) -> None:
    """An instance is a copy of a world's data, and `<tempdir>` is shared by the machine.

    Every level, not just the root: the root's mode protects what is under it only
    as far as the root itself can be trusted, and a root found already in place is
    exactly what cannot be. The modes are exact under either umask because they
    are set rather than asked for, `mkdir`'s mode being masked by it.
    """
    world = build_world(tmp_path, work_dir=None, name="notesworld")

    with world.instance(None) as instance:
        per_process = pid_dir(temp_root, os.getpid())
        for directory in (temp_root, per_process, per_process / "notesworld", instance.dir):
            assert stat_module.S_IMODE(directory.stat().st_mode) == 0o700, directory
        assert instance.dir.is_relative_to(per_process)


def test_the_working_directory_of_a_process_carries_its_pid_namespace(
    tmp_path: Path, temp_root: Path
) -> None:
    """A pid means nothing outside the namespace that issued it (see the sweep tests)."""
    world = build_world(tmp_path, work_dir=None)

    with world.instance(None) as instance:
        name = instance.dir.parent.parent.name

    if Path("/proc/self/ns/pid").exists():
        assert name == f"{os.stat('/proc/self/ns/pid').st_ino}-{os.getpid()}"
    else:  # no procfs to ask: the bare pid, which is the spec's own layout
        assert name == str(os.getpid())


def no_procfs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer `/proc/self/ns/pid` the way a machine without procfs does: it is not there.

    macOS has no pid namespaces and no procfs to ask about them, and CI here has
    both, so the degrade would otherwise be reasoned about rather than run.
    """
    real_stat = instances.os.stat

    def stat(path: Any, **kwargs: Any) -> Any:
        if path == "/proc/self/ns/pid":
            raise FileNotFoundError(errno.ENOENT, "No such file or directory", path)
        return real_stat(path, **kwargs)

    monkeypatch.setattr(instances.os, "stat", stat)


def test_without_a_procfs_the_working_directory_is_the_bare_pid(
    tmp_path: Path, temp_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recorded degrade: the spec's own layout, one namespace, and a sweep that works.

    Not the bare pid with something else in front of it -- `0-`, say, from an
    error value standing in for "there is no namespace" -- which would be a third
    layout that neither machine uses.
    """
    no_procfs(monkeypatch)
    world = build_world(tmp_path, work_dir=None, name="notesworld")
    dead = temp_root / str(dead_pid())
    (dead / "an-instance").mkdir(parents=True)

    with world.instance(None) as instance:
        assert instance.dir.parent == temp_root / str(os.getpid()) / "notesworld"
        assert not dead.exists(), "the sweep did not run on the degraded layout"


def test_the_working_root_is_never_wider_than_0o700_even_for_a_moment(
    tmp_path: Path, temp_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`mkdir` makes the root and `fchmod` settles it, and between them there is a window.

    The mode `mkdir` is given is a ceiling masked by the umask, which is why the
    `fchmod` follows it -- and is also why the ceiling has to be asked for: with
    `mkdir`'s default of `0o777`, a permissive umask leaves the root
    world-writable until the `fchmod` lands. The window exists at exactly one
    observable moment, so it is read from inside the `fchmod` itself, under the
    umask that makes the difference visible.
    """
    real_fchmod = instances.os.fchmod
    seen: list[int] = []

    def watch(fd: int, mode: int) -> None:
        seen.append(stat_module.S_IMODE(os.fstat(fd).st_mode))
        real_fchmod(fd, mode)

    monkeypatch.setattr(instances.os, "fchmod", watch)
    # Last, and with nothing between it and the `try`: anything that raises in
    # between would leave `umask 000` behind for the rest of the session.
    previous = os.umask(0o000)
    try:
        world = build_world(tmp_path, work_dir=None)
        with world.instance(None):
            pass
    finally:
        os.umask(previous)

    assert seen == [0o700]
    assert stat_module.S_IMODE(temp_root.stat().st_mode) == 0o700


def test_a_working_root_left_behind_with_the_wrong_mode_is_repaired(
    tmp_path: Path, temp_root: Path
) -> None:
    """`exist_ok=True` says nothing about a mode, and the root outlives the run that made it."""
    temp_root.mkdir(parents=True)
    temp_root.chmod(0o755)
    world = build_world(tmp_path, work_dir=None)

    with world.instance(None):
        assert stat_module.S_IMODE(temp_root.stat().st_mode) == 0o700


def test_a_working_root_that_is_a_symlink_is_refused_and_never_followed(
    tmp_path: Path, temp_root: Path
) -> None:
    """The attack `<tempdir>` invites: the sticky bit stops deletes, not planted names.

    `<tempdir>` is world-writable and `seahaven-<uid>` is predictable, so another
    user can put a symlink there before Seahaven ever runs. Followed by path, the
    `chmod` lands on the target and the sweep recursively removes the target's
    numerically-named children. Both halves are pinned here, through
    `world.instance()` rather than through either function: the sweep runs first
    and would have done its damage before the refusal.
    """
    victim = tmp_path / "victim"
    (victim / instances._process_dirname(dead_pid()) / "precious").mkdir(parents=True)
    victim.chmod(0o755)
    temp_root.parent.mkdir(parents=True, exist_ok=True)
    temp_root.symlink_to(victim)
    world = build_world(tmp_path, work_dir=None)

    with pytest.raises(WorldBug, match=r"cannot make a working directory under .*seahaven-"):
        world.instance(None)

    assert stat_module.S_IMODE(victim.stat().st_mode) == 0o755, "the chmod followed the symlink"
    assert contents(victim) != [], "the sweep followed the symlink"


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="counting descriptors needs procfs")
def test_opening_the_working_root_leaves_no_descriptor_behind(
    tmp_path: Path, temp_root: Path
) -> None:
    """The root is opened once per instance, so a descriptor kept is one per instance.

    A long eval run makes thousands of instances and would run the process out of
    descriptors.
    """
    world = build_world(tmp_path, work_dir=None)
    with world.instance(None):  # the first instance also sweeps and warms sqlite
        pass
    before = len(os.listdir("/proc/self/fd"))

    for _ in range(8):
        with world.instance(None):
            pass

    assert len(os.listdir("/proc/self/fd")) == before


def fake_owner(monkeypatch: pytest.MonkeyPatch, root: Path, *, the_root: bool) -> None:
    """Make one side of the root's inode belong to somebody this process is not.

    A suite that runs as one user cannot produce a directory another user owns,
    so the ownership is faked from this side and the comparison exercised is the
    real one: `geteuid` answers with a user this process is not, and every
    directory on the chosen side of the root answers with that same user, so the
    other side is what fails to match. `the_root=True` makes the root the odd one
    out (a root planted by somebody else); `the_root=False` makes everything
    below it the odd one out (a directory planted below a root that is ours). A
    second uid is what the phase plan records exercising both end to end.
    """
    root_inode = root.stat().st_ino
    real_fstat = instances.os.fstat
    somebody_else = os.geteuid() + 1
    monkeypatch.setattr(instances.os, "geteuid", lambda: somebody_else)

    def fstat(fd: int) -> Any:
        info = real_fstat(fd)
        planted = (info.st_ino == root_inode) is the_root
        # The planted directory keeps its real owner, which is now nobody this
        # process claims to be; everything else answers as this process.
        return info if planted else SimpleNamespace(st_uid=somebody_else)

    monkeypatch.setattr(instances.os, "fstat", fstat)


def test_a_working_root_belonging_to_another_user_is_refused(
    tmp_path: Path, temp_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain directory planted at the root's name, which a `chmod` as uid 0 would adopt."""
    temp_root.mkdir(parents=True)
    world = build_world(tmp_path, work_dir=None)
    fake_owner(monkeypatch, temp_root, the_root=True)

    with pytest.raises(WorldBug, match=r"cannot make a working directory under .*seahaven-"):
        world.instance(None)


def test_a_per_process_directory_that_is_a_symlink_is_refused_and_never_followed(
    tmp_path: Path, temp_root: Path
) -> None:
    """The name below the root is as predictable as the root's own, and as plantable.

    A root that is writable by somebody else -- left that way by an external
    actor, or caught in the window before the first `fchmod` under a permissive
    umask -- lets a symlink be planted at `<namespace>-<pid>`. Created through
    its name, the whole instance lands wherever the link points, and a live
    world's database is readable by whoever put it there. Pinned through
    `world.instance()`, which is where it happened.
    """
    victim = tmp_path / "victim"
    victim.mkdir()
    temp_root.mkdir(parents=True)
    (temp_root / instances._process_dirname(os.getpid())).symlink_to(victim)
    world = build_world(tmp_path, work_dir=None, name="notesworld")

    with pytest.raises(WorldBug, match=r"cannot make a working directory under .*seahaven-"):
        world.instance(None)

    assert contents(victim) == [], "the instance was made through the symlink"


def test_a_world_directory_that_is_a_symlink_is_refused_and_never_followed(
    tmp_path: Path, temp_root: Path
) -> None:
    """The same again one level further down: every level is checked, not just the first."""
    victim = tmp_path / "victim"
    victim.mkdir()
    per_process = pid_dir(temp_root, os.getpid())
    per_process.mkdir(parents=True)
    (per_process / "notesworld").symlink_to(victim)
    world = build_world(tmp_path, work_dir=None, name="notesworld")

    with pytest.raises(WorldBug, match=r"cannot make a working directory under .*seahaven-"):
        world.instance(None)

    assert contents(victim) == [], "the instance was made through the symlink"


def test_a_directory_below_the_root_belonging_to_another_user_is_refused(
    tmp_path: Path, temp_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain directory planted below the root is theirs, not a place to put a world."""
    temp_root.mkdir(parents=True)
    pid_dir(temp_root, os.getpid()).mkdir()
    world = build_world(tmp_path, work_dir=None, name="notesworld")
    fake_owner(monkeypatch, temp_root, the_root=False)

    with pytest.raises(WorldBug, match=r"cannot make a working directory under .*seahaven-"):
        world.instance(None)


def test_the_sweep_removes_through_the_descriptor_it_judged_through(
    tmp_path: Path, temp_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Collection and removal are one inode, not one name looked up twice.

    The window between them is real -- the root is a name in a world-writable
    `<tempdir>` -- so it is staged here rather than raced: the first removal
    swaps the checked root away and leaves a symlink to a victim standing at its
    name. Through the descriptor the remaining entries are still the ones that
    were judged; through the name they are the victim's.
    """
    world = build_world(tmp_path, work_dir=None)
    victim = tmp_path / "victim"
    names = [instances._process_dirname(dead_pid()) for _ in range(2)]
    for name in names:
        (temp_root / name / "an-instance").mkdir(parents=True)
        (victim / name / "precious").mkdir(parents=True)
    moved = temp_root.parent / "moved"
    real_rmtree = instances.shutil.rmtree

    def rmtree(path: Any, *, dir_fd: int | None = None, ignore_errors: bool = False) -> None:
        if not moved.exists():
            temp_root.rename(moved)
            temp_root.symlink_to(victim)
        real_rmtree(path, dir_fd=dir_fd, ignore_errors=ignore_errors)

    monkeypatch.setattr(instances.shutil, "rmtree", rmtree)

    swept = world._instances().sweep_stale_processes()

    assert sorted(contents(victim)) == sorted(names), "the sweep removed through the root's name"
    assert contents(moved) == [], "the entries that were judged were not the ones removed"
    assert swept == 2


def test_the_instance_directory_is_made_through_the_descriptor_that_was_checked(
    tmp_path: Path, temp_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last `mkdir` is anchored to the checked `<world>` descriptor, not composed as a path.

    Every component above it has just been checked, so on today's code a composed
    path lands in the same place and the anchoring is a structural property with
    no output to observe. It is staged the way the sweep's is: once the `<world>`
    directory has been opened and checked, it is renamed away and a symlink to a
    victim is left standing at its name. Through the descriptor the instance is
    made in the directory that was judged; through the name it would be made in
    the victim's.
    """
    world = build_world(tmp_path, work_dir=None, name="notesworld")
    victim = tmp_path / "victim"
    victim.mkdir()
    process_dir = pid_dir(temp_root, os.getpid())
    world_dir = process_dir / "notesworld"
    moved = process_dir / "moved"
    real_open_child = instances._open_child

    @contextmanager
    def swapping(parent: int, name: str) -> Iterator[int]:
        with real_open_child(parent, name) as fd:
            if name == "notesworld":
                world_dir.rename(moved)
                world_dir.symlink_to(victim)
            yield fd

    monkeypatch.setattr(instances, "_open_child", swapping)

    world._instances()._make_instance_dir("an-instance")

    assert contents(moved) == ["an-instance"], "the instance was not made through the descriptor"
    assert contents(victim) == [], "the instance was made through the world directory's name"


def test_a_platform_that_cannot_harden_the_root_has_no_default_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No user id, no `O_NOFOLLOW`, no `mkdirat`: a refusal that says so.

    Not an `AttributeError` or a `NotImplementedError` out of `world.instance()`.
    """
    monkeypatch.setattr(instances, "_POSIX_WORK_ROOT", False)
    world = build_world(tmp_path, work_dir=None)

    with pytest.raises(WorldBug, match=r"no default working directory on this platform"):
        world.instance(None)

    assert world._instances().sweep_stale_processes() == 0


def test_a_configured_working_directory_that_cannot_be_made_is_a_world_bug(
    tmp_path: Path,
) -> None:
    """The caller named it, so the caller is who the refusal is for."""
    blocked = tmp_path / "a-file"
    blocked.write_text("not a directory")
    world = build_world(tmp_path, work_dir=blocked / "work")

    with pytest.raises(WorldBug, match=r"cannot make the working directory .*World\(work_dir="):
        world.instance(None)


def test_closing_the_manager_destroys_every_instance(world: World) -> None:
    first = world.instance(None)
    second = world.instance(None)

    world._instances().close()
    world._instances().close()

    assert first.closed and second.closed
    assert instance_dirs(world) == []


def test_two_hundred_instances_of_one_fixture_in_one_process(world: World) -> None:
    fixture = frozen_fixture(world)

    live = [world.instance(fixture, seed=number) for number in range(200)]
    try:
        assert len({instance.id for instance in live}) == 200
        assert len({instance.dir for instance in live}) == 200
        assert live[7].call("rows", sql="SELECT count(*) AS c FROM notes") == [{"c": 1}]
    finally:
        for instance in live:
            instance.destroy()

    assert instance_dirs(world) == []


def test_an_instance_is_its_own_copy(world: World) -> None:
    """Two instances of one fixture cannot see each other."""
    fixture = frozen_fixture(world)

    with world.instance(fixture) as first, world.instance(fixture) as second:
        add(first, "only-in-the-first")

        assert second.inspect().rows("SELECT count(*) AS c FROM notes") == [{"c": 1}]


def test_the_instance_reads_as_what_it_is(world: World) -> None:
    with world.instance(None) as instance:
        assert instance.id in repr(instance)
        assert "blank" in repr(instance)


def test_the_inspection_handle_is_closed_with_the_instance(world: World) -> None:
    instance = world.instance(None)
    handle: Db = instance.inspect()

    instance.destroy()

    with pytest.raises(DbError) as raised:
        handle.rows("SELECT 1")
    assert "closed" in raised.value.sqlite_message


def test_destroy_takes_the_instance_out_of_the_registry(world: World) -> None:
    """A server that makes and destroys instances for a day must not grow all day."""
    manager = world._instances()
    instance = world.instance(None)
    assert manager._instances == {instance.id: instance}

    instance.destroy()

    assert manager._instances == {}


def test_the_manager_destroys_an_instance_too(world: World) -> None:
    instance = world.instance(None)

    world._instances().destroy(instance)

    assert instance.closed
    assert not instance.dir.exists()


def test_the_connection_is_closed_with_the_instance(world: World) -> None:
    """Not just the files: a process serving 500 sessions would run out of handles."""
    instance = world.instance(None)

    instance.destroy()

    with pytest.raises(DbError) as raised:
        instance.db.rows("SELECT 1")
    assert "closed" in raised.value.sqlite_message


def test_destroy_logs_one_line(world: World, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="seahaven.instances")
    instance = world.instance(None)

    instance.destroy()
    instance.destroy()

    destroyed = [
        record.getMessage() for record in caplog.records if "destroyed" in record.getMessage()
    ]
    assert destroyed == [f"destroyed instance {instance.id} of world {world.name}"]


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs /proc")
def test_a_creation_that_fails_after_the_database_is_open_leaks_nothing(tmp_path: Path) -> None:
    world = build_world(tmp_path)

    @world.instance_startup
    def seed(ctx: Ctx) -> None:
        raise RuntimeError("the hook could not")

    with pytest.raises(RuntimeError):  # the first attempt warms every lazy import and cache
        world.instance(None)
    before = len(os.listdir("/proc/self/fd"))

    for _ in range(3):
        with pytest.raises(RuntimeError):
            world.instance(None)

    assert len(os.listdir("/proc/self/fd")) == before


def test_a_creation_that_fails_before_the_database_is_open_leaves_nothing(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = frozen_fixture(world)

    def explode(*_args: object, **_kwargs: object) -> None:
        raise OSError("the disk is full")

    monkeypatch.setattr(instances.shutil, "copyfile", explode)
    with pytest.raises(OSError, match="disk is full"):
        world.instance(fixture)

    assert instance_dirs(world) == []


def test_the_manager_registers_one_atexit_hook(world: World) -> None:
    """So a process that forgets to destroy its instances still takes their files with it."""
    before = atexit._ncallbacks()

    world.instance(None).destroy()
    world.instance(None).destroy()

    assert atexit._ncallbacks() == before + 1


def test_the_sweep_says_what_it_removed(
    tmp_path: Path, temp_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="seahaven.instances")
    world = build_world(tmp_path, work_dir=None)
    (pid_dir(temp_root, dead_pid()) / "an-instance").mkdir(parents=True)

    world._instances().sweep_stale_processes()

    assert [record.getMessage() for record in caplog.records if "swept" in record.getMessage()] == [
        "swept 1 working directories of processes that are gone"
    ]


def test_the_sweep_leaves_a_directory_from_another_pid_namespace_alone(
    tmp_path: Path, temp_root: Path
) -> None:
    """Two containers sharing a `<tempdir>`: one container's pid is not the other's process.

    `os.kill(pid, 0)` answers in the caller's namespace, so a name from another
    namespace cannot be judged at all -- and judging it wrong means deleting a
    running eval's database. It is left alone for ever instead.
    """
    if not Path("/proc/self/ns/pid").exists():
        pytest.skip("no procfs, so there is no namespace to be in another of")
    world = build_world(tmp_path, work_dir=None)
    elsewhere = temp_root / f"{os.stat('/proc/self/ns/pid').st_ino + 1}-{dead_pid()}"
    (elsewhere / "an-instance").mkdir(parents=True)

    with world.instance(None):
        pass

    assert world._instances().sweep_stale_processes() == 0
    assert elsewhere.is_dir()


def test_the_sweep_never_signals_process_zero(tmp_path: Path, temp_root: Path) -> None:
    """`kill(0, 0)` is this process's whole group, which is no way to ask a question."""
    world = build_world(tmp_path, work_dir=None)
    zero = pid_dir(temp_root, 0)
    zero.mkdir(parents=True)

    assert world._instances().sweep_stale_processes() == 0
    assert zero.is_dir()


def test_the_sweep_leaves_a_process_it_may_not_signal(
    tmp_path: Path, temp_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another user's pid: the cost of guessing wrong is deleting a running instance."""
    world = build_world(tmp_path, work_dir=None)
    theirs = pid_dir(temp_root, dead_pid())
    theirs.mkdir(parents=True)

    def refuse(_pid: int, _signal: int) -> None:
        raise PermissionError("not yours")

    monkeypatch.setattr(instances.os, "kill", refuse)

    assert world._instances().sweep_stale_processes() == 0
    assert theirs.is_dir()


def test_the_sweep_runs_once_per_process_and_not_once_per_world(
    tmp_path: Path, temp_root: Path
) -> None:
    """The directories it removes belong to a process, so the first world sweeps for all of them."""
    first = build_world(tmp_path / "first", work_dir=None, name="first")
    second = build_world(tmp_path / "second", work_dir=None, name="second")
    with first.instance(None):
        pass
    left = pid_dir(temp_root, dead_pid())
    (left / "an-instance").mkdir(parents=True)

    with second.instance(None):
        pass

    assert left.is_dir()


def test_a_call_queued_behind_the_gate_does_not_delay_a_destroy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate is taken before the instance lock, so a queued call holds nothing."""
    world = build_world(tmp_path)
    inside = threading.Event()
    release = threading.Event()
    arrivals = threading.Semaphore(0)

    class WatchedGate(instances._Gate):
        """A one-slot gate that says when a caller has reached it.

        So the test waits for an arrival instead of for a duration: under the
        ordering this test is here to refuse -- the lock taken first and the gate
        second -- the arrival is the moment the lock is already held.

        Subclasses the module's own gate rather than `BoundedSemaphore`, so the
        substitute published by `instances.concurrency()` is the one that is
        actually gating: the size travels with the semaphore and a test cannot
        move one without the other.
        """

        def acquire(self, blocking: bool = True, timeout: float | None = None) -> bool:
            arrivals.release()
            return super().acquire(blocking, timeout)

    @world.tool
    def slow(ctx: Ctx) -> dict[str, bool]:
        """Hold the only slot of the gate until the test lets go."""
        inside.set()
        assert release.wait(WAIT)
        return {"done": True}

    monkeypatch.setattr(instances, "_gate", WatchedGate(1))
    with world.instance(None) as holder:
        queued_instance = world.instance(None)
        occupier = Caller(partial(holder.call, "slow"))
        occupier.start()
        assert inside.wait(WAIT)
        assert arrivals.acquire(timeout=WAIT)  # the occupier's own arrival, and the slot is gone

        # This one gets no further than the gate. Taken inside the lock instead,
        # it would be holding its instance's lock while it waited.
        queued = Caller(partial(queued_instance.call, "now"))
        queued.start()
        assert arrivals.acquire(timeout=WAIT), "the queued call never reached the gate"
        assert queued_instance.lock.acquire(blocking=False), "the queued call holds its own lock"
        queued_instance.lock.release()

        destroyed = Caller(queued_instance.destroy)
        destroyed.start()
        destroyed.join(WAIT / 5)
        assert not destroyed.is_alive(), "destroy waited for a call that had not started"

        release.set()
        occupier.finish()
        queued.join(WAIT)  # it wakes to find its instance gone, which is its business
        destroyed.finish()
    assert queued_instance.closed
