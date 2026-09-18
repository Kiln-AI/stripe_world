"""The world object: construction, the three registration verbs, and what each of them refuses."""

import copy
import importlib.resources
import importlib.util
import os
import subprocess
import sys
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import IO, Any

import pytest

import seahaven
from seahaven.call import Call, Handler
from seahaven.composition import epoch
from seahaven.ctx import Ctx
from seahaven.errors import WorldBug
from seahaven.tool import Tool
from seahaven.world import CONTROL_TOOL_NAMES, World, sql_files

# `src/`, for the one test that runs `seahaven` in a subprocess of its own: it
# must import the tree under test, not whatever is installed.
SRC_DIR = str(Path(seahaven.__file__).parent.parent)

SCHEMA = "CREATE TABLE notes (id TEXT PRIMARY KEY, body TEXT NOT NULL) STRICT;"

# A fixed instant, so a fixture frozen in a test carries a clock somebody chose.
NOW = "2026-01-01T00:00:00.000Z"

# Every world pins a state format; none of the tests below is about which one.
PIN = "seahaven.state/1"

MODULE_SOURCE = f"""
from seahaven import World

world = World("generated", "1.0.0", {SCHEMA!r}, state_format={PIN!r})
"""


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World("testworld", "1.0.0", SCHEMA, fixtures_dir=tmp_path / "fixtures", state_format=PIN)


def echo(ctx: Ctx, word: str) -> dict[str, str]:
    """Echo one word."""
    return {"word": word}


def registered(world: World) -> list[str]:
    """The names this world registered, in order.

    Every world also carries the framework's two control tools, registered at
    construction; they are not what these tests are about.
    """
    return [name for name, tool in world.tools.items() if not tool.control]


def world_built_in(module_path: Path) -> World:
    """Build a world from a module on disk, so the derivation has a real file to walk up from."""
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(MODULE_SOURCE)
    spec = importlib.util.spec_from_file_location(
        f"generated_{module_path.parent.name}", module_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.world


def test_a_world_is_its_name_its_version_and_its_schema(tmp_path: Path) -> None:
    """`name` and `version` are informational, and go into the sidecar and the metadata."""
    world = World("projecttracker", "1.2.0", SCHEMA, fixtures_dir=tmp_path, state_format=PIN)

    assert (world.name, world.version, world.schema) == ("projecttracker", "1.2.0", SCHEMA)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        ".",
        "..",
        "../..",
        "../escaped",
        "a/b",
        "a/",
        "/abs",
        "a\\b",
        ".hidden",
        "a/../b",
        "a\x00b",
        # `names.why_not_a_name`'s clauses, one case each: the drive letter that
        # only Windows reads as a path, a character outside the charset, the
        # edges Windows strips, a device name, and the limit.
        "C:x",
        "café",
        " leading",
        "trailing ",
        "trailing.",
        "con",
        "aux.sqlite",
        "x" * 129,
    ],
)
def test_a_world_name_that_is_not_a_directory_name_is_refused(tmp_path: Path, bad: str) -> None:
    """The name becomes a path component, and `_open_child`'s `O_NOFOLLOW` does not stop a `..`.

    `World("..")` used to put an instance in the working root beside the
    per-process directories, and `World("../..")` a live `state.sqlite` outside
    the root entirely, where the sweep never looks.
    """
    with pytest.raises(WorldBug, match="not a world name"):
        World(bad, "1.0.0", SCHEMA, fixtures_dir=tmp_path, state_format=PIN)


def test_a_refused_world_name_says_which_clause_it_broke_and_what_the_rule_is(
    tmp_path: Path,
) -> None:
    """The report this rule generates is "my world name stopped working"; this answers it."""
    with pytest.raises(WorldBug) as raised:
        World("café", "1.0.0", SCHEMA, fixtures_dir=tmp_path, state_format=PIN)

    message = str(raised.value)
    assert "'é'" in message
    assert "1 to 128 characters" in message
    # The clause names what is wrong and the rule names the charset, each once: a
    # refusal that spells the alphabet out twice is a refusal nobody finishes.
    assert message.count("letters, digits") == 1


@pytest.mark.parametrize(
    "name", ["payments", "my-world", "my_world", "my world", "World2", "v1.2.3", "projecttracker"]
)
def test_the_names_people_use_are_world_names(tmp_path: Path, name: str) -> None:
    assert World(name, "1.0.0", SCHEMA, fixtures_dir=tmp_path, state_format=PIN).name == name


def test_a_world_name_is_refused_before_anything_is_built(tmp_path: Path) -> None:
    """The name is checked first, so a bad name reads as a bad name and not as bad DDL."""
    with pytest.raises(WorldBug, match="not a world name"):
        World(
            "..",
            "1.0.0",
            "CREATE TALBE notes (id TEXT PRIMARY KEY);",
            fixtures_dir=tmp_path,
            state_format=PIN,
        )


def test_a_dot_inside_a_world_name_is_fine(tmp_path: Path) -> None:
    """Only a *leading* dot is refused: `..` is the traversal, `a.b` is a name."""
    assert World("a.b", "1.0.0", SCHEMA, fixtures_dir=tmp_path, state_format=PIN).name == "a.b"


def test_the_schema_hash_ignores_layout_and_nothing_else(tmp_path: Path) -> None:
    spaced = "CREATE   TABLE notes\n\t(id TEXT PRIMARY KEY,\n    body TEXT NOT NULL)\n STRICT;\n"
    renamed = SCHEMA.replace("body", "text")

    world = World("w", "1.0.0", SCHEMA, fixtures_dir=tmp_path, state_format=PIN)

    assert (
        world.schema_hash
        == World("w", "1.0.0", spaced, fixtures_dir=tmp_path, state_format=PIN).schema_hash
    )
    assert (
        world.schema_hash
        != World("w", "1.0.0", renamed, fixtures_dir=tmp_path, state_format=PIN).schema_hash
    )
    assert world.schema == SCHEMA


def test_a_world_whose_ddl_does_not_execute_cannot_be_built(tmp_path: Path) -> None:
    with pytest.raises(WorldBug) as raised:
        World(
            "w",
            "1.0.0",
            "CREATE TALBE notes (id TEXT PRIMARY KEY);",
            fixtures_dir=tmp_path,
            state_format=PIN,
        )

    assert "'w'" in str(raised.value)
    assert 'near "TALBE": syntax error' in str(raised.value)  # SQLite's own words, kept


def test_the_ddl_rules_belong_to_the_lint_and_not_to_construction(tmp_path: Path) -> None:
    """A table with no `STRICT` and no primary key is a lint finding, not a broken world."""
    World("w", "1.0.0", "CREATE TABLE notes (id TEXT);", fixtures_dir=tmp_path, state_format=PIN)


