"""The world-code rules: what a wall clock, a coin flip and a silent tool look like.

Three shapes of test. The call rules and the two composition rules that read
source are exercised over one-module worlds written into `tmp_path`, because what
is being tested is the reading of a line of Python and there are a lot of lines to
read. SH205 is exercised over a real registered tool, because a description is a
property of the registry and not of the source. SH206 is exercised over the
committed `bazaar` and `emporium` trees, because a stale description is a fact
about a *composition* -- which node owns the tool, and what the prefix renamed it
to -- and there is no honest way to have one without the packages.
"""

from dataclasses import replace
from pathlib import Path
from types import ModuleType

import payments
import pytest

from seahaven.cli import discover
from seahaven.ctx import Ctx
from seahaven.errors import WorldBug
from seahaven.lint import Finding, Target
from seahaven.lint import code as code_lint
from seahaven.world import World
from tests.conftest import NOTES_SCHEMA, WORLDS, build_world, stub_target

pytestmark = pytest.mark.usefixtures("isolated_imports")


def findings(
    tmp_path: Path, source: str, *, module: str = "tools/notes.py", world: World | None = None
) -> list[Finding]:
    """Every code finding for one module of a world that has no tools."""
    package_dir = tmp_path / "pkg"
    path = package_dir / module
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    if world is None:
        world = World(
            "linted",
            "1.0.0",
            NOTES_SCHEMA,
            fixtures_dir=tmp_path / "fixtures",
            work_dir=tmp_path / "work",
            state_format="seahaven.state/1",
        )
    return code_lint.run(stub_target(world, package_dir))


def host(tmp_path: Path) -> World:
    """A world with one child, so `payments` is a name `ctx.worlds` really has.

    Added with an empty allow list: what SH209 asks about is the registered
    `name=`, and a child that contributes nothing cannot bring a finding of
    another rule with it.
    """
    world = build_world(tmp_path)
    world.add_world(payments.world, name="payments", tool_allow_list=[])
    return world


def target_for(name: str) -> Target:
    found = discover(None, WORLDS / name)
    return Target(found.world, found.package, found.imported)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("from datetime import datetime\nx = datetime.now()\n", id="datetime.now"),
        pytest.param("import datetime\nx = datetime.datetime.now()\n", id="datetime.datetime.now"),
        pytest.param("from datetime import datetime\nx = datetime.utcnow()\n", id="utcnow"),
        pytest.param("from datetime import date\nx = date.today()\n", id="date.today"),
        pytest.param("import time\nx = time.time()\n", id="time.time"),
        pytest.param("import time\nx = time.monotonic()\n", id="time.monotonic"),
        pytest.param("import time\nx = time.perf_counter()\n", id="time.perf_counter"),
    ],
)
def test_every_wall_clock_read_is_sh201(tmp_path: Path, source: str) -> None:
    (finding,) = findings(tmp_path, source)
    assert finding.code == "SH201"
    assert finding.severity == "warning"
    assert "ctx.clock" in finding.fix


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "from datetime import datetime as dt\nx = dt.now()\n", id="datetime renamed on import"
        ),
        pytest.param("import time as clock\nx = clock.time()\n", id="time renamed on import"),
        pytest.param("from time import time\nx = time()\n", id="the function imported by name"),
    ],
)
def test_a_wall_clock_read_through_an_alias_is_still_sh201(tmp_path: Path, source: str) -> None:
    """A rule that only sees one spelling is a rule an author steps around by accident."""
    (finding,) = findings(tmp_path, source)
    assert finding.code == "SH201"


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("def t(ctx):\n    return ctx.clock.iso()\n", id="ctx.clock.iso"),
        pytest.param("def t(ctx):\n    return ctx.clock.now()\n", id="ctx.clock.now"),
        pytest.param("def t(ctx):\n    return ctx.ids.uuid()\n", id="ctx.ids.uuid"),
        pytest.param("def t(ctx):\n    return ctx.ids.random.random()\n", id="ctx.ids.random"),
    ],
)
def test_the_endorsed_spellings_are_clean(tmp_path: Path, source: str) -> None:
    """`ctx.clock.now()` is the instance's clock and must never read as the machine's."""
    assert findings(tmp_path, source) == []


