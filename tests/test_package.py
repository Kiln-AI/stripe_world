"""The package's public surface: one import, one `World`."""

import stripeapi


def test_importing_the_package_builds_the_world() -> None:
    """The tooling finds a world by importing its package and reading `world`."""
    assert stripeapi.world.name == "stripeapi"
    assert stripeapi.world.pinned_state_format == "seahaven.state/1"


def test_the_error_handler_is_the_only_middleware() -> None:
    """Registration order is chain order, outermost first.

    The Stripe envelope and idempotency layers register inside the error handler
    in later phases; until they exist, the chain is the error handler alone.
    """
    from stripeapi.middleware.error_handler import error_handler

    assert list(stripeapi.world.middlewares) == [error_handler]


def test_the_world_registers_no_tools_of_its_own_yet() -> None:
    """Phase 1 is a skeleton: the four Stripe tools arrive with the dispatcher.

    `controller_run_sql` is the framework's, contributed to every world and not
    part of this world's surface.
    """
    assert set(stripeapi.world.tools) == {"controller_run_sql"}