def test_an_explicit_fixtures_dir_is_used_as_given(tmp_path: Path) -> None:
    assert World(
        "w", "1.0.0", SCHEMA, fixtures_dir=tmp_path / "elsewhere", state_format=PIN
    ).fixtures_dir == (tmp_path / "elsewhere")


def test_fixtures_are_at_the_project_root_of_a_src_layout(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'generated'\n")

    world = world_built_in(tmp_path / "src" / "generated" / "world.py")

    assert world.fixtures_dir == tmp_path / "fixtures"


def test_an_installed_package_has_its_fixtures_beside_it(tmp_path: Path) -> None:
    """No `pyproject.toml` above the module, as in a wheel. Construction still succeeds."""
    site_packages = tmp_path / "site-packages"

    world = world_built_in(site_packages / "generated" / "world.py")

    assert world.fixtures_dir == site_packages / "fixtures"


def test_a_copy_keeps_the_registrations_and_freezes_where_it_is_pointed(tmp_path: Path) -> None:
    """`copy.copy(world)` is how a caller says "this world, writing somewhere else".

    The reason it needs a `__copy__` at all is the instance manager: a `World`
    makes one lazily and hands *itself* to every instance it creates, so a plain
    attribute copy -- which would carry the original's manager -- makes instances
    belonging to the original and freezes them back into the original's fixtures
    directory, silently. A world's fixture test rebuilds the committed fixtures
    into a temporary directory through this, and that is the failure it would
    otherwise get.
    """
    world = World(
        "w",
        "1.0.0",
        SCHEMA,
        fixtures_dir=tmp_path / "here",
        work_dir=tmp_path / "work",
        state_format=PIN,
    )
    world.tool(echo)
    (tmp_path / "here").mkdir()
    (tmp_path / "there").mkdir()
    # A manager on the original before the copy is taken: the case that fails.
    with world.instance(None, now=NOW):
        pass

    elsewhere = copy.copy(world)
    elsewhere.fixtures_dir = tmp_path / "there"
    with elsewhere.instance(None, now=NOW) as live:
        assert live.call("echo", word="hi") == {"word": "hi"}
        frozen = live.freeze("only", "The one fixture, frozen by a copy.")

    assert frozen.dir == tmp_path / "there" / "only"
    assert [fixture.id for fixture in elsewhere.fixtures()] == ["only"]
    assert world.fixtures() == []
    assert world.fixtures_dir == tmp_path / "here"


def test_a_copy_registers_on_itself_alone(tmp_path: Path) -> None:
    """All three registries are copied, not shared: nothing added to a copy reaches back."""

    def passthrough(ctx: Ctx, call: Call, next_: Handler) -> Any:
        return next_(ctx, call)

    def startup(ctx: Ctx) -> None:
        pass

    world = World("w", "1.0.0", SCHEMA, fixtures_dir=tmp_path, state_format=PIN)
    world.tool(echo)

    elsewhere = copy.copy(world)
    elsewhere.tool(echo, name="echo_twice")
    elsewhere.middleware(passthrough)
    elsewhere.instance_startup(startup)

    assert "echo_twice" in elsewhere.tools
    assert "echo_twice" not in world.tools
    assert "echo" in elsewhere.tools
    assert elsewhere.middlewares == (passthrough,)
    assert world.middlewares == ()
    assert [hook.fn for hook in elsewhere.startup_hooks] == [startup]
    assert world.startup_hooks == ()


def test_a_copy_carries_the_state_formats(tmp_path: Path) -> None:
    """The fifth registry, snapshotted like the other four."""
    world = World("w", "1.0.0", SCHEMA, fixtures_dir=tmp_path, state_format=PIN)

    @world.state_format("acme.state/1")
    def acme(world: World, instance: Any) -> dict[str, Any]:
        """A format the copy is expected to answer to as well."""
        return {"acme": True}

    twin = copy.copy(world)
    world.state_format("acme.state/2")(acme)

    assert twin.resolve_state_format("acme.state/1") is acme
    with pytest.raises(WorldBug, match="has no state format"):
        twin.resolve_state_format("acme.state/2")


def test_registering_a_state_format_does_not_invalidate_a_seal(tmp_path: Path) -> None:
    """A format is not part of a composition, so nothing about the tree changes when one lands.

    Every other registration verb bumps the epoch, which reseals every world in
    the process on its next use. This one must not, or an extension registering
    a format would reseal a tree that has live instances.
    """
    world = World("w", "1.0.0", SCHEMA, fixtures_dir=tmp_path, state_format=PIN)
    world.tool(echo)
    sealed = world.composition()
    before = epoch()

    @world.state_format("acme.state/1")
    def acme(world: World, instance: Any) -> dict[str, Any]:
        """Registered after the seal, and the seal stands."""
        return {}

    assert epoch() == before
    assert world.composition() is sealed


def test_a_copy_is_a_snapshot_the_original_cannot_reach_either(tmp_path: Path, ctx: Ctx) -> None:
    """The other direction, which the docstring and the spec both promise.

    `world.py`'s module docstring says registration is open for the life of the
    world and that the registry and the chain are read at call time. A copy is
    the one place that stops: it holds the three registries as they were, so a
    world is copied once import-time registration is done and not before.
    """

    ran: list[str] = []

    def passthrough(ctx: Ctx, call: Call, next_: Handler) -> Any:
        ran.append("middleware")
        return next_(ctx, call)

    def startup(ctx: Ctx) -> None:
        pass

    world = World("w", "1.0.0", SCHEMA, fixtures_dir=tmp_path, state_format=PIN)
    world.tool(echo)
    elsewhere = copy.copy(world)

    world.tool(echo, name="echo_twice")
    world.middleware(passthrough)
    world.instance_startup(startup)

    assert "echo_twice" not in elsewhere.tools
    assert elsewhere.middlewares == ()
    assert elsewhere.startup_hooks == ()
    # The chain is the copy's own tree sealed, and a middleware registered on the
    # original after the copy was taken is not in it. Asserted by running it: the
    # chain is rebuilt by every reseal, so it is the same chain, not the same
    # object.
    call = Call("echo", {"word": "hi"}, elsewhere.tools["echo"])
    assert elsewhere.chain(ctx.with_call(call), call) == {"word": "hi"}
    assert ran == []


def test_the_working_directory_and_untracked_tables_are_carried(tmp_path: Path) -> None:
    world = World(
        "w",
        "1.0.0",
        SCHEMA,
        fixtures_dir=tmp_path,
        work_dir=tmp_path / "work",
        untracked_tables=["audit", "sessions"],
        state_format=PIN,
    )

    assert world.work_dir == tmp_path / "work"
    assert world.untracked_tables == ("audit", "sessions")
    # `None` is the default: a per-process temporary directory, resolved later.
    assert World("w", "1.0.0", SCHEMA, fixtures_dir=tmp_path, state_format=PIN).work_dir is None


def test_the_description_is_kept_as_given_and_carried_by_a_copy(tmp_path: Path) -> None:
    """A free string, unvalidated, and `None` when it is not given.

    It is the one-line description the OpenEnv metadata publishes and nothing
    else reads it. Unlike `name` it never becomes a path or an identifier, so
    there is no rule to enforce -- an empty string is accepted here and falls
    back at publication, which `tests/test_env.py` pins.
    """
    world = World(
        "w", "1.0.0", SCHEMA, fixtures_dir=tmp_path, description="  A world.  ", state_format=PIN
    )

    assert world.description == "  A world.  "
    assert copy.copy(world).description == "  A world.  "
    assert World("w", "1.0.0", SCHEMA, fixtures_dir=tmp_path, state_format=PIN).description is None
    assert (
        World(
            "w", "1.0.0", SCHEMA, fixtures_dir=tmp_path, description="", state_format=PIN
        ).description
        == ""
    )


def test_the_registry_is_ordered_and_read_only(world: World) -> None:
    world.tool(echo)

    @world.tool
    def second(ctx: Ctx) -> dict:
        """Second."""
        return {}

    assert registered(world) == ["echo", "second"]
    with pytest.raises(TypeError):
        world.tools["third"] = world.tools["echo"]  # ty: ignore[invalid-assignment]


def test_the_registry_inverted_by_function_holds_every_tool_built_from_one(
    world: World,
) -> None:
    """What `ctx.worlds.<name>.call(fn)` resolves through, and why it is multi-valued."""
    world.tool(echo)
    world.tool(Tool.from_function(echo, name="echo_twice"))

    @world.tool
    def second(ctx: Ctx) -> dict:
        """Second."""
        return {}

    assert world.tools_by_fn[echo] == (world.tools["echo"], world.tools["echo_twice"])
    assert world.tools_by_fn[second] == (world.tools["second"],)
    with pytest.raises(TypeError):
        world.tools_by_fn[echo] = ()  # ty: ignore[invalid-assignment]


def test_the_inverted_registry_holds_no_control_tool(world: World) -> None:
    """They are the framework's, not a world's surface, and no `call` serves them."""
    control_tools = [world.tools[name] for name in CONTROL_TOOL_NAMES]

    assert [tool for tools in world.tools_by_fn.values() for tool in tools] == []
    assert all(tool.fn not in world.tools_by_fn for tool in control_tools)


def test_a_function_that_cannot_be_a_dictionary_key_cannot_be_a_tool(world: World) -> None:
    """The inverted registry keys on it, so the whole registration is refused, not half of it."""

    @dataclass  # unhashable: dataclasses drop `__hash__` unless frozen or eq=False
    class Callable_:
        label: str

        def __call__(self, ctx: Ctx, word: str) -> dict[str, str]:
            """Echo."""
            return {"word": word}

    built = Tool.from_function(Callable_("one"), name="echo_object")

    with pytest.raises(WorldBug, match="cannot be hashed"):
        world.tool(built)

    assert "echo_object" not in world.tools


def test_a_copy_snapshots_the_inverted_registry_too(tmp_path: Path) -> None:
    world = World("w", "1.0.0", SCHEMA, fixtures_dir=tmp_path, state_format=PIN)
    world.tool(echo)
    elsewhere = copy.copy(world)

    world.tool(echo, name="echo_twice")

    assert elsewhere.tools_by_fn[echo] == (elsewhere.tools["echo"],)
    assert world.tools_by_fn[echo] == (world.tools["echo"], world.tools["echo_twice"])


def test_a_name_can_only_be_registered_once(world: World) -> None:
    world.tool(echo)

    with pytest.raises(WorldBug, match="registered twice"):
        world.tool(echo)


@pytest.mark.parametrize("name", ["reset", "step", "state", "close"])
def test_the_environment_verbs_are_not_tool_names(world: World, name: str) -> None:
    with pytest.raises(WorldBug, match="OpenEnv reserves"):
        world.tool(echo, name=name)


def test_a_control_tool_name_is_not_a_tool_name(world: World) -> None:
    with pytest.raises(WorldBug, match="control tool"):
        world.tool(echo, name="controller_run_sql")


def test_a_tool_from_a_factory_is_registered_as_it_is(world: World) -> None:
    built = Tool.from_function(echo, name="run_sql", description="Run SQL.", transaction=False)

    returned = world.tool(built)

    assert returned is built
    assert world.tools["run_sql"] is built
    assert world.tools["run_sql"].transaction is False


@pytest.mark.parametrize(
    "options",
    [{"name": "other"}, {"description": "other"}, {"transaction": False}, {"transaction": True}],
)
def test_options_cannot_be_passed_with_a_tool_a_factory_built(
    world: World, options: dict[str, Any]
) -> None:
    built = Tool.from_function(echo)

    with pytest.raises(WorldBug, match="built by a factory"):
        world.tool(built, **options)


def test_something_that_is_neither_a_function_nor_a_tool_is_refused(world: World) -> None:
    with pytest.raises(WorldBug, match="a function or a Tool"):
        world.tool(42)  # ty: ignore[no-matching-overload]


def test_the_decorator_forms_return_what_was_decorated(world: World, ctx: Ctx) -> None:
    @world.tool
    def plain(ctx: Ctx, word: str) -> dict[str, str]:
        """Plain."""
        return {"word": word}

    @world.tool(name="renamed", description="Given.", transaction=False)
    def with_options(ctx: Ctx) -> dict:
        """Ignored."""
        return {}

    @world.middleware
    def timing(ctx: Ctx, call: Call, next_: Handler) -> Any:
        return next_(ctx, call)

    @world.instance_startup
    def startup(ctx: Ctx, *, user_id: str | None = None) -> None:
        return None

    # A decorated tool is still an ordinary function, callable in a test.
    assert plain(ctx, "hi") == {"word": "hi"}
    assert world.tools["plain"].name == "plain"
    # One call is one transaction unless the tool says otherwise, whether or not
    # an option was passed: `transaction=None` here means "not given", not "off".
    assert world.tools["plain"].transaction is True
    assert world.tools["renamed"].description == "Given."
    assert world.tools["renamed"].transaction is False
    assert world.middlewares == (timing,)
    assert [hook.fn for hook in world.startup_hooks] == [startup]


def test_the_verbs_also_work_called_with_parentheses_and_nothing_else(world: World) -> None:
    @world.tool()
    def tool(ctx: Ctx) -> dict:
        """Tool."""
        return {}

    @world.middleware()
    def middleware(ctx: Ctx, call: Call, next_: Handler) -> Any:
        return next_(ctx, call)

    @world.instance_startup()
    def startup(ctx: Ctx) -> None:
        return None

    assert registered(world) == ["tool"]
    assert world.middlewares == (middleware,)
    assert [hook.fn for hook in world.startup_hooks] == [startup]


def test_anything_callable_as_three_positional_arguments_is_a_middleware(world: World) -> None:
    class Callable_:
        def __call__(self, ctx: Ctx, call: Call, next_: Handler) -> Any:
            return next_(ctx, call)

    def variadic(*args: Any) -> Any:
        return args[2](args[0], args[1])

    world.middleware(Callable_())
    world.middleware(variadic)

    assert len(world.middlewares) == 2


@pytest.mark.parametrize(
    "obj",
    [
        lambda ctx, call: None,
        lambda: None,
        42,
    ],
)
def test_anything_else_is_not_a_middleware(world: World, obj: Any) -> None:
    with pytest.raises(WorldBug, match=r"\(ctx, call, next_\)"):
        world.middleware(obj)


def test_the_chain_is_rebuilt_as_middleware_arrives(world: World, ctx: Ctx) -> None:
    seen: list[str] = []

    @world.tool
    def touch(ctx: Ctx) -> dict:
        """Touch."""
        seen.append("tool")
        return {}

    call = Call("touch", {}, world.tools["touch"])
    world.chain(ctx.with_call(call), call)

    @world.middleware
    def later(ctx: Ctx, call: Call, next_: Handler) -> Any:
        seen.append("middleware")
        return {"intercepted": True}

    # Read at call time, so a middleware registered after the first call runs on
    # the second: a world stays registrable for its whole life.
    assert world.chain(ctx.with_call(call), call) == {"intercepted": True}
    assert seen == ["tool", "middleware"]


def test_a_tool_registered_later_joins_the_registry(world: World) -> None:
    world.tool(echo)

    @world.tool
    def late(ctx: Ctx) -> dict:
        """Late."""
        return {}

    assert registered(world) == ["echo", "late"]


def test_startup_hooks_record_the_reset_arguments_they_accept(world: World) -> None:
    @world.instance_startup
    def principal(ctx: Ctx, *, user_id: str | None = None, tenant: str = "t") -> None:
        return None

    @world.instance_startup
    def anything(ctx: Ctx, **kwargs: Any) -> None:
        return None

    first, second = world.startup_hooks

    assert (first.accepts, first.takes_var_kwargs) == (frozenset({"user_id", "tenant"}), False)
    assert (second.accepts, second.takes_var_kwargs) == (frozenset(), True)
    assert world.accepted_startup_kwargs == {"user_id", "tenant"}


def test_a_startup_hook_is_callable_as_registered(world: World, ctx: Ctx) -> None:
    seen: dict[str, Any] = {}

    @world.instance_startup
    def startup(ctx: Ctx, *, user_id: str) -> None:
        seen["user_id"] = user_id
        seen["ctx"] = ctx

    world.startup_hooks[0](ctx, user_id="u_1")

    assert seen == {"user_id": "u_1", "ctx": ctx}


def test_a_world_with_no_startup_hooks_accepts_no_reset_arguments(world: World) -> None:
    assert world.accepted_startup_kwargs == frozenset()


@pytest.mark.parametrize("name", ["fixture", "seed", "now"])
def test_a_startup_hook_cannot_take_resets_own_arguments(world: World, name: str) -> None:
    namespace: dict[str, Any] = {}
    exec(f"def startup(ctx, *, {name}=None): pass", namespace)

    with pytest.raises(WorldBug, match="reset's own"):
        world.instance_startup(namespace["startup"])


def test_a_startup_hook_takes_the_context_and_nothing_else_positionally(world: World) -> None:
    def two_positional(ctx: Ctx, user_id: str) -> None:
        return None

    def variadic(ctx: Ctx, *args: Any) -> None:
        return None

    def none_at_all() -> None:
        return None

    for hook in (two_positional, variadic, none_at_all):
        with pytest.raises(WorldBug, match="only positional parameter"):
            world.instance_startup(hook)


def test_every_world_carries_the_one_control_tool(world: World) -> None:
    """Registered at construction, flagged, and none of the world's own doing."""
    assert [name for name, tool in world.tools.items() if tool.control] == ["controller_run_sql"]
    assert registered(world) == []


def test_a_control_tools_name_is_taken_even_by_another_control_tool(world: World) -> None:
    """The framework registered it, so even a second control tool is a duplicate."""
    second = replace(Tool.from_function(echo, name="controller_run_sql"), control=True)

    with pytest.raises(WorldBug, match="registered twice"):
        world.tool(second)


def test_not_even_a_control_tool_may_take_an_environment_verb(world: World) -> None:
    """`reset` and `step` belong to the wire, and no flag of this framework's reclaims them."""
    control_tool = replace(Tool.from_function(echo, name="reset"), control=True)

    with pytest.raises(WorldBug, match="OpenEnv reserves"):
        world.tool(control_tool)


def test_a_startup_hook_that_has_no_signature_is_refused(world: World) -> None:
    with pytest.raises(WorldBug, match="the context and keyword arguments"):
        world.instance_startup(42)  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize("has_project_file", [True, False])
def test_a_world_built_where_there_is_no_module_file_falls_back_to_the_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, has_project_file: bool
) -> None:
    """A `World` built in a REPL or an `exec` has no `__file__` to walk up from.

    The project root if there is one, and otherwise the working directory
    itself: "beside the package" means nothing when there is no package.
    """
    working_dir = tmp_path / "somewhere" / "deeper"
    working_dir.mkdir(parents=True)
    if has_project_file:
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'generated'\n")
    monkeypatch.chdir(working_dir)
    namespace: dict[str, Any] = {}

    exec(MODULE_SOURCE, namespace)

    expected = tmp_path if has_project_file else working_dir
    assert namespace["world"].fixtures_dir == expected / "fixtures"


