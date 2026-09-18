"""Shared fixtures: a frozen instant, a hardened database on it, and a small world.

`world` and `instance` here are *not* `seahaven.pytest_plugin`'s. The framework's
own tests need a throwaway world on `tmp_path` per test, which is what a world's
tests never need and what the plugin therefore does not offer; a conftest fixture
shadows a plugin one, so the two coexist. The plugin is exercised where a world
exercises it: `tests/test_pytest_plugin.py` through `pytester`, and
ProjectTracker's own suite.
"""

import copy
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from seahaven import instances
from seahaven.cli import main
from seahaven.clock import Clock
from seahaven.ctx import Ctx, InstanceInfo
from seahaven.db import Db, open_instance
from seahaven.errors import ToolError
from seahaven.handles import unbound
from seahaven.ids import Ids, instance_seed
from seahaven.instances import Instance
from seahaven.lint import Target
from seahaven.world import World

# `pytester`, which runs pytest inside pytest, is how the plugin is tested. It is
# a plugin pytest ships and does not enable by default, and `pytest_plugins` is
# only honoured in a conftest at the root of the collected tree -- this one.
pytest_plugins = ["pytester"]

WAIT = 5.0  # every thread test's patience, in seconds

# The small worlds the lints and the CLI are run against (`tests/worlds/README.md`).
WORLDS = Path(__file__).resolve().parent / "worlds"

# The packages of the two committed trees: `emporium` over `shop` and `payments`,
# and the composition rules' fixtures over `ledger`. Unlike the lint worlds, these
# are *imported* by the tests and by each other -- a host adds the `World` object
# a package exports, which is an ordinary import and not something discovery does
# -- so their source directories go on the path here, before any test module is
# collected, rather than in a fixture no import statement can wait for.
COMPOSITE_WORLDS = ("payments", "shop", "emporium", "ledger", "bazaar", "unsealed")
for _name in COMPOSITE_WORLDS:
    _src = str(WORLDS / _name / "src")
    if _src not in sys.path:
        sys.path.insert(0, _src)

# Milliseconds on purpose: a clock whose instant is a whole second hides the
# rounding mistakes that a world's canonical timestamps would trip over.
INSTANT = datetime(2024, 3, 5, 12, 0, 0, 123000, tzinfo=UTC)
INSTANT_ISO = "2024-03-05T12:00:00.123Z"

NOTES_SCHEMA = """
CREATE TABLE notes (
    id TEXT PRIMARY KEY,
    body TEXT NOT NULL,
    n INTEGER NOT NULL DEFAULT 0
) STRICT;
"""


class Caller(threading.Thread):
    """A call on another thread, whose failure is the test's failure.

    An exception in a bare `Thread` is a warning pytest prints and a test that
    passes anyway, which is no way to test a lock.
    """

    def __init__(self, run: Callable[[], Any]) -> None:
        super().__init__(daemon=True)
        self._run = run
        self.failure: BaseException | None = None

    def run(self) -> None:
        try:
            self._run()
        except BaseException as error:
            self.failure = error

    def finish(self, timeout: float = WAIT) -> None:
        self.join(timeout)
        assert not self.is_alive(), "the call never finished"
        if self.failure is not None:
            raise self.failure


@pytest.fixture
def clock() -> Clock:
    return Clock(INSTANT)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "state.sqlite"


@pytest.fixture
def seed() -> bytes:
    """The instance seed a connection's `random()` and `randomblob()` draw from."""
    return instance_seed("test")


@pytest.fixture
def db(db_path: Path, clock: Clock, seed: bytes) -> Iterator[Db]:
    """A writable instance connection, hardened the way a real instance is."""
    database = open_instance(db_path, clock, seed)
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def ctx(db: Db, clock: Clock, seed: bytes) -> Ctx:
    """An instance context as a call receives it, without an instance behind it.

    Everything in the call path takes a `Ctx` and nothing else, which is what
    lets it be tested before `instances.py` exists. Its `worlds` is unbound, as
    every context with no activation behind it is: reaching another world through
    it raises, and a chain run with it keeps it in every layer.
    """
    return Ctx(
        db=db,
        clock=clock,
        ids=Ids(seed),
        state={},
        instance=InstanceInfo(id="i_test", fixture=None, seed=seed),
        worlds=unbound(),
    )