def test_a_wall_clock_read_in_middleware_is_not_reported(tmp_path: Path) -> None:
    """Timing a call is a real wall clock doing a real job."""
    source = "import time\nx = time.perf_counter()\n"
    assert findings(tmp_path, source, module="middleware/timing.py") == []
    assert findings(tmp_path, source, module="middleware/deeper/timing.py") == []


def test_a_wall_clock_read_outside_middleware_is_reported(tmp_path: Path) -> None:
    """The exemption is the directory, not the file name."""
    source = "import time\nx = time.perf_counter()\n"
    assert [f.code for f in findings(tmp_path, source, module="helpers/timing.py")] == ["SH201"]


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("import random\n", id="import random"),
        pytest.param("from random import choice\n", id="from random import"),
        pytest.param("import random as r\n", id="import random renamed"),
    ],
)
def test_importing_random_is_sh203(tmp_path: Path, source: str) -> None:
    (finding,) = findings(tmp_path, source)
    assert finding.code == "SH203"
    assert "ctx.ids" in finding.fix


def test_a_call_into_random_is_sh203(tmp_path: Path) -> None:
    """The import and the call are both reported: the fix is the same for both."""
    codes = [f.code for f in findings(tmp_path, "import random\nx = random.random()\n")]
    assert codes == ["SH203", "SH203"]


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("import uuid\nx = uuid.uuid4()\n", id="uuid4"),
        pytest.param("import uuid\nx = uuid.uuid1()\n", id="uuid1"),
        pytest.param("from uuid import uuid4\nx = uuid4()\n", id="uuid4 imported by name"),
    ],
)
def test_uuid_from_the_entropy_pool_is_sh203(tmp_path: Path, source: str) -> None:
    (finding,) = findings(tmp_path, source)
    assert finding.code == "SH203"


def test_importing_uuid_alone_is_not_a_finding(tmp_path: Path) -> None:
    """`uuid.UUID` is how `ctx.ids.uuid()`'s own output is parsed."""
    assert findings(tmp_path, "import uuid\nx = uuid.UUID(int=0, version=4)\n") == []


def test_a_module_that_does_not_parse_is_left_to_the_import_error(tmp_path: Path) -> None:
    assert findings(tmp_path, "def broken(:\n") == []


def test_a_tool_with_no_description_is_sh205(tmp_path: Path) -> None:
    world = build_world(tmp_path)

    @world.tool(description="")
    def silent(ctx: Ctx) -> None:
        """Registered with an empty description."""

    found = [f for f in code_lint.run(stub_target(world, tmp_path)) if f.code == "SH205"]
    assert len(found) == 1
    assert "silent" in found[0].message
    assert found[0].severity == "warning"
    assert "docstring" in found[0].fix


def test_a_tool_with_a_docstring_is_clean(tmp_path: Path) -> None:
    """Every tool in the shared toolset has one, and none of them is reported."""
    world = build_world(tmp_path)
    assert [f for f in code_lint.run(stub_target(world, tmp_path)) if f.code == "SH205"] == []


def test_a_control_tool_is_never_sh205(tmp_path: Path) -> None:
    """The framework's own two are never listed and never reach an agent.

    Both have descriptions, so the only way to ask whether `control` is what
    excludes them is to put one without a description in the registry, which
    `world.tool` refuses by name. Hence the private dict: the alternative is a
    branch nothing exercises.
    """
    world = build_world(tmp_path)
    silent = replace(world.tools["controller_run_sql"], description="")
    world._tools[silent.name] = silent
    assert [f for f in code_lint.run(stub_target(world, tmp_path)) if f.code == "SH205"] == []


def test_sh205_points_at_the_function(tmp_path: Path) -> None:
    world = build_world(tmp_path)

    @world.tool(description="")
    def silent(ctx: Ctx) -> None:
        """Registered with an empty description."""

    (found,) = [f for f in code_lint.run(stub_target(world, tmp_path)) if f.code == "SH205"]
    assert found.path == Path(__file__)
    assert found.line is not None


def test_the_tidy_world_has_no_code_findings() -> None:
    found = discover(None, WORLDS / "tidy")
    assert code_lint.run(Target(found.world, found.package, found.imported)) == []