# --- `sql_files`: the schema a world package ships ----------------------------


def a_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, Path]:
    """An importable package on disk, as a world is. Returns its name and directory.

    The name is unique per package built, because `sys.modules` outlives a test
    and a second package by one name would resolve to the first one's directory.
    """
    global _packages_built
    _packages_built += 1
    name = f"shipped{_packages_built}"
    package = tmp_path / name
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    return name, package


_packages_built = 0


def a_zipped_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A package importable from a zip, with the same schema directory as `a_package`.

    The pair is what makes the portability promise testable: `pathlib` and
    `zipfile.Path` disagree about how several spellings of `directory` resolve,
    and the disagreement is only visible by asking both.
    """
    global _packages_built
    _packages_built += 1
    name = f"zipped{_packages_built}"
    source = tmp_path / f"src{_packages_built}"
    package = source / name
    (package / "schema").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "schema" / "001_core.sql").write_text(SCHEMA)
    archive = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        for path in sorted(source.rglob("*")):
            zipped.write(path, path.relative_to(source))
    monkeypatch.syspath_prepend(str(archive))
    importlib.invalidate_caches()
    return name


def test_sql_files_reads_every_sql_file_in_filename_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `001_`, `002_` convention is the whole of the ordering, and it is sorted by name.

    Written out of order on disk and with two digits either side of ten, so a
    test cannot pass on directory order or on a string comparison that puts
    `010` before `002`.
    """
    name, package = a_package(tmp_path, monkeypatch)
    schema = package / "schema"
    schema.mkdir()
    (schema / "010_last.sql").write_text("SELECT 'third';")
    (schema / "001_first.sql").write_text("SELECT 'first';")
    (schema / "002_second.sql").write_text("SELECT 'second';")

    assert sql_files(name, "schema") == "SELECT 'first';\nSELECT 'second';\nSELECT 'third';"


