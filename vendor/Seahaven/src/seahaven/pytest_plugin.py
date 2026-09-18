"""The pytest fixtures a world's own tests are written against.

Registered through the `pytest11` entry point, so installing `seahaven` activates
it in any project and nothing has to be added to a `conftest.py`. It does nothing
to a run that does not use its fixtures: two fixtures, one marker and one option.

A world's test says which fixture it starts from and nothing else::

    @pytest.mark.seahaven(fixture="empty")
    def test_an_issue_can_be_created(instance: seahaven.Instance) -> None:
        assert instance.call("create_issue", title="x")["title"] == "x"

`world` is found the way `seahaven` finds it -- functional spec §2.2, the nearest
`pyproject.toml` and the attribute `world` on the package its `[project] name`
normalises to -- from pytest's rootdir, with `--seahaven-world module:attr` as the
override. The name is the CLI's `--world` in pytest's namespace, which is shared
with every other plugin installed.

`instance` is one instance per test, destroyed on teardown whether the test passed
or not. `fixture=None` is a blank instance and not a missing value, so what fails
is the marker's *absence*: a falsy test would turn the blank-instance spelling
into the error message for a forgotten marker.

Nothing here is imported by a world's tests. The fixtures are used by name, which
is the whole interface.
"""

from collections.abc import Iterator
from typing import Any

import pytest

from seahaven import cli
from seahaven.instances import Instance
from seahaven.world import World

__all__ = ["instance", "world"]

MARKER = "seahaven"
WORLD_OPTION = "--seahaven-world"

_MARKER_SIGNATURE = (
    f"{MARKER}(fixture, seed=None, **startup_kwargs): the fixture the `instance` fixture is "
    "created from; fixture=None is a blank instance"
)

_EXAMPLE = (
    f'@pytest.mark.{MARKER}(fixture="empty") for a fixture on disk, or '
    f"@pytest.mark.{MARKER}(fixture=None) for a blank instance"
)

# The mistake behind most of the failures below: a marker on a test *replaces*
# the module's `pytestmark`, it does not add to it.
_CLOSEST_WINS = (
    f"a @pytest.mark.{MARKER} on a test replaces a module's `pytestmark` marker rather than "
    "adding to it, so every marker names its own fixture"
)


def pytest_addoption(parser: pytest.Parser) -> None:
    """`--seahaven-world`: the CLI's `--world`, for a layout the convention misses."""
    parser.addoption(
        WORLD_OPTION,
        metavar="module:attr",
        default=None,
        help=(
            "the world the `world` and `instance` fixtures act on, as an importable module and "
            "the attribute holding it; the default is the convention: the project's package, "
            "attribute 'world'"
        ),
    )


def pytest_configure(config: pytest.Config) -> None:
    """Register the marker, so `--strict-markers` accepts a world's tests."""
    config.addinivalue_line("markers", _MARKER_SIGNATURE)


@pytest.fixture(scope="session")
def world(request: pytest.FixtureRequest) -> World:
    """The project's `World`: one import, one object, for the whole session.

    Session-scoped because a `World` is a declaration -- tools, schema, middleware
    -- registered at import and never mutated by a test. What a test gets a fresh
    one of is the *instance*.
    """
    try:
        return cli.find_world(request.config.getoption(WORLD_OPTION), start=request.config.rootpath)
    except cli.CliError as error:
        # Every `CliError` is one line with the fix in it. Raising it would print
        # that line under a traceback through `import_module`, which says only
        # that Python was involved.
        pytest.fail(str(error), pytrace=False)


@pytest.fixture
def instance(request: pytest.FixtureRequest, world: World) -> Iterator[Instance]:
    """A fresh instance per test, from the fixture the `seahaven` marker names."""
    _refuse_two_markers_on_one_node(request.node)
    marker = request.node.get_closest_marker(MARKER)
    if marker is None:
        pytest.fail(f"the `instance` fixture needs a marker: {_EXAMPLE}", pytrace=False)
    fixture, startup_kwargs = _fixture_and_kwargs(marker)
    with world.instance(fixture, **startup_kwargs) as live:
        yield live


def _refuse_two_markers_on_one_node(item: pytest.Item) -> None:
    """Two `seahaven` markers on one node -- test, class or module: one wins, silently.

    `own_markers` over `listchain()` and not `iter_markers`: a module's
    `pytestmark` and a marker on a test are two *different* nodes, and the
    closest one is meant to win -- that is how a module of fixture tests spells
    its one blank-instance case. Two markers on a single node are the ambiguity:
    nothing chooses between them on purpose, and the two spellings do not even
    choose the same end of the list. The lower of two decorators wins; the first
    entry of a `pytestmark` list wins.
    """
    for node in item.listchain():
        markers = [mark for mark in node.own_markers if mark.name == MARKER]
        if len(markers) > 1:
            pytest.fail(
                f"{node.nodeid} carries {len(markers)} @pytest.mark.{MARKER} markers and only "
                f"one of them would be used; keep one: {_EXAMPLE}",
                pytrace=False,
            )


def _fixture_and_kwargs(marker: pytest.Mark) -> tuple[str | None, dict[str, Any]]:
    """The fixture the marker names, and everything else it passes to the world.

    `seed`, `now` and a world's own startup kwargs are whatever is left after
    `fixture` is taken out: the plugin does not enumerate them, because startup
    kwargs are the world's and it cannot.
    """
    if len(marker.args) > 1:
        pytest.fail(
            f"@pytest.mark.{MARKER} takes one fixture, not {len(marker.args)}: {_EXAMPLE}",
            pytrace=False,
        )
    if "fixture" in marker.kwargs:
        if marker.args:
            # Both spellings at once: one of the two would be dropped silently,
            # and which one is not something a reader should have to know.
            pytest.fail(
                f"@pytest.mark.{MARKER} was given a fixture twice, "
                f"{marker.args[0]!r} and fixture={marker.kwargs['fixture']!r}: {_EXAMPLE}",
                pytrace=False,
            )
        fixture = marker.kwargs["fixture"]
    elif marker.args:
        fixture = marker.args[0]
    else:
        # `fixture=None` is a blank instance, so the question is whether the
        # marker said anything about a fixture at all -- never whether what it
        # said is truthy. The commonest way to arrive here is a marker written to
        # add `now=` or `seed=` to a module's `pytestmark`, so that is the half
        # of the message that says what to do.
        pytest.fail(
            f"@pytest.mark.{MARKER} needs a fixture: {_EXAMPLE}. Note that {_CLOSEST_WINS}",
            pytrace=False,
        )
    return fixture, {name: value for name, value in marker.kwargs.items() if name != "fixture"}
