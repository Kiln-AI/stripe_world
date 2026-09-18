"""The framework's own tool, driven the way an eval drives it.

Everything here goes through `Instance.call`, because that is the whole of the
control path: the generic machinery (validation, serialisation, the bypasses) is
pinned in `test_instances.py` against a stand-in control tool, and what these
tests are about is the real one.

`controller_run_sql` is deprecated, so every call of it warns. The module filter
below silences the calls that are here for another reason; the two tests about
the warning turn it back on for themselves.
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from seahaven import control, sandbox
from seahaven.call import Call, Handler
from seahaven.ctx import Ctx
from seahaven.errors import ArgumentError, DbError, UnknownTool, WorldBug
from seahaven.instances import Instance
from seahaven.world import World
from tests.conftest import WAIT, Caller, build_world

pytestmark = pytest.mark.filterwarnings("ignore:controller_run_sql is deprecated")

SCHEMA = """
CREATE TABLE notes (
    id TEXT PRIMARY KEY,
    body TEXT NOT NULL,
    n INTEGER NOT NULL DEFAULT 0
) STRICT;

CREATE TABLE files (
    id TEXT PRIMARY KEY,
    data BLOB
) STRICT;
"""


@pytest.fixture
def instance(tmp_path: Path) -> Any:
    world = build_world(tmp_path, SCHEMA)
    with world.instance(None) as live:
        yield live


def add(instance: Instance, id: str, body: str = "b") -> None:
    instance.call("execute", sql=f"INSERT INTO notes (id, body) VALUES ('{id}', '{body}')")


def test_it_reads_a_table_the_framework_knows_nothing_about(instance: Instance) -> None:
    """No allowlist: a table no world declared, made after the instance started, is readable."""
    instance.call("execute", sql="CREATE TABLE scratch (k TEXT PRIMARY KEY) STRICT")
    instance.call("execute", sql="INSERT INTO scratch (k) VALUES ('kept')")

    assert instance.call("controller_run_sql", sql="SELECT k FROM scratch") == {
        "columns": ["k"],
        "rows": [["kept"]],
        "row_count": 1,
        "truncated": False,
    }


def test_it_runs_the_introspection_pragmas(instance: Instance) -> None:
    """`PRAGMA table_info` is a read on the inspection connection, so control may ask it."""
    result = instance.call("controller_run_sql", sql="PRAGMA table_info(notes)")

    assert [row[1] for row in result["rows"]] == ["id", "body", "n"]


def test_it_reads_the_committed_state_not_the_world_allowlist(instance: Instance) -> None:
    add(instance, "n1")

    assert instance.call("controller_run_sql", sql="SELECT count(*) FROM notes")["rows"] == [[1]]


def test_params_are_positional(instance: Instance) -> None:
    add(instance, "n1", body="first")
    add(instance, "n2", body="second")

    result = instance.call(
        "controller_run_sql", sql="SELECT id FROM notes WHERE body = ?", params=["second"]
    )

    assert result["rows"] == [["n2"]]


def test_a_write_is_refused(instance: Instance) -> None:
    """The inspection connection's permanent denial, not an allowlist of control's own."""
    with pytest.raises(DbError) as raised:
        instance.call("controller_run_sql", sql="INSERT INTO notes (id, body) VALUES ('x', 'y')")

    assert raised.value.refusals
    assert instance.call("controller_run_sql", sql="SELECT count(*) FROM notes")["rows"] == [[0]]


def test_a_schema_change_is_refused(instance: Instance) -> None:
    """The adjacent case to a write: a statement SQLite would run on a read-only connection."""
    with pytest.raises(DbError):
        instance.call("controller_run_sql", sql="CREATE TABLE sneaky (id TEXT PRIMARY KEY) STRICT")

    assert instance.call(
        "controller_run_sql",
        sql="SELECT count(*) FROM sqlite_master WHERE name = 'sneaky'",
    )["rows"] == [[0]]


def test_the_permanent_denial_is_still_there_afterwards(instance: Instance) -> None:
    """`run_statement` puts back what it found; what it found had better not be nothing.

    The reason control mirrors the connection's authorizer rather than replacing
    it: if a control call left the door open, the next read through `inspect()`
    would be running with no denial at all.
    """
    instance.call("controller_run_sql", sql="SELECT 1")

    assert instance.inspect().conn.authorizer is not None
    with pytest.raises(DbError):
        instance.call("controller_run_sql", sql="DELETE FROM notes")


def test_the_control_handle_is_its_own_connection_opened_once_and_closed_with_the_instance(
    tmp_path: Path,
) -> None:
    """One handle, not the eval's, and not a new one per call.

    Not the eval's, because `run_statement` borrows connection state and an
    `inspect()` read takes no lock. Not a new one per call, because nothing would
    ever close them: a long eval would spend a file descriptor on every control
    call it made.
    """
    world: World = build_world(tmp_path, SCHEMA)
    instance = world.instance(None)
    instance.call("controller_run_sql", sql="SELECT 1")
    handle = instance._control_db()

    assert handle is instance._control_db()
    assert handle is not instance.inspect()

    instance.destroy()

    with pytest.raises(DbError) as raised:
        handle.rows("SELECT 1")
    assert "closed" in raised.value.sqlite_message


def test_a_control_connection_with_no_authorizer_is_a_world_bug(instance: Instance) -> None:
    """The second lock is the one control leans on; gone, this is not the door to find out.

    Reaching for the private handle on purpose: it is the connection control
    actually runs on, and the `inspect()` one -- which this test used to clear,
    and which cleared nothing that mattered once control got its own -- is not.
    """
    instance._control_db().conn.authorizer = None

    with pytest.raises(WorldBug, match="no authorizer"):
        instance.call("controller_run_sql", sql="SELECT 1")


def test_sqlites_own_text_is_the_message(instance: Instance) -> None:
    """An eval grading a run wants the real error, as at a SQL door."""
    with pytest.raises(DbError, match="no such column: missing") as raised:
        instance.call("controller_run_sql", sql="SELECT missing FROM notes")

    assert raised.value.refusals == ()


def test_a_second_statement_is_refused(instance: Instance) -> None:
    """One statement, like every other door in the framework."""
    with pytest.raises(DbError, match="statement after the first"):
        instance.call("controller_run_sql", sql="SELECT 1; SELECT 2")


def test_a_blob_arrives_as_base64_and_null_as_null(instance: Instance) -> None:
    """One BLOB policy for the framework: base64 here, in `run_sql` and in a changeset."""
    instance.call("execute", sql="INSERT INTO files (id, data) VALUES ('f1', x'00ff')")
    instance.call("execute", sql="INSERT INTO files (id, data) VALUES ('f2', NULL)")

    result = instance.call("controller_run_sql", sql="SELECT id, data FROM files ORDER BY id")

    assert result["rows"] == [["f1", "AP8="], ["f2", None]]


def test_a_blob_can_be_a_parameter_as_well_as_a_result(instance: Instance) -> None:
    """The other half of the blob policy: base64 comes back, raw bytes go in.

    `SqlValue` includes `bytes`, so an eval matching on a stored blob sends the
    bytes themselves -- there is nothing on the way in that would decode base64,
    and a caller that sent text would be matching text.
    """
    instance.call("execute", sql="INSERT INTO files (id, data) VALUES ('f1', x'00ff')")

    result = instance.call(
        "controller_run_sql", sql="SELECT id FROM files WHERE data = ?", params=[b"\x00\xff"]
    )

    assert result["rows"] == [["f1"]]


def test_the_fixed_value_cap_applies_to_a_control_read(instance: Instance) -> None:
    """Recorded in the phase plan: the spec says "no caps", and this one is not lifted.

    `MAX_VALUE_BYTES` is not a policy `controller_run_sql` sets -- it is
    `run_statement`'s, for a reason that does not go away for a trusted caller
    (nothing can interrupt SQLite inside one opcode). An eval that needs a value
    larger than this reads it through `instance.inspect()`, which has no cap --
    and which control no longer shares a connection with, so that advice holds
    from any thread at any time, not only between calls.
    """
    big = sandbox.MAX_VALUE_BYTES + 1
    instance.call("execute", sql=f"INSERT INTO files (id, data) VALUES ('big', zeroblob({big}))")

    with pytest.raises(DbError) as raised:
        instance.call("controller_run_sql", sql="SELECT data FROM files WHERE id = 'big'")

    assert raised.value.refusals == (f"value larger than {sandbox.MAX_VALUE_BYTES} bytes",)
    assert len(instance.inspect().rows("SELECT data FROM files WHERE id = 'big'")[0]["data"]) == big


def test_an_attach_is_refused(instance: Instance) -> None:
    """The case that says the mirrored denial is load-bearing and not decoration.

    A write is refused twice over -- the connection is read-only as well -- but
    `ATTACH` is a statement SQLite would run happily on a read-only connection.
    What stops it is the inspection authorizer's answer, passed on.
    """
    with pytest.raises(DbError) as raised:
        instance.call("controller_run_sql", sql="ATTACH DATABASE ':memory:' AS other")

    assert raised.value.refusals == ("action ATTACH ':memory:'",)


def test_a_vacuum_is_refused_as_a_write_before_it_runs(instance: Instance) -> None:
    """VACUUM asks the authorizer for nothing, so what stops it is the read-only tracer.

    `run_statement(read_only=True)` is what control passes, and this is the case
    that tells it apart from `read_only=False`: SQLite's own verdict on the
    prepared statement, before it steps.
    """
    with pytest.raises(DbError) as raised:
        instance.call("controller_run_sql", sql="VACUUM")

    assert raised.value.refusals == ("write in a read-only query",)


def test_dispatch_without_a_call_on_the_context_is_a_world_bug(instance: Instance) -> None:
    """`control.dispatch` is public, and a context with no call is not a thing to guess at."""
    with pytest.raises(WorldBug, match="bound to its call"):
        control.dispatch(instance, instance.ctx)


def test_a_bad_argument_is_an_argument_error(instance: Instance) -> None:
    with pytest.raises(ArgumentError):
        instance.call("controller_run_sql", sql=17)
    with pytest.raises(ArgumentError):
        instance.call("controller_run_sql", sql="SELECT 1", params="n1")


# The deprecation, and what the removal of `controller_changes` left behind.


def test_controller_run_sql_warns_that_it_is_deprecated(instance: Instance) -> None:
    """The docstring names the replacement, and so does the warning."""
    with pytest.warns(DeprecationWarning, match="read inst.state.. instead"):
        instance.call("controller_run_sql", sql="SELECT 1")

    assert instance.world.tools["controller_run_sql"].description.startswith("Deprecated:")


def test_the_deprecation_is_reported_against_the_callers_own_line(instance: Instance) -> None:
    """Not a line of the framework's: `skip_file_prefixes` walks out to the caller.

    A `stacklevel` cannot do it -- in process, over OpenEnv and through
    `control.dispatch` are three different depths -- and a warning attributed to
    `instances.py` would be deduplicated once for the whole process, so a caller
    would be told once ever.
    """
    with pytest.warns(DeprecationWarning) as caught:
        instance.call("controller_run_sql", sql="SELECT 1")

    (warned,) = caught.list
    assert Path(warned.filename) == Path(__file__)
    assert Path(warned.filename).parent != Path(control.__file__).parent


def test_controller_changes_is_an_unknown_tool(instance: Instance) -> None:
    """Removed with `Instance.changes()`: `inst.state()` is what an eval reads."""
    with pytest.raises(UnknownTool, match="controller_changes"):
        instance.call("controller_changes")


def test_a_world_may_now_register_a_tool_named_controller_changes(tmp_path: Path) -> None:
    """The name left the reserved set with the tool, so it is a world's to use."""
    world: World = build_world(tmp_path, SCHEMA)

    @world.tool(name="controller_changes")
    def whatever_the_world_means_by_it(ctx: Ctx) -> dict[str, str]:
        """A world's own tool, which happens to have had the name before."""
        return {"mine": "not the framework's"}

    with world.instance(None) as instance:
        assert instance.call("controller_changes") == {"mine": "not the framework's"}
        assert "controller_changes" in {tool["name"] for tool in instance.tools()}