def test_sql_files_reads_only_sql_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A README beside the DDL, or an editor's backup, is not part of the schema."""
    name, package = a_package(tmp_path, monkeypatch)
    schema = package / "schema"
    schema.mkdir()
    (schema / "001_core.sql").write_text("SELECT 1;")
    (schema / "README.md").write_text("not DDL")
    (schema / "001_core.sql.bak").write_text("SELECT 2;")
    (schema / "notes.txt").write_text("SELECT 3;")

    assert sql_files(name, "schema") == "SELECT 1;"


def test_sql_files_builds_a_world_from_a_package_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end, the way a world's `world.py` calls it: DDL in, a live schema out."""
    name, package = a_package(tmp_path, monkeypatch)
    schema = package / "schema"
    schema.mkdir()
    (schema / "001_core.sql").write_text("CREATE TABLE a (id TEXT PRIMARY KEY) STRICT;")
    # The second file depends on the first, which is what the ordering is for.
    (schema / "002_more.sql").write_text("CREATE INDEX a_id ON a (id);")

    world = World("w", "1.0.0", sql_files(name, "schema"), fixtures_dir=tmp_path, state_format=PIN)

    with world.instance(None) as instance:
        names = instance.inspect().rows(
            "SELECT name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    assert [row["name"] for row in names] == ["a", "a_id"]


def test_sql_files_keeps_the_text_of_each_file_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Comments and layout survive: the schema hash is of the DDL as written."""
    name, package = a_package(tmp_path, monkeypatch)
    schema = package / "schema"
    schema.mkdir()
    (schema / "001_core.sql").write_text("-- a comment\nCREATE TABLE a (id TEXT PRIMARY KEY);\n")

    assert sql_files(name, "schema") == "-- a comment\nCREATE TABLE a (id TEXT PRIMARY KEY);\n"


def test_a_schema_directory_that_is_not_there_is_a_world_bug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mistyped directory name, which would otherwise be a world with no tables."""
    name, _ = a_package(tmp_path, monkeypatch)

    with pytest.raises(WorldBug) as raised:
        sql_files(name, "schemas")

    assert "'schemas'" in str(raised.value)
    assert f"'{name}'" in str(raised.value)


def test_a_schema_directory_holding_no_sql_is_a_world_bug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The adjacent mistake: the directory is there and the DDL is not in it."""
    name, package = a_package(tmp_path, monkeypatch)
    schema = package / "schema"
    schema.mkdir()
    (schema / "README.md").write_text("the DDL goes here")

    with pytest.raises(WorldBug, match=r"no \*\.sql file"):
        sql_files(name, "schema")


def test_a_file_where_the_schema_directory_should_be_is_a_world_bug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The author who put the DDL in `schema.sql` and named it `schema`.

    "Does not exist" would send them looking for a missing file; the file is
    right there, so the message says what it found instead.
    """
    name, package = a_package(tmp_path, monkeypatch)
    (package / "schema").write_text("CREATE TABLE a (id TEXT PRIMARY KEY);")

    with pytest.raises(WorldBug, match="is a file, not a directory"):
        sql_files(name, "schema")


def test_a_directory_named_like_a_sql_file_is_a_world_bug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `*.sql` that is not a file is named, not skipped.

    Skipping it reports "holds no *.sql file" about a directory whose listing
    shows one, which is a worse thirty minutes than any message here.
    """
    name, package = a_package(tmp_path, monkeypatch)
    schema = package / "schema"
    schema.mkdir()
    (schema / "001_core.sql").mkdir()

    with pytest.raises(WorldBug, match="which is not a file"):
        sql_files(name, "schema")


def test_a_schema_file_that_is_not_utf8_text_is_a_world_bug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DDL saved in a byte encoding names itself, rather than raising a `UnicodeDecodeError`.

    Before the guard this reached a world author as a traceback out of an import
    with neither the package nor the file in it.
    """
    name, package = a_package(tmp_path, monkeypatch)
    schema = package / "schema"
    schema.mkdir()
    (schema / "001_core.sql").write_bytes(b"-- \xff\xfe latin\nSELECT 1;")

    with pytest.raises(WorldBug) as raised:
        sql_files(name, "schema")

    assert "'001_core.sql'" in str(raised.value)
    assert f"'{name}'" in str(raised.value)
    assert "UTF-8" in str(raised.value)


def test_a_schema_file_that_cannot_be_read_is_a_world_bug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the read: an `OSError` names the file too.

    The failure is injected rather than arranged on disk, because the suite runs
    as a user who can read an unreadable file; what is being pinned is that the
    `OSError` family is converted at all, which no permission bit can show here.
    """
    name, package = a_package(tmp_path, monkeypatch)
    schema = package / "schema"
    schema.mkdir()
    (schema / "001_core.sql").write_text("SELECT 1;")
    real_read_text = Path.read_text

    def refuse(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.name == "001_core.sql":
            raise PermissionError(13, "Permission denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", refuse)

    with pytest.raises(WorldBug, match="could not be read"):
        sql_files(name, "schema")


def test_a_schema_directory_that_cannot_be_listed_is_a_world_bug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And the listing, for the same reason and injected the same way."""
    name, package = a_package(tmp_path, monkeypatch)
    schema = package / "schema"
    schema.mkdir()
    (schema / "001_core.sql").write_text("SELECT 1;")
    real_iterdir = Path.iterdir

    def refuse(self: Path) -> Any:
        if self.name == "schema":
            raise PermissionError(13, "Permission denied")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", refuse)

    with pytest.raises(WorldBug, match="cannot read schema directory"):
        sql_files(name, "schema")


@pytest.mark.parametrize(
    "directory",
    [
        "..",
        "../elsewhere",
        "/etc",
        "schema/../..",
        "C:/etc",
        # Backslashes too, and on POSIX as well: `schema\..\..` is a legal
        # filename here and a traversal on Windows, so a world that used one
        # would build differently on different machines -- which is the one
        # thing this function promises does not happen.
        "..\\elsewhere",
        "schema\\..\\..",
        # The two shapes `PureWindowsPath.is_absolute()` calls relative because
        # it wants a drive *and* a root: a root with no drive, and a drive with
        # no root, which resolves against that drive's working directory.
        "\\etc",
        "C:schema",
        "\\",
        # And in a later segment, where it is easiest to think it has been made
        # safe by the segment in front of it: `PureWindowsPath("schema") /
        # "C:evil"` is `C:evil`, because joining a drive-relative path replaces
        # what came before it rather than appending to it.
        "schema/C:evil",
    ],
)
def test_a_schema_directory_that_leaves_the_package_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: str
) -> None:
    """The portability promise, enforced rather than claimed.

    `files(pkg) / directory` is `joinpath`, so `..` and an absolute path are
    followed on a filesystem and cannot be resolved in a zip at all: a world
    that used one would build from a checkout and fail once installed. Refused
    where the mistake is, not where it surfaces -- and refused on both
    separators and both Windows spellings of a root, because the mistake is not
    the platform the author is on.
    """
    name, package = a_package(tmp_path, monkeypatch)
    (tmp_path / "elsewhere").mkdir(exist_ok=True)
    (tmp_path / "elsewhere" / "001.sql").write_text("SELECT 'escaped';")
    (package / "schema").mkdir()

    with pytest.raises(WorldBug, match="leaves the package"):
        sql_files(name, directory)


