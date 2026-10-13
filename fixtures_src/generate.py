r"""How every fixture in `fixtures/` is made. Commit it, because it is the recipe.

A fixture is a binary artifact, and a binary artifact with no source is one
nobody can change. This module is that source: one builder per fixture, each
taking a blank instance and filling it.

Rebuild one fixture by deleting its directory and re-running the recipe:

    rm -rf fixtures/<id> && uv run python fixtures_src/generate.py <id>

With no arguments, every fixture is rebuilt in lineage order: `empty`,
`small_co`, `big_co`. Do not use `seahaven fixture freeze --run` for the company
fixtures: that path opens the instance without the test-mode startup and
without a fixed clock, so it cannot reproduce them.

Every build starts from a blank instance at `NOW` on a `fixed` clock, opened
with that fixture's `STARTUP` options (the companies are test-mode accounts).
`build` carries `NOW`, the startup options and the description for you, which
is what makes it the thing a test calls. Hand it `world=` and the fixture is
frozen into *that* world's fixtures directory, so a test that rebuilds a fixture
to compare it with the committed bytes builds into a temporary directory.

The company builders replay history through the world's own code
(`fixtures_src/replay/`). Nothing here reads the wall clock, and nothing here is
random outside `ctx.ids`.
"""

import importlib
from collections.abc import Callable

import seahaven

__all__ = [
    "BUILDERS",
    "DESCRIPTIONS",
    "NOW",
    "STARTUP",
    "big_co",
    "build",
    "empty",
    "main",
    "small_co",
]

# The instant every fixture of this world is frozen at: the one `build` passes,
# chosen once and never moved. This same literal is
# `BLANK_NOW` in `tests/conftest.py`, so a blank test instance and the frozen
# fixtures share one instant by construction (`components/fixtures.md`).
NOW = "2026-09-01T14:00:00.000Z"


def empty(inst: seahaven.Instance) -> None:
    """`empty`: the schema and nothing else.

    Deliberately does no work. It exists so that the first fixture is made the
    same way every later one is, and so the fixture others are forked from has a
    source like the rest.
    """


def _company_builder(package: str) -> Callable[[seahaven.Instance], None]:
    """A company package's `build`, imported only when that fixture is built: the companies
    replay thousands of steps through the world, and `empty` should not pay for importing them."""
    return importlib.import_module(package).build


def small_co(inst: seahaven.Instance) -> None:
    """`small_co`: Wrenfield Labs (components/small_co.md), imported when built."""
    _company_builder("fixtures_src.small_co")(inst)


def big_co(inst: seahaven.Instance) -> None:
    """`big_co`: Ostrander Systems (components/big_co.md), imported when built."""
    _company_builder("fixtures_src.big_co")(inst)


# Every fixture this module can build, by id, in lineage order; the startup
# options each blank instance is opened with; and what each one tells whoever is
# choosing between them (functional spec §3.6).
BUILDERS = {"empty": empty, "small_co": small_co, "big_co": big_co}
STARTUP: dict[str, dict[str, object]] = {
    "empty": {},
    "small_co": {"livemode": False},
    "big_co": {"livemode": False},
}
DESCRIPTIONS = {
    "empty": "Schema only. Start here to write history, or to test setup flows.",
    "small_co": (
        "Wrenfield Labs, a two-person booking and payments tool for independent tutors, music "
        "teachers and coaches, as a test-mode Stripe account: a hand-written cast of about 45 "
        "customers from a private beta through a public launch, with renewals, a failed payment "
        "in dunning, refunds and a dispute. Good for evals that need a small account whose every "
        "customer has a story."
    ),
    "big_co": (
        "Ostrander Systems, field-service scheduling software for trades businesses, as a "
        "test-mode Stripe account: thousands of customers over 18 months of simulated "
        "subscriptions, renewals, upgrades, churn, dunning, refunds, disputes and payouts, plus "
        "scripted loose ends. Good for evals that need a realistic account at scale."
    ),
}


def build(fixture_id: str, *, world: seahaven.World | None = None) -> seahaven.Fixture:
    """Freeze `fixture_id` from a blank instance at `NOW`, and return it.

    `world` is which world to freeze into, defaulting to this package's. It is
    the seam a test needs: a fixture lands in `world.fixtures_dir`, so a test
    that rebuilds one to compare it with the committed bytes hands in a world
    pointed at a temporary directory rather than moving the imported world's --
    which is a process-wide change that everything else in the run would see.

    The instance is destroyed whether the build succeeds or not, and a failure
    leaves no fixture directory behind: `freeze` publishes by rename.

    The clock is `fixed`, whatever the world's default, so the fixture's `now`
    is `NOW` exactly and a rebuild writes the same bytes (the core's own
    fixture template does the same).
    """
    into = world if world is not None else _package_world()
    with into.instance(None, now=NOW, clock_mode="fixed", startup=STARTUP[fixture_id]) as inst:
        BUILDERS[fixture_id](inst)
        return inst.freeze(fixture_id, DESCRIPTIONS[fixture_id])


def _package_world() -> seahaven.World:
    """This world, imported when a build asks for it and not before.

    `seahaven fixture freeze --run` imports this module for one function, and a
    builder should not cost a world import until it is called. Spelled through
    `world.py` rather than through the package attribute, which is a `World`
    shadowing the module of that name.
    """
    from seahaven_stripe_world.world import world

    return world


def main(argv: list[str], *, world: seahaven.World | None = None) -> int:
    """`python fixtures_src/generate.py [fixture-id ...]`: re-run the recipe.

    With no arguments, rebuilds every fixture in the committed lineage order
    (`empty` first; a later fixture forks the one before it). Prints each
    fixture's id, `now` and directory. Returns 1 for an unknown id.

    Rebuilding over committed bytes is delete-then-regenerate: `freeze` and
    `fork` both refuse to overwrite an existing directory, so remove the
    fixture's directory under `fixtures/` first.
    """
    wanted = argv or list(BUILDERS)
    for fixture_id in wanted:
        if fixture_id not in BUILDERS:
            print(f"no fixture named {fixture_id!r}; known: {', '.join(BUILDERS)}")
            return 1
    for fixture_id in wanted:
        frozen = build(fixture_id, world=world)
        print(f"{frozen.id} frozen at {frozen.now} -> {frozen.dir}")
    return 0


if __name__ == "__main__":
    import sys
    from pathlib import Path

    # Run as a script, this file's directory is on the path rather than the
    # repo root the company builders import `fixtures_src` from.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    raise SystemExit(main(sys.argv[1:]))