def test_the_control_tool_is_not_offered_to_the_agent(instance: Instance) -> None:
    listed = {tool["name"] for tool in instance.tools()}

    assert "controller_run_sql" not in listed


def test_control_calls_bypass_the_middleware_chain(tmp_path: Path) -> None:
    world: World = build_world(tmp_path, SCHEMA)
    seen: list[str] = []

    @world.middleware
    def record(ctx: Ctx, call: Call, next_: Handler) -> Any:
        seen.append(call.name)
        return next_(ctx, call)

    with world.instance(None) as instance:
        instance.call("controller_run_sql", sql="SELECT 1")
        add(instance, "n1")

    assert seen == ["execute"]


def test_a_control_call_from_inside_a_call_does_not_deadlock(tmp_path: Path) -> None:
    """The lock is held by the call in flight, and control takes it again on the same thread."""
    world: World = build_world(tmp_path, SCHEMA)
    holder: list[Instance] = []

    @world.tool
    def audited(ctx: Ctx) -> dict[str, Any]:
        """Write, then read the instance back through the control door."""
        ctx.db.execute("INSERT INTO notes (id, body) VALUES ('n1', 'b')")
        return {"counted": holder[0].call("controller_run_sql", sql="SELECT count(*) FROM notes")}

    with world.instance(None) as instance:
        holder.append(instance)

        # The write is in the call's own transaction, so the control read on the
        # instance's second connection cannot see it yet. What is being pinned is
        # that it answers at all: the same thread already holds the instance lock.
        assert instance.call("audited") == {
            "counted": {"columns": ["count(*)"], "rows": [[0]], "row_count": 1, "truncated": False}
        }
        assert instance.call("controller_run_sql", sql="SELECT count(*) FROM notes")["rows"] == [
            [1]
        ]