@pytest.mark.parametrize("directory", ["./schema", "schema/.", "schema//", "schema/", ".//schema"])
def test_a_schema_directory_spelled_with_a_dot_or_a_bare_separator_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: str
) -> None:
    """The spellings that do not leave the package and still break the promise.

    `./schema` is what an author writes who is thinking about relative paths at
    all. `pathlib` normalises a `.` and a repeated or trailing separator away,
    so it builds from a source tree; `zipfile.Path.joinpath` is `posixpath.join`
    and keeps them, so the same world fails from an installed wheel. Refused,
    and told apart from a traversal in the message, because it is a different
    mistake with the same consequence.
    """
    name, package = a_package(tmp_path, monkeypatch)
    (package / "schema").mkdir()
    (package / "schema" / "001_core.sql").write_text(SCHEMA)

    with pytest.raises(WorldBug, match="is not spelled as a path inside it"):
        sql_files(name, directory)


@pytest.mark.parametrize(
    "directory", ["schema", "./schema", "schema/.", "schema//", "schema/", "..", "/etc", "C:schema"]
)
def test_a_schema_directory_resolves_the_same_from_a_source_tree_and_from_a_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: str
) -> None:
    """The whole promise, asked of both readers at once.

    Every spelling must give the same answer from a package on disk and from the
    same package in a zip -- the DDL, or a `WorldBug`. This is the test the
    guard exists for: each defect in this function so far has been a spelling
    that built from a source tree and raised from a wheel, and no unit test of
    the guard on its own can see that, because the disagreement is between the
    two `joinpath` implementations underneath it.
    """
    name, package = a_package(tmp_path, monkeypatch)
    (package / "schema").mkdir()
    (package / "schema" / "001_core.sql").write_text(SCHEMA)
    zipped = a_zipped_package(tmp_path, monkeypatch)

    def answer(anchor: str) -> str:
        try:
            return sql_files(anchor, directory)
        except WorldBug:
            return "refused"

    from_disk, from_zip = answer(name), answer(zipped)
    assert from_disk == from_zip
    # And it is one of the two answers, not two different failures reported alike.
    assert from_disk in (SCHEMA, "refused")