@pytest.fixture(autouse=True)
def _process_wide_runtime_state() -> Iterator[None]:
    """Give every test the process state a fresh process would have.

    The concurrency gate and the once-per-process sweep are module-level by
    design (both are about the process, not about one world), so a test that
    changes either would otherwise change the next one.
    """
    gate = instances._gate
    yield
    instances._gate = gate
    instances._swept = False


def build_world(
    tmp_path: Path,
    schema: str = NOTES_SCHEMA,
    *,
    name: str = "testworld",
    version: str = "1.0.0",
    **options: Any,
) -> World:
    """A world on a throwaway directory, with the tools the instance tests drive.

    The tools are deliberately generic: `execute` lets a test drive any schema
    through a real `Instance.call`, which is the only way to exercise the
    transaction, the chain and the changeset the way a world does.
    """
    options.setdefault("fixtures_dir", tmp_path / "fixtures")
    options.setdefault("work_dir", tmp_path / "work")
    options.setdefault("state_format", "seahaven.state/1")
    world = World(name, version, schema, **options)
    register_test_tools(world)
    return world


def register_test_tools(world: World) -> None:
    """The small toolset every world in these tests shares."""

    @world.tool
    def execute(ctx: Ctx, sql: str) -> dict[str, int]:
        """Run one statement for its effect."""
        result = ctx.db.execute(sql)
        return {"rowcount": result.rowcount}

    @world.tool
    def rows(ctx: Ctx, sql: str) -> list[dict[str, Any]]:
        """Run one query and return every row."""
        return ctx.db.rows(sql)

    @world.tool
    def write_then_fail(ctx: Ctx, sql: str) -> dict[str, str]:
        """Write, then fail: what the per-call transaction is for."""
        ctx.db.execute(sql)
        raise Boom("it did not work out")

    @world.tool
    def crash(ctx: Ctx) -> None:
        """Fail with something that is not a `ToolError` at all."""
        raise ValueError("a bug in world code")

    @world.tool
    def mint(ctx: Ctx) -> dict[str, str]:
        """Draw from the instance's seeded id stream."""
        return {"id": ctx.ids.uuid(), "roll": str(ctx.ids.random.random())}

    @world.tool
    def now(ctx: Ctx) -> dict[str, str]:
        """The instance's clock, from Python and from SQL."""
        row = ctx.db.one("SELECT datetime('now') AS sql_now")
        assert row is not None
        return {"python": ctx.clock.iso(), "sql": str(row["sql_now"])}


def composable_world(
    name: str, *, version: str = "1.0.0", extra_schema: str = "", **options: Any
) -> World:
    """A world with a table of its own and the two tools that read and write it.

    Everything is named after the world -- the table, the tools -- so several of
    these compose into one flat surface with no prefix, and a test can tell which
    node's store a row landed in by which table holds it.

    `version` and `extra_schema` are what a fixture test varies: two worlds of one
    name at two versions with one schema, or at one version with two schemas, are
    the two halves of "the schema hash is the invalidation signal, not the
    version".
    """
    own = f"CREATE TABLE {name}_rows (id TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT;"
    options.setdefault("state_format", "seahaven.state/1")
    world = World(name, version, own + extra_schema, **options)

    @world.tool(name=f"{name}_write")
    def write(ctx: Ctx, value: str) -> dict[str, str]:
        """Write one row into this world's own store."""
        row = {"id": ctx.ids.uuid(), "value": value}
        ctx.db.execute(
            f"INSERT INTO {name}_rows (id, value) VALUES (?, ?)", row["id"], row["value"]
        )
        return row

    @world.tool(name=f"{name}_read")
    def read(ctx: Ctx) -> list[str]:
        """Every value in this world's own store, oldest first."""
        return [row["value"] for row in ctx.db.rows(f"SELECT value FROM {name}_rows ORDER BY id")]

    return world


class Boom(ToolError):
    """A world's own error, as every world defines its own."""

    def __init__(self, message: str) -> None:
        super().__init__("boom", message)