def test_the_messy_world_has_every_code_finding() -> None:
    """A wall clock, `uuid4`, `random` twice and an undescribed tool, none from middleware."""
    found = discover(None, WORLDS / "messy")
    reported = code_lint.run(Target(found.world, found.package, found.imported))
    assert sorted({f.code for f in reported}) == ["SH201", "SH203", "SH205"]
    # `middleware/timing.py` reads `time.perf_counter()` twice and is exempt.
    assert not any("timing" in f.path.name for f in reported)
    # `tools/orphan.py` is never imported, but it is still world code on disk.
    assert not any(f.code == "SH201" and "orphan" in f.path.name for f in reported)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("def t(self):\n    return self.time.time()\n", id="an attribute named time"),
        pytest.param("def t(clocks):\n    return clocks.date.today()\n", id="one named date"),
    ],
)
def test_a_chain_that_merely_ends_in_a_wall_clock_name_is_not_sh201(
    tmp_path: Path, source: str
) -> None:
    """Resolving the root is the point; matching the tail would undo it."""
    assert findings(tmp_path, source) == []


def test_a_relative_import_of_the_worlds_own_random_is_not_sh203(tmp_path: Path) -> None:
    """A world may have a `random.py` of its own, and `.random` is not the stdlib's."""
    assert findings(tmp_path, "from .random import seeded\n") == []


def test_the_stdlib_random_is_still_sh203_beside_it(tmp_path: Path) -> None:
    assert [f.code for f in findings(tmp_path, "from random import choice\n")] == ["SH203"]


def test_a_world_that_is_one_module_is_refused_by_the_target(tmp_path: Path) -> None:
    """A `Target` built by hand gets a sentence, never an `AttributeError`."""
    module = ModuleType("flat")
    world = build_world(tmp_path)
    with pytest.raises(WorldBug, match="a world is a package"):
        _ = Target(world=world, package=module, imported=frozenset()).package_dir


# --- SH208: an instance made inside a call -----------------------------------


@pytest.mark.parametrize(
    "module",
    [
        pytest.param("tools/notes.py", id="a tools module"),
        pytest.param("middleware/gate.py", id="a middleware module"),
        pytest.param("tools.py", id="a one-module tools layer"),
    ],
)
def test_making_an_instance_in_a_registering_module_is_sh208(tmp_path: Path, module: str) -> None:
    (finding,) = findings(tmp_path, "def t(ctx):\n    return world.instance(None)\n", module=module)
    assert finding.code == "SH208"
    assert finding.severity == "error"
    assert finding.line == 2
    assert "ctx.worlds" in finding.fix


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("def t(ctx):\n    return other.world.instance(None)\n", id="through a module"),
        pytest.param("def t(self):\n    return self._world.instance(None)\n", id="through self"),
    ],
)
def test_every_receiver_of_instance_is_sh208(tmp_path: Path, source: str) -> None:
    """The receiver is whatever the module called its `World`; the mistake is the call."""
    assert [f.code for f in findings(tmp_path, source)] == ["SH208"]


def test_making_an_instance_outside_the_registering_directories_is_not_sh208(
    tmp_path: Path,
) -> None:
    """A generator script and a helper are not a call; `world.instance(...)` is their door."""
    source = "def t(ctx):\n    return world.instance(None)\n"
    assert findings(tmp_path, source, module="fixtures_src/generate.py") == []
    assert findings(tmp_path, source, module="helpers.py") == []


def test_naming_an_instance_without_calling_it_is_not_sh208(tmp_path: Path) -> None:
    assert findings(tmp_path, "maker = world.instance\n") == []


# --- SH209: a child name nothing registered ----------------------------------


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("def t(ctx):\n    return ctx.worlds.paymnets\n", id="attribute"),
        pytest.param('def t(ctx):\n    return ctx.worlds["paymnets"]\n', id="subscript"),
    ],
)
def test_a_child_name_no_add_world_registered_is_sh209(tmp_path: Path, source: str) -> None:
    (finding,) = findings(tmp_path, source, world=host(tmp_path))
    assert finding.code == "SH209"
    assert finding.severity == "error"
    assert "'paymnets'" in finding.message
    assert "payments" in finding.fix


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("def t(ctx):\n    return ctx.worlds.payments.call('x')\n", id="attribute"),
        pytest.param('def t(ctx):\n    return ctx.worlds["payments"].db\n', id="subscript"),
    ],
)
def test_a_registered_child_name_is_not_sh209(tmp_path: Path, source: str) -> None:
    assert findings(tmp_path, source, world=host(tmp_path)) == []


