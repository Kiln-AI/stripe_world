"""The package's public surface: one import, one `World`."""

import seahaven_stripe_world


def test_importing_the_package_builds_the_world() -> None:
    """The tooling finds a world by importing its package and reading `world`."""
    assert seahaven_stripe_world.world.name == "seahaven_stripe_world"
    assert seahaven_stripe_world.world.pinned_state_format == "seahaven.state/1"


def test_the_two_names_face_opposite_audiences() -> None:
    """The world's name discloses; the MCP server's name is Stripe's own.

    Asserted rather than left to the comments in `world.py`, because the split is
    the whole of this world's naming rule and a well-meaning edit that collapses
    the two names back into one would otherwise pass: `mcp_server_name` defaults
    to `name`, so deleting the argument is silent.
    """
    assert seahaven_stripe_world.world.name == "seahaven_stripe_world"
    assert seahaven_stripe_world.world.mcp_server_name == "stripe-mcp"


def test_the_chain_is_error_handler_then_stripe_envelope_then_idempotency() -> None:
    """Registration order is chain order, outermost first.

    The error handler wraps everything — a bug below it reaches the agent as
    this world's `INTERNAL`, never as a raw traceback. The Stripe envelope
    sits inside it, and the idempotency layer innermost — inside the envelope
    so it sees `ApiResponse` returns and `StripeApiError` raises rather than
    rendered bodies (`components/cross_cutting.md` §3.1.7).
    """
    from seahaven_stripe_world.middleware.error_handler import error_handler
    from seahaven_stripe_world.middleware.idempotency import idempotency
    from seahaven_stripe_world.middleware.stripe_envelope import stripe_envelope

    assert list(seahaven_stripe_world.world.middlewares) == [
        error_handler,
        stripe_envelope,
        idempotency,
    ]


def test_the_four_stripe_tools_are_registered() -> None:
    """The dispatcher phase's surface: the four Stripe MCP tools. The
    controller tool is the framework's, contributed to every world and not
    part of this world's surface; `call_stripe` is deliberately unregistered
    (functional spec §2.5)."""
    assert set(seahaven_stripe_world.world.tools) == {
        "stripe_api_read",
        "stripe_api_write",
        "stripe_api_search",
        "stripe_api_details",
        "controller_run_sql",
    }