def test_a_control_call_answers_while_another_instance_holds_the_lock(tmp_path: Path) -> None:
    """Not the gate -- the other instance's own lock, which this one never waits on."""
    world: World = build_world(tmp_path, SCHEMA)
    inside = threading.Event()
    release = threading.Event()

    @world.tool
    def slow(ctx: Ctx) -> dict[str, bool]:
        """Hold one instance's lock for as long as the test says."""
        inside.set()
        assert release.wait(WAIT)
        return {"done": True}

    with world.instance(None) as first, world.instance(None) as second:
        caller = Caller(lambda: first.call("slow"))
        caller.start()
        assert inside.wait(WAIT)

        started = time.perf_counter()
        assert second.call("controller_run_sql", sql="SELECT 1")["rows"] == [[1]]
        assert time.perf_counter() - started < WAIT / 5

        release.set()
        caller.finish()


# Run in a child process, not on a thread of the test runner. The regression this
# guards against is a deadlock between a control read and an `inspect()` read on
# one connection, and the thread that loses it is blocked inside APSW holding the
# GIL -- so nothing in this process gets to notice, and a `join(timeout)` here
# would never come back. A child can be waited on with a timeout and killed.
RACE = """
import faulthandler
import sys
import threading
from pathlib import Path

from seahaven import World

# The child's own deadline, inside the parent's. It fires from a thread that
# needs no GIL, so it reports even when the interpreter is wedged, and what it
# prints is the two stacks that collided.
faulthandler.dump_traceback_later({deadline}, exit=True)

tmp = Path(sys.argv[1])
world = World(
    "race",
    "1.0.0",
    "CREATE TABLE notes (id TEXT PRIMARY KEY, body TEXT NOT NULL) STRICT;",
    state_format="seahaven.state/1",
    fixtures_dir=tmp / "fixtures",
    work_dir=tmp / "work",
)
instance = world.instance(None)
with instance.bulk() as ctx:
    ctx.db.executemany(
        "INSERT INTO notes (id, body) VALUES (?, 'b')", [(f"n{{n}}", ) for n in range({rows})]
    )

stop = threading.Event()


def read_until_stopped() -> None:
    # The handle an eval holds, read from another thread without the instance
    # lock, which is exactly what `inspect()` promises.
    db = instance.inspect()
    while not stop.is_set():
        assert len(db.rows("SELECT id, body FROM notes")) == {rows}


reader = threading.Thread(target=read_until_stopped, daemon=True)
reader.start()
for _ in range({reads}):
    counted = instance.call("controller_run_sql", sql="SELECT count(*) FROM notes")
    assert counted["rows"] == [[{rows}]]
stop.set()
reader.join({deadline})
assert not reader.is_alive()
instance.destroy()
print("finished")
"""


def test_a_control_read_answers_while_another_thread_reads_the_inspection_handle(
    tmp_path: Path,
) -> None:
    """The same instance, at the same time, on the two handles an eval really uses.

    Reads through `inspect()` do not take the instance lock -- that is the whole
    point of the handle -- so a control read cannot assume it is alone on
    whatever connection it runs on. It runs on the instance's own control
    connection, and this is what says so: before that, `run_statement` set the
    authorizer on the `inspect()` connection while another thread was stepping a
    cursor on it, and the process stopped, first call, every time.
    """
    script = tmp_path / "race.py"
    script.write_text(RACE.format(deadline=WAIT * 2, rows=200, reads=30))

    done = subprocess.run(
        # This interpreter, on a script this test just wrote, importing the
        # `seahaven` this run imported rather than whatever the child would have
        # found on its own.
        [sys.executable, str(script), str(tmp_path)],
        env={**os.environ, "PYTHONPATH": os.pathsep.join(entry for entry in sys.path if entry)},
        capture_output=True,
        text=True,
        timeout=WAIT * 12,
        check=False,
    )

    assert done.stdout.split() == ["finished"], done.stderr
    assert done.returncode == 0
