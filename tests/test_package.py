"""The package's public surface: one import, one `World`."""

import stripeapi


def test_importing_the_package_builds_the_world() -> None:
    """The tooling finds a world by importing its package and reading `world`."""
    assert stripeapi.world.name == "stripeapi"
    assert stripeapi.world.pinned_state_format == "seahaven.state/1"


def test_the_chain_is_error_handler_then_stripe_envelope_then_idempotency() -> None:
    """Registration order is chain order, outermost first.

    The error handler wraps everything — a bug below it reaches the agent as
    this world's `INTERNAL`, never as a raw traceback. The Stripe envelope
    sits inside it, and the idempotency layer innermost — inside the envelope
    so it sees `ApiResponse` returns and `StripeApiError` raises rather than
    rendered bodies (`components/cross_cutting.md` §3.1.7).
    """
    from stripeapi.middleware.error_handler import error_handler
    from stripeapi.middleware.idempotency import idempotency
    from stripeapi.middleware.stripe_envelope import stripe_envelope

    assert list(stripeapi.world.middlewares) == [error_handler, stripe_envelope, idempotency]


def test_the_four_stripe_tools_are_registered() -> None:
    """The dispatcher phase's surface: the four Stripe MCP tools. The
    controller tool is the framework's, contributed to every world and not
    part of this world's surface; `call_stripe` is deliberately unregistered
    (functional spec §2.5)."""
    assert set(stripeapi.world.tools) == {
        "stripe_api_read",
        "stripe_api_write",
        "stripe_api_search",
        "stripe_api_details",
        "controller_run_sql",
    }
