"""The error wrapper: what an agent is told when something goes wrong.

The rule the handler exists to keep is that no engine or framework text reaches
an agent unless this world chose it. Every case here drives a real tool on a
real world through `Instance.call`, because a middleware tested with a stub for
`next_` proves only that a function maps an exception.

A `StripeApiError` is deliberately absent from these cases: it is not the error
handler's to map. The Stripe-envelope middleware catches it outside the
per-call transaction and renders `{status, body}`; if it reached this handler,
that would be the bug (`tests/test_stripe_envelope.py` holds those cases).
"""

import logging
from collections.abc import Callable
from typing import Any

import pytest
import seahaven

from conftest import BLANK_NOW, a_tool
from stripeapi.errors import Internal, InvalidInput

# A table no world here has, for the `DbError` an ordinary tool provokes by
# writing SQL that cannot run.
MISSING_TABLE = "charges"

type Probe = Callable[..., seahaven.World]


def test_this_worlds_own_shape_reaches_the_agent_unchanged(probe: Probe) -> None:
    """A `ToolError` this world wrote for the agent already is passed through."""

    def refusing(ctx: seahaven.Ctx) -> None:
        raise InvalidInput("method", "must be GET, POST or DELETE")

    world = probe(a_tool(refusing, "refusing"))
    with world.instance(None, now=BLANK_NOW) as instance:
        with pytest.raises(InvalidInput) as raised:
            instance.call("refusing")
        assert raised.value.to_dict() == {
            "code": "INVALID_INPUT",
            "message": "method: must be GET, POST or DELETE",
            "details": {"field": "method"},
        }


def test_an_argument_error_becomes_invalid_input_and_keeps_the_framework_error_behind_it(
    probe: Probe,
) -> None:
    """Every violation in one `INVALID_INPUT`, with the framework's own error as the cause.

    This is the branch behind the tool contract: a write verb sent to the read
    tool, a non-object `params` — the authoring mistakes functional spec §2.3
    reserves for Seahaven's declared-error mechanism.
    """

    def calls_for(
        ctx: seahaven.Ctx, method: str, params: dict[str, Any]
    ) -> None:  # pragma: no cover
        raise AssertionError("validation refuses the call before the tool runs")

    world = probe(a_tool(calls_for, "calls_for"))
    with world.instance(None, now=BLANK_NOW) as instance:
        with pytest.raises(seahaven.ToolError) as raised:
            instance.call("calls_for", method=7, params="not-an-object")
        assert raised.value.code == "INVALID_INPUT"
        # Both failures in one error, so the agent fixes them in one turn.
        assert "method" in raised.value.message
        assert "params" in raised.value.message
        cause = raised.value.__cause__
        assert isinstance(cause, seahaven.ArgumentError)
        assert [v["path"] for v in cause.violations] == ["method", "params"]


def test_a_database_error_becomes_internal(probe: Probe) -> None:
    """SQLite's complaint about this world's own SQL is this world's bug, not the agent's."""

    def broken(ctx: seahaven.Ctx) -> None:
        ctx.db.rows(f"SELECT * FROM {MISSING_TABLE}")

    world = probe(a_tool(broken, "broken"))
    with world.instance(None, now=BLANK_NOW) as instance:
        with pytest.raises(Internal) as raised:
            instance.call("broken")
        assert raised.value.to_dict() == {
            "code": "INTERNAL",
            "message": "Something went wrong",
            "details": None,
        }
        # The whole point of the branch: none of SQLite's text got out.
        assert MISSING_TABLE not in repr(raised.value)
        # And the author can still find it: the `DbError` is the cause, with the text.
        cause = raised.value.__cause__
        assert isinstance(cause, seahaven.DbError)
        assert f"no such table: {MISSING_TABLE}" in cause.sqlite_message


def test_a_database_error_the_agent_never_sees_is_written_to_the_log(
    probe: Probe, caplog: pytest.LogCaptureFixture
) -> None:
    """`INTERNAL` tells the author nothing, so the handler tells the log everything."""

    def broken(ctx: seahaven.Ctx) -> None:
        ctx.db.rows(f"SELECT * FROM {MISSING_TABLE}")

    world = probe(a_tool(broken, "broken"))
    with (
        caplog.at_level(logging.ERROR, logger="stripeapi.errors"),
        world.instance(None, now=BLANK_NOW) as instance,
    ):
        with pytest.raises(Internal):
            instance.call("broken")
        (record,) = [r for r in caplog.records if r.name == "stripeapi.errors"]
        assert instance.id in record.getMessage()
        assert "broken" in record.getMessage()
        assert record.exc_info is not None
        logged = record.exc_info[1]
        assert isinstance(logged, seahaven.DbError)
        assert logged.sqlite_message == f"no such table: {MISSING_TABLE}"


def test_a_world_bug_is_reraised_unchanged(probe: Probe) -> None:
    """The author's, not the agent's. Loudly, and unchanged.

    An id minted with an unknown prefix, an event type Stripe does not have:
    dressing those as product errors would grade this world's bugs as agent
    behavior, so the handler must not touch them.
    """
    from stripeapi._ids import stripe_id

    def minting_wrong(ctx: seahaven.Ctx) -> None:
        stripe_id(ctx, "cu_")  # typo of cus_

    world = probe(a_tool(minting_wrong, "minting_wrong"))
    with world.instance(None, now=BLANK_NOW) as instance:
        with pytest.raises(seahaven.WorldBug) as raised:
            instance.call("minting_wrong")
        assert "cu_" in str(raised.value)


def test_an_unexpected_exception_becomes_internal(probe: Probe) -> None:
    """A bug in world code is not an agent's problem to read."""

    def exploding(ctx: seahaven.Ctx) -> None:
        raise KeyError("payment_method")

    world = probe(a_tool(exploding, "exploding"))
    with world.instance(None, now=BLANK_NOW) as instance:
        with pytest.raises(Internal) as raised:
            instance.call("exploding")
        assert raised.value.to_dict() == {
            "code": "INTERNAL",
            "message": "Something went wrong",
            "details": None,
        }
        assert "'payment_method'" not in repr(raised.value)
        assert isinstance(raised.value.__cause__, KeyError)
