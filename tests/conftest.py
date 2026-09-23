"""What every test of this world starts from: the instants, and a probe world.

A live instance comes from Seahaven's own pytest plugin: `@pytest.mark.seahaven`
says which fixture it starts from and the `instance` fixture makes it, so nothing
here builds one. That is the same path an eval takes -- `world.instance(...)`
then `instance.call(...)` -- and it is the only one that proves the chain, the
transaction and the serialiser are wired up. Nothing here calls a tool function
directly.
"""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Literal

import pytest
import seahaven

from schema_conformance import capture
from seahaven_stripe_world.middleware.error_handler import error_handler
from seahaven_stripe_world.middleware.stripe_envelope import stripe_envelope
from seahaven_stripe_world.startup import ACCOUNT_ID
from seahaven_stripe_world.world import world

# The instant every fixture of this world is frozen at (`fixtures_src/generate.py`).
FIXTURE_NOW = "2026-09-01T14:00:00.000Z"

# A blank instance's clock in the tests that do not use a fixture. Deliberately
# the same literal as `FIXTURE_NOW`, per `components/fixtures.md`: the frozen
# instant the resource-phase tests assume is the one `small` and `large` will
# later be frozen at, chosen once here and never touched again.
# `test_fixtures.py` asserts the two literals cannot drift apart.
BLANK_NOW = "2026-09-01T14:00:00.000Z"


@pytest.fixture
def probe(tmp_path: Path) -> Callable[..., seahaven.World]:
    """Builds a throwaway world carrying this world's real middleware.

    This world's tools cannot raise most of what the chain maps, and a test
    that needs a route the real table does not carry yet (a scoped child list,
    a hand-written handler) cannot add one to the real router. Rather than
    test the middleware as a function with a stub for `next_` — which proves
    it maps an exception, not that it maps one raised by a tool inside a real
    chain — each case registers the tools it needs on a world of its own and
    drives it through `Instance.call`.

    The world is this world's schema (plus whatever `schema=` adds) and this
    world's middleware — the error handler outermost, the Stripe envelope
    inside it, exactly the real chain; only the tools are the test's.
    Registering them on the real `world` would add them to the world every
    other test and every later phase sees, and registration is for the life of
    the process.

    The one registered tool is `call_stripe`-backed, so a probe world speaks
    the real dispatcher: the test patches `seahaven_stripe_world.dispatch.router.ROUTER`
    (a module attribute the dispatcher reads per call) to add its routes.
    """

    def build(*tools: Any, name: str = "probe", schema: str = "") -> seahaven.World:
        built = seahaven.World(
            name,
            world.version,
            world.schema + schema,
            fixtures_dir=tmp_path / "fixtures",
            work_dir=tmp_path / "work",
            state_format="seahaven.state/1",
        )
        built.middleware(error_handler)
        built.middleware(stripe_envelope)

        @built.instance_startup
        def _probe_startup(ctx: seahaven.Ctx, **_kwargs: object) -> None:
            """Set up the account state that _ids needs for Format B ids."""
            ctx.state["account"] = {
                "id": ACCOUNT_ID,
                "livemode": True,
                "name": None,
                "country": "US",
                "default_currency": "usd",
                "object": {},
            }

        for tool in tools:
            built.tool(tool)
        return built

    return build


def a_tool(fn: Callable[..., Any], name: str) -> Any:
    """Register `fn` under `name`, so a test can name a tool the handler knows."""
    return seahaven.Tool.from_function(fn, name=name, description=f"the {name} probe")


def dispatch_tool() -> Any:
    """The probe world's face over the real dispatcher (functional spec §2.5's
    unregistered escape hatch, registered here where it is the point)."""

    from seahaven_stripe_world.tools.api import call_stripe

    def call(
        ctx: seahaven.Ctx,
        method: Literal["GET", "POST", "DELETE"],
        path: str,
        params: dict | None = None,
    ) -> object:
        return call_stripe(ctx, method, path, params)

    return seahaven.Tool.from_function(call, name="call_stripe", description="the dispatcher probe")


@pytest.fixture(autouse=True)
def _schema_conformance(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[list[capture.CapturedCall]]:
    """Every dispatcher-tool response body this test produced is validated
    against the pinned spec after the body runs (components/conformance.md,
    "Schema conformance"). No test opts in and none can opt out: from Phase 6
    on, every resource phase's own tests are the corpus.

    Registered here rather than ``tests/schema_conformance/conftest.py`` — a
    conftest only covers its own subtree, and the resource suites the design
    means to cover live beside this package. ``Instance.call`` is wrapped
    rather than the design's ``instance.call_log`` because a ``CallRecord``
    carries no result (SEAHAVEN_FINDINGS.md Entry 9).

    Tests may take this fixture to assert on what they captured; the
    validation itself always runs at teardown.
    """
    captured = capture.begin(monkeypatch)
    yield captured
    capture.check(captured)