def test_a_grandchild_reached_through_a_handle_is_not_sh209(tmp_path: Path) -> None:
    """`ctx.worlds.payments.worlds.<name>` is the child's registrations, not this world's."""
    source = "def t(ctx):\n    return ctx.worlds.payments.worlds.anything\n"
    assert findings(tmp_path, source, world=host(tmp_path)) == []


def test_a_computed_child_name_is_not_sh209(tmp_path: Path) -> None:
    """A lint reads literals; a name built at run time is outside what it can see."""
    source = "def t(ctx, which):\n    return ctx.worlds[which]\n"
    assert findings(tmp_path, source, world=host(tmp_path)) == []


def test_a_dunder_on_the_container_is_not_a_child_name(tmp_path: Path) -> None:
    """`__getattr__` is consulted only for what ordinary lookup does not find."""
    source = "def t(ctx):\n    return ctx.worlds.__class__\n"
    assert findings(tmp_path, source, world=host(tmp_path)) == []


def test_a_world_that_adds_nothing_says_so_in_the_fix(tmp_path: Path) -> None:
    (finding,) = findings(tmp_path, "def t(ctx):\n    return ctx.worlds.payments\n")
    assert finding.code == "SH209"
    assert "adds no worlds" in finding.fix


# --- SH206: a description a prefix left stale --------------------------------


def test_a_prefixed_worlds_stale_description_is_sh206() -> None:
    """`ledger`'s two tools name each other, and `bazaar` prefixes both."""
    found = [f for f in code_lint.run(target_for("bazaar")) if f.code == "SH206"]
    assert {f.severity for f in found} == {"warning"}
    assert sorted(f.message.split("; the description is ")[0] for f in found) == [
        "'ledger_list_entries' describes 'post_entry', which node 'ledger' contributes as "
        "'ledger_post_entry'",
        "'ledger_post_entry' describes 'list_entries', which node 'ledger' contributes as "
        "'ledger_list_entries'",
    ]
    assert all("tool_prefix" in f.fix for f in found)


def test_sh206_is_anchored_on_the_add_world_and_names_the_added_worlds_file() -> None:
    """The text is `ledger`'s and the edit is `bazaar`'s, so the finding points at the edit.

    An installed vendor world's description is inside `site-packages`: a file the
    host's author cannot change and should not, and the only absolute path in an
    otherwise project-relative report.
    """
    (finding, _) = sorted(
        (f for f in code_lint.run(target_for("bazaar")) if f.code == "SH206"),
        key=lambda f: f.message,
    )
    assert finding.path.name == "world.py"
    assert finding.path.parent.name == "bazaar"
    assert finding.line is None
    assert "entries.py:" in finding.message


def test_a_composition_whose_descriptions_name_nothing_is_not_sh206() -> None:
    """`emporium` prefixes both payments accounts, and neither describes the other."""
    assert [f for f in code_lint.run(target_for("emporium")) if f.code == "SH206"] == []


def prefixed_pair(tmp_path: Path, alpha_doc: str) -> list[Finding]:
    """A host prefixing one added world whose `alpha` describes itself however it likes."""
    child = build_world(tmp_path / "added", name="added")

    @child.tool
    def alpha(ctx: Ctx) -> None:
        pass

    @child.tool
    def beta(ctx: Ctx) -> None:
        """The other one."""

    child._tools["alpha"] = replace(child.tools["alpha"], description=alpha_doc)
    host = build_world(tmp_path, name="host")
    host.add_world(child, name="child", tool_prefix="pay_", tool_allow_list=["alpha", "beta"])
    return [f for f in code_lint.run(stub_target(host, tmp_path)) if f.code == "SH206"]


def test_a_description_that_names_a_sibling_is_sh206(tmp_path: Path) -> None:
    (finding,) = prefixed_pair(tmp_path, "Call beta first.")
    assert "'beta'" in finding.message and "'pay_beta'" in finding.message
    assert "tool_prefix" in finding.fix


@pytest.mark.parametrize(
    "description",
    [
        pytest.param("Call pay_beta first.", id="the name the agent really sees"),
        pytest.param("Call betagram first.", id="a longer name that starts with it"),
        pytest.param("Call alphabeta first.", id="a longer name that ends with it"),
    ],
)
def test_a_mention_that_is_not_the_sibling_is_not_sh206(tmp_path: Path, description: str) -> None:
    """A word boundary, and `_` is a word character: the prefixed name is not the bare one."""
    assert prefixed_pair(tmp_path, description) == []