def a_namespace_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, portions: int
) -> tuple[str, list[Path]]:
    """A namespace package (no `__init__.py`) spread over `portions` sys.path entries.

    `files()` answers a `MultiplexedPath` however many portions there are, so the
    portion count is not the axis anything turns on. What `sql_files` refuses is
    a *segment* that resolves to more than one directory, which is what
    `joinpath` answers: a segment present in one portion joins to a real `Path`
    however many portions the package has, and only a segment present in two or
    more joins to another `MultiplexedPath`. The tests below build both sides of
    that line, and a two-portion package on each side of it.
    """
    global _packages_built
    _packages_built += 1
    name = f"namespaced{_packages_built}"
    roots = []
    for portion in range(portions):
        root = tmp_path / f"path{portion}"
        (root / name).mkdir(parents=True)
        roots.append(root / name)
        monkeypatch.syspath_prepend(str(root))
    importlib.invalidate_caches()
    return name, roots


def test_sql_files_reads_a_package_that_has_no_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A namespace package in one place, which is the third kind of `Traversable`.

    `importlib.resources.files()` answers a `Path` for a package on disk, a
    `zipfile.Path` for one in a zip, and a `MultiplexedPath` for a namespace
    package -- and only the first of those three is a `pathlib.Path`. The
    symlink guard asks `isinstance(entry, Path)` before it asks `is_symlink()`
    for exactly that reason: `zipfile.Path` happens to answer `is_symlink` and
    `MultiplexedPath` does not, so a guard that trusted the `Traversable`
    protocol to carry it would raise `AttributeError` here instead of reading
    the schema.

    One portion always joins to a real `Path`, so it reads. What decides the
    other cases is not the portion count but whether the segment itself is in
    more than one portion -- see the two tests below, which are a two-portion
    package that reads and a two-portion package that does not.
    """
    name, roots = a_namespace_package(tmp_path, monkeypatch, portions=1)
    (roots[0] / "schema").mkdir()
    (roots[0] / "schema" / "001_core.sql").write_text(SCHEMA)

    assert not (roots[0] / "__init__.py").exists()
    assert sql_files(name, "schema") == SCHEMA


def test_a_schema_directory_in_one_portion_of_a_split_package_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The permissive half of the rule, which is the half that says what it is.

    The package has two portions; only one of them holds `schema`. Joining
    `schema` onto the two-portion `MultiplexedPath` therefore answers a real
    `Path` -- `MultiplexedPath._follow` resolves to the single portion that has
    it -- and the schema is in one place, so it is read.

    Without this test the implemented rule is indistinguishable from a rule that
    refuses any package of more than one portion: that wider guard, with a
    byte-identical message, passes every other test in this file. The narrowness
    is the point. A world may legitimately be a namespace package whose schema
    happens to live in one of its portions, and refusing it would be refusing
    something an installed wheel reproduces exactly.
    """
    name, roots = a_namespace_package(tmp_path, monkeypatch, portions=2)
    (roots[0] / "schema").mkdir()
    (roots[0] / "schema" / "001_core.sql").write_text(SCHEMA)
    (roots[1] / "elsewhere").mkdir()

    assert len(importlib.import_module(name).__path__) == 2
    assert sql_files(name, "schema") == SCHEMA