@pytest.fixture
def world(tmp_path: Path) -> World:
    """A world with the notes schema and the shared toolset."""
    return build_world(tmp_path)


@pytest.fixture
def instance(world: World) -> Iterator[Instance]:
    """A blank instance of that world, destroyed when the test ends."""
    with world.instance(None, now=INSTANT_ISO) as live:
        yield live


@pytest.fixture
def emporium_instance(tmp_path: Path) -> Iterator[Instance]:
    """A blank instance of the committed composite world, on its own directory.

    Four nodes from three worlds: `main`, `payments`, `payments_eu` and `shop`
    (`tests/worlds/README.md`). Imported here and not at the top because the
    path it is found on is the one this module sets up above.

    Do not use this fixture from a module that declares
    `pytestmark = pytest.mark.usefixtures("isolated_imports")`: that fixture
    purges a world imported during a test, and `emporium.world` has to stay the
    same object for the whole run because node identity is object identity.
    """
    import emporium

    world = copy.copy(emporium.world)
    world.work_dir = tmp_path / "work"
    with world.instance(None, now=INSTANT_ISO) as live:
        yield live


@pytest.fixture
def isolated_imports() -> Iterator[None]:
    """Undo what a test's imports of a *world* did to the process.

    The CLI and the lints import worlds, and discovery puts their project roots
    on `sys.path` to do it. Both outlive the test that caused them, and a world
    left in `sys.modules` would make the next test's import of it a no-op -- which
    is exactly the thing SH301 is asking about. Declared with
    `pytestmark = pytest.mark.usefixtures("isolated_imports")` by every module
    that imports a world *during a test*.

    Only worlds are purged. A blanket sweep of everything imported during the
    test would also evict a standard-library or third-party module that happened
    to be imported lazily inside it, and a module re-imported behind objects that
    still hold its old classes fails in a way nobody enjoys debugging. A world
    lives under `tests/worlds/` or in a temporary directory; nothing else does.

    The composite worlds above are the exception where a test module imports one
    directly: those are imported at collection time, before any test runs, so
    they are never in the set this purges, and their `World` objects stay the
    same objects for the run -- which they have to, because node identity is
    object identity.
    """
    path = list(sys.path)
    modules = set(sys.modules)
    try:
        yield
    finally:
        sys.path[:] = path
        for name in set(sys.modules) - modules:
            if _is_a_test_world(sys.modules[name]):
                del sys.modules[name]


def _is_a_test_world(module: object) -> bool:
    """Whether a module was loaded from somewhere a test put it.

    A namespace package (`fixtures_src`, in a scaffold) has no `__file__` and
    only a `__path__`, so both are asked.
    """
    roots = (str(WORLDS), tempfile.gettempdir())
    places = [getattr(module, "__file__", None), *getattr(module, "__path__", [])]
    return any(place is not None and str(place).startswith(roots) for place in places)


def stub_target(world: World, package_dir: Path, imported: frozenset[str] = frozenset()) -> Target:
    """A lint target over a directory of source files, with no package to import.

    `lint.ddl` and `lint.code` read a world, its files and (for SH205) its tool
    registry, and nothing else: a `Target` over a directory of `*.sql` and `*.py`
    written by the test is the whole of what they need, and is what keeps a
    committed world per malformed table out of `tests/worlds/`.
    """
    module = ModuleType("stub")
    module.__path__ = [str(package_dir)]
    return Target(world=world, package=module, imported=imported)


@dataclass(frozen=True)
class CliResult:
    """What `seahaven` left behind: its exit code and its two streams."""

    code: int
    out: str
    err: str

    @property
    def lines(self) -> list[str]:
        return self.out.splitlines()


def run_cli(capsys: pytest.CaptureFixture[str], *argv: str) -> CliResult:
    """Run the CLI in process, the way its entry point does.

    In process rather than as a subprocess: `main` is the entry point, its return
    value is the exit code, and a subprocess would test the console script the
    installer writes instead of anything here. The one test that does use a
    subprocess is the scaffold's own `pytest` run, which has to be one.
    """
    code = main(list(argv))
    captured = capsys.readouterr()
    return CliResult(code=code, out=captured.out, err=captured.err)
