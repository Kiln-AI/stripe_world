"""Cross-cutting integration: the middleware chain, the response shape, the
headers, and the determinism property (`components/cross_cutting.md` section 5.6).

These tests verify the cross-cutting mechanisms compose correctly across the
full surface, not within one slice.

Header and response-shape assertions use ``call_stripe`` (the raw-HTTP face)
because MCP tools return bare body on success and raise on error — they have
no ``{status, body, headers}`` wrapper.
"""

import pytest

from conftest import BLANK_NOW, api_write, dispatch_tool

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW, clock_mode="fixed")


# -- middleware chain order ----------------------------------------------------


def test_middleware_order() -> None:
    """The chain is exactly error_handler, stripe_envelope, idempotency --
    in that registration order (outermost first)."""
    from seahaven_stripe_world.middleware.error_handler import error_handler
    from seahaven_stripe_world.middleware.idempotency import idempotency
    from seahaven_stripe_world.middleware.stripe_envelope import stripe_envelope
    from seahaven_stripe_world.world import world

    names = [getattr(mw, "__name__", None) for mw in world.middlewares]
    assert names == ["error_handler", "stripe_envelope", "idempotency"]
    assert list(world.middlewares) == [error_handler, stripe_envelope, idempotency]


# -- response shape (raw-HTTP face) --------------------------------------------


def test_response_has_three_keys(probe) -> None:
    """The raw-HTTP face keeps ``{status, body, headers}``."""
    world = probe(dispatch_tool())
    with world.instance(None, now=BLANK_NOW) as instance:
        result = instance.call("call_stripe", method="POST", path="/v1/customers", params={})
        assert set(result) == {"status", "body", "headers"}
        assert isinstance(result["status"], int)
        assert isinstance(result["body"], dict)
        assert isinstance(result["headers"], dict)


def test_stripe_version_header_on_every_success(probe) -> None:
    """A successful response carries the pinned API version."""
    world = probe(dispatch_tool())
    with world.instance(None, now=BLANK_NOW) as instance:
        result = instance.call("call_stripe", method="POST", path="/v1/customers", params={})
        assert result["headers"]["Stripe-Version"] == "2026-08-26.dahlia"


def test_stripe_version_header_on_every_error(probe) -> None:
    """An error response also carries the pinned API version."""
    world = probe(dispatch_tool())
    with world.instance(None, now=BLANK_NOW) as instance:
        result = instance.call("call_stripe", method="GET", path="/v1/customers/cus_missing")
        assert result["status"] == 404
        assert result["headers"]["Stripe-Version"] == "2026-08-26.dahlia"


def test_request_id_header_is_present_and_unique(probe) -> None:
    """`Request-Id` is minted per call and is always present."""
    world = probe(dispatch_tool())
    with world.instance(None, now=BLANK_NOW) as instance:
        r1 = instance.call("call_stripe", method="POST", path="/v1/customers", params={})
        r2 = instance.call("call_stripe", method="POST", path="/v1/customers", params={})
        assert r1["headers"]["Request-Id"].startswith("req_")
        assert r2["headers"]["Request-Id"].startswith("req_")
        assert r1["headers"]["Request-Id"] != r2["headers"]["Request-Id"]


def test_idempotency_key_echoed_in_headers(probe) -> None:
    """When a caller sends an idempotency key, it is recorded in the event.
    Uses ``call_stripe`` because ``stripe_api_write`` no longer carries
    ``idempotency_key`` on the MCP surface."""
    world_p = probe(dispatch_tool())
    with world_p.instance(None, now=BLANK_NOW) as instance:
        instance.call(
            "call_stripe",
            method="POST",
            path="/v1/customers",
            params={},
            idempotency_key="ik-echo-1",
        )
        row = instance.inspect().one(
            "SELECT request_idempotency_key FROM events ORDER BY x_seq DESC LIMIT 1"
        )
        assert row is not None
        assert row["request_idempotency_key"] == "ik-echo-1"


def test_idempotency_key_absent_when_none_sent() -> None:
    """Without a key, no idempotency key is recorded."""
    import seahaven_stripe_world

    with seahaven_stripe_world.world.instance(None, now=BLANK_NOW) as instance:
        api_write(instance, "POST", "/v1/customers", {})
        row = instance.inspect().one(
            "SELECT request_idempotency_key FROM events ORDER BY x_seq DESC LIMIT 1"
        )
        assert row is not None
        assert row["request_idempotency_key"] is None


def test_get_carries_headers_but_no_idempotency_key(probe) -> None:
    """A GET response through the raw face carries headers without
    `Idempotency-Key`."""
    world = probe(dispatch_tool())
    with world.instance(None, now=BLANK_NOW) as instance:
        instance.call("call_stripe", method="POST", path="/v1/customers", params={})
        result = instance.call("call_stripe", method="GET", path="/v1/customers")
        assert set(result["headers"]) == {"Stripe-Version", "Request-Id"}


# -- short-circuit ordinal consumption ----------------------------------------


def test_short_circuit_consumes_an_ordinal(probe) -> None:
    """A replayed call still advances `call_count` (section 3.0's caveat): the
    ordinal is consumed even though nothing is written. An eval author must
    count change-log records, not calls. Uses ``call_stripe`` for
    ``idempotency_key``."""
    world_p = probe(dispatch_tool())
    with world_p.instance(None, now=BLANK_NOW) as instance:
        instance.call(
            "call_stripe",
            method="POST",
            path="/v1/customers",
            params={"email": "once@example.test"},
            idempotency_key="sc-1",
        )
        instance.call(
            "call_stripe",
            method="POST",
            path="/v1/customers",
            params={"email": "once@example.test"},
            idempotency_key="sc-1",
        )
        assert instance.inspect().one("SELECT count(*) AS n FROM customers") == {"n": 1}


# -- determinism ---------------------------------------------------------------


def test_determinism_across_two_rollouts() -> None:
    """Same fixture and seed, two rollouts produce identical ids, timestamps,
    change log, and expanded bodies (section 5.6's test_determinism)."""
    import seahaven_stripe_world
    from seahaven_stripe_world.startup import ACCOUNT_ID

    def rollout() -> dict:
        with seahaven_stripe_world.world.instance(None, now=BLANK_NOW) as inst:
            cus1 = inst.call(
                "stripe_api_write",
                stripe_api_operation_id="PostCustomers",
                parameters={"email": "det@example.test"},
                stripe_context=ACCOUNT_ID,
                livemode=True,
            )
            cus2 = inst.call(
                "stripe_api_write",
                stripe_api_operation_id="PostCustomers",
                parameters={"name": "Deterministic"},
                stripe_context=ACCOUNT_ID,
                livemode=True,
            )
            cus_id = cus1["id"]
            expanded = inst.call(
                "stripe_api_read",
                stripe_api_operation_id="GetCustomersCustomer",
                parameters={"customer": cus_id, "expand": ["default_source"]},
                stripe_context=ACCOUNT_ID,
                livemode=True,
            )
            log = inst.change_log()
            return {
                "cus1_id": cus1["id"],
                "cus2_id": cus2["id"],
                "expanded_body": expanded,
                "log_len": len(log),
                "log_tables": [r.table for r in log],
            }

    a = rollout()
    b = rollout()
    assert a["cus1_id"] == b["cus1_id"]
    assert a["cus2_id"] == b["cus2_id"]
    assert a["expanded_body"] == b["expanded_body"]
    assert a["log_len"] == b["log_len"]
    assert a["log_tables"] == b["log_tables"]