def test_a_schema_directory_split_across_two_sys_path_entries_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two portions, and the schema is no longer in one place.

    `files()` on a two-portion namespace package answers a `MultiplexedPath`
    whose `joinpath` answers another one, and whose `iterdir` merges the
    children of two real directories. No wheel can reproduce it -- installing
    both portions lands them in one directory under `site-packages` -- so it is
    the failure `sql_files` exists to prevent, arriving through the one shape
    that is not spelled in `directory` at all.

    It is also what makes the sort key observable: merged children do not share
    a parent, so ordering them by `str` rather than by `name` would put
    `path1/.../002_b.sql` before `path0/.../001_a.sql` and run the schema out of
    order. Refusing the root is what keeps that unreachable.
    """
    name, roots = a_namespace_package(tmp_path, monkeypatch, portions=2)
    for root, sql in zip(roots, ["002_b.sql", "001_a.sql"], strict=True):
        (root / "schema").mkdir()
        (root / "schema" / sql).write_text(SCHEMA)

    with pytest.raises(WorldBug, match="resolves to more than one directory"):
        sql_files(name, "schema")


def test_a_schema_directory_reached_through_a_split_directory_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ambiguity can be in a directory on the way, not only in the last one.

    `sql` exists in both portions and `sql/schema` in only one, so joining
    resolves `sql` to a `MultiplexedPath` and `sql/schema` back to a single real
    `Path`. Checking only the directory the schema is in would accept that, and
    it should not be accepted: which `schema` the world gets depends on the order
    of `sys.path`, and installing both portions merges them, so the answer is not
    the one the checkout gave. Refused at the segment that is ambiguous, which is
    also the segment the author has to change.
    """
    name, roots = a_namespace_package(tmp_path, monkeypatch, portions=2)
    (roots[0] / "sql" / "schema").mkdir(parents=True)
    (roots[0] / "sql" / "schema" / "001_core.sql").write_text(SCHEMA)
    (roots[1] / "sql").mkdir()

    with pytest.raises(WorldBug, match=r"segment 'sql'.*resolves to more than one directory"):
        sql_files(name, "sql/schema")


def test_a_schema_directory_split_across_two_entries_cannot_smuggle_in_a_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bypass the refusal above closes, stated as the attack it is.

    A `MultiplexedPath` is not a `pathlib.Path` and *is* on a filesystem, so the
    symlink guard cannot answer for it and would skip it in silence. One portion
    is then free to be a symlink pointing anywhere, and its files are merged into
    the schema as if the package shipped them. The root is refused before the
    symlink guard is asked, so this never gets that far -- and the assertion
    that matters is the second one: whatever the error says, the text from
    outside the package is not in the schema.
    """
    name, roots = a_namespace_package(tmp_path, monkeypatch, portions=2)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "001_core.sql").write_text("-- FROM OUTSIDE THE PACKAGE")
    (roots[0] / "schema").symlink_to(outside, target_is_directory=True)
    (roots[1] / "schema").mkdir()
    (roots[1] / "schema" / "002_ok.sql").write_text(SCHEMA)

    with pytest.raises(WorldBug) as raised:
        sql_files(name, "schema")

    assert "FROM OUTSIDE THE PACKAGE" not in str(raised.value)


class _BareTraversable:
    """A `Traversable` and nothing more: the protocol's members, and no others.

    `Traversable` does not require `is_symlink`. The three implementations in
    play happen to differ about it -- `pathlib.Path` has it, `zipfile.Path` has
    it and always answers `False`, `MultiplexedPath` does not have it at all --
    and `MultiplexedPath` never reaches the symlink guard, because joining a
    segment onto one yields a real `Path`. So nothing on this interpreter shows
    what the guard's `isinstance` test is for. This does.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def name(self) -> str:
        return self._path.name

    def __truediv__(self, other: str) -> _BareTraversable:
        return _BareTraversable(self._path / other)

    def joinpath(self, other: str) -> _BareTraversable:
        return self / other

    def is_dir(self) -> bool:
        return self._path.is_dir()

    def is_file(self) -> bool:
        return self._path.is_file()

    def iterdir(self) -> Iterator[_BareTraversable]:
        return (_BareTraversable(child) for child in self._path.iterdir())

    def open(self, *args: Any, **kwargs: Any) -> IO[Any]:
        return self._path.open(*args, **kwargs)

    def read_text(self, encoding: str | None = None) -> str:
        return self._path.read_text(encoding=encoding)


def test_a_traversable_that_is_not_a_path_is_not_asked_about_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The symlink guard's `isinstance` test, which nothing else reaches.

    Symlinks belong to a filesystem, so the guard asks `isinstance(entry, Path)`
    before it asks `is_symlink()`. Drop that test and a `Traversable` that does
    not implement `is_symlink` -- which the protocol does not require -- raises
    `AttributeError` out of an import instead of building the world. Pinned with
    a `Traversable` that implements the protocol and nothing else, because no
    implementation that ships with Python is both reachable here and missing
    `is_symlink`.
    """
    name, package = a_package(tmp_path, monkeypatch)
    (package / "schema").mkdir()
    (package / "schema" / "001_core.sql").write_text(SCHEMA)
    monkeypatch.setattr(importlib.resources, "files", lambda anchor: _BareTraversable(package))

    assert sql_files(name, "schema") == SCHEMA


def test_a_schema_directory_that_is_a_symlink_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The workaround the `..` guard hands an author, closed in the same words.

    `is_dir()` follows a symlink, so refusing `../shared_schema` and then reading
    `schema -> ../shared_schema` would take the path away and leave the door. A
    zip holds no symlink and a checkout without symlink support holds a text
    file where one should be, so it breaks the same promise.
    """
    name, package = a_package(tmp_path, monkeypatch)
    shared = tmp_path / "shared_schema"
    shared.mkdir()
    (shared / "001_core.sql").write_text(SCHEMA)
    (package / "schema").symlink_to(shared, target_is_directory=True)

    with pytest.raises(WorldBug, match="is a symlink, which leaves the package"):
        sql_files(name, "schema")


def test_a_schema_directory_reached_through_a_symlink_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every directory on the way, not only the last one.

    `sql` may be a symlink and `sql/schema` an ordinary directory inside it; the
    escape is the same, and checking only the leaf would miss it.
    """
    name, package = a_package(tmp_path, monkeypatch)
    shared = tmp_path / "shared_tree"
    (shared / "schema").mkdir(parents=True)
    (shared / "schema" / "001_core.sql").write_text(SCHEMA)
    (package / "sql").symlink_to(shared, target_is_directory=True)

    with pytest.raises(WorldBug, match="is a symlink, which leaves the package"):
        sql_files(name, "sql/schema")


def test_a_schema_file_that_is_a_symlink_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symlinked `*.sql` is read and concatenated like any other, so it is refused too."""
    name, package = a_package(tmp_path, monkeypatch)
    (package / "schema").mkdir()
    (package / "schema" / "001_core.sql").write_text(SCHEMA)
    outside = tmp_path / "outside.sql"
    outside.write_text("SELECT 'escaped';")
    (package / "schema" / "002_link.sql").symlink_to(outside)

    with pytest.raises(WorldBug, match="is a symlink, which leaves the package"):
        sql_files(name, "schema")


@pytest.mark.parametrize("directory", [None, "", 42])
def test_a_schema_directory_that_is_not_a_name_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: Any
) -> None:
    name, _ = a_package(tmp_path, monkeypatch)

    with pytest.raises(WorldBug, match="needs the name of a directory"):
        sql_files(name, directory)


def test_a_schema_directory_inside_the_package_may_be_nested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the traversal rule must not break: a directory below the package's own."""
    name, package = a_package(tmp_path, monkeypatch)
    nested = package / "sql" / "schema"
    nested.mkdir(parents=True)
    (nested / "001_core.sql").write_text("SELECT 'nested';")

    assert sql_files(name, "sql/schema") == "SELECT 'nested';"


def test_a_module_inside_a_package_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`__name__` instead of `__package__`, which is one keystroke and one word away.

    `importlib.resources.files` answers for a module with an object that has no
    directory behind it, so the failure would otherwise be "does not exist",
    about a directory that does, quoting an importlib internal as the place it
    looked.
    """
    name, package = a_package(tmp_path, monkeypatch)
    (package / "schema").mkdir()
    (package / "schema" / "001_core.sql").write_text("SELECT 1;")
    (package / "world.py").write_text("")

    with pytest.raises(WorldBug, match="is a module inside one"):
        sql_files(f"{name}.world", "schema")


def test_sql_files_reads_a_package_from_a_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The portability promise, from the other side: a zipimported world builds.

    `importlib.resources` is what makes this true and `__file__` would not, so
    the claim is worth a test rather than a sentence.
    """
    source = tmp_path / "src"
    package = source / "zipped_world"
    (package / "schema").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "schema" / "001_core.sql").write_text("CREATE TABLE a (id TEXT PRIMARY KEY);")
    (package / "schema" / "002_more.sql").write_text("CREATE INDEX a_id ON a (id);")
    archive = tmp_path / "world.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        for path in sorted(source.rglob("*")):
            zipped.write(path, path.relative_to(source))
    monkeypatch.syspath_prepend(str(archive))
    importlib.invalidate_caches()

    assert sql_files("zipped_world", "schema") == (
        "CREATE TABLE a (id TEXT PRIMARY KEY);\nCREATE INDEX a_id ON a (id);"
    )


def test_a_schema_file_is_read_as_utf8_whatever_the_locale_says(tmp_path: Path) -> None:
    """UTF-8 because the call says so, not because the machine happened to agree.

    `Path.read_text()` with no `encoding` uses the locale's encoding, which on a
    developer's machine and in CI is UTF-8 and under `LC_ALL=C` is ASCII. A world
    whose schema carried an accented comment would then build in one place and
    raise a `UnicodeDecodeError` in another, which is the same portability
    promise the `..` guard above keeps. A subprocess is the only way to assert
    it: the encoding is fixed when the interpreter starts.
    """
    package = tmp_path / "accented_world"
    (package / "schema").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    # Written as bytes, so this test does not depend on the encoding of the
    # machine that runs it either.
    (package / "schema" / "001_core.sql").write_bytes(
        "-- caf\u00e9\nCREATE TABLE a (id TEXT PRIMARY KEY);".encode()
    )
    program = (
        "import sys, seahaven\n"
        "schema = seahaven.sql_files('accented_world', 'schema')\n"
        # Back to UTF-8 bytes before anything touches stdout, whose encoding is
        # ASCII here too -- printing the text would fail for the wrong reason.
        "sys.stdout.buffer.write(schema.encode())\n"
    )
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(tmp_path), SRC_DIR]),
        "PYTHONDONTWRITEBYTECODE": "1",
        "LC_ALL": "C",
        "LANG": "C",
        # Both of the escape hatches CPython has for exactly this situation, off:
        # UTF-8 mode and the C-locale coercion that would quietly make it UTF-8.
        "PYTHONUTF8": "0",
        "PYTHONCOERCECLOCALE": "0",
    }
    done = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, env=environment, check=False
    )
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    assert done.stdout.decode() == "-- caf\u00e9\nCREATE TABLE a (id TEXT PRIMARY KEY);"


def test_a_package_that_cannot_be_imported_is_a_world_bug() -> None:
    """A typo in the package name is named, not raised as an `ImportError`."""
    with pytest.raises(WorldBug, match="cannot read schema of package"):
        sql_files("no_such_package_anywhere", "schema")


@pytest.mark.parametrize("package", [".relative", ".", "..", ".projecttracker", "pkg.", "a-b"])
def test_a_package_name_that_is_not_a_python_name_is_refused(package: str) -> None:
    """The one spelling that escaped the handler below, and the family it is in.

    `import_module(".projecttracker")` does not raise `ImportError`. A leading
    dot is a *relative* import, and with no anchor to be relative to it raises
    `TypeError` from inside `importlib._bootstrap` -- past the `except
    ImportError` that turns a bad package name into a `WorldBug`, and out to the
    author as a traceback naming neither the world, the directory, nor Seahaven.

    Checked as an allowlist, so the answer does not depend on having thought of
    the spelling: every dotted part of a package name is a Python name.
    """
    with pytest.raises(WorldBug, match="sql_files needs the name of a package"):
        sql_files(package, "schema")


def test_a_world_that_fails_on_import_fails_as_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The width of the import handler, in the direction the other test cannot see.

    `except ImportError` is narrow on purpose. A world package whose own module
    body raises -- a bad SQL constant, a missing dependency of its own, a typo at
    module level -- is not a world that "cannot be read"; relabelling it
    `WorldBug: cannot read schema of package` would take the author's own
    exception away and hand back a message pointing at the schema directory,
    which is the one place the bug is not. Widen the handler to `except
    Exception` and this test is what notices.
    """
    name, package = a_package(tmp_path, monkeypatch)
    (package / "schema").mkdir()
    (package / "schema" / "001_core.sql").write_text(SCHEMA)
    (package / "__init__.py").write_text('raise ValueError("the world owns this bug")')

    with pytest.raises(ValueError, match="the world owns this bug"):
        sql_files(name, "schema")


@pytest.mark.parametrize("package", [None, "", 42])
def test_a_package_that_is_not_a_package_name_is_refused_by_name(package: Any) -> None:
    """`__package__` is `None` in a module that is not in a package, and that is the hazard.

    `importlib.resources.files(None)` does not fail: with no anchor it resolves
    to the *caller's* package, which is this framework, and a world would be
    built from whatever `seahaven/<dir>` happened to hold. It is refused here,
    where the name of the argument can be said.
    """
    with pytest.raises(WorldBug, match="needs the name of a package"):
        sql_files(package, "schema")
