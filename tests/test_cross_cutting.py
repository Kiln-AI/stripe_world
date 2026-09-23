"""Cross-cutting integration: the middleware chain, the response shape, the
headers, and the determinism property (`components/cross_cutting.md` section 5.6).

These tests verify the cross-cutting mechanisms compose correctly across the
full surface, not within one slice.

Header and response-shape assertions use ``call_stripe`` (the raw-HTTP face)
because MCP tools return bare body on success and raise on error — they have
no ``{status, body, headers}`` wrapper.
"""

import pytest
import seahaven

from conftest import BLANK_NOW, dispatch_tool

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


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


def test_idempotency_key_echoed_in_headers() -> None:
    """When a caller sends an idempotency key, it is echoed back in headers."""
    import seahaven_stripe_world

    with seahaven_stripe_world.world.instance(None, now=BLANK_NOW) as instance:
        instance.call(
            "stripe_api_write",
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
        instance.call("stripe_api_write", method="POST", path="/v1/customers", params={})
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


def test_short_circuit_consumes_an_ordinal(instance: seahaven.Instance) -> None:
    """A replayed call still advances `call_count` (section 3.0's caveat): the
    ordinal is consumed even though nothing is written. An eval author must
    count change-log records, not calls."""
    instance.call(
        "stripe_api_write",
        method="POST",
        path="/v1/customers",
        params={"email": "once@example.test"},
        idempotency_key="sc-1",
    )
    instance.call(
        "stripe_api_write",
        method="POST",
        path="/v1/customers",
        params={"email": "once@example.test"},
        idempotency_key="sc-1",
    )
    assert instance.inspect().one("SELECT count(*) AS n FROM customers") == {"n": 1}


# -- determinism ---------------------------------------------------------------


def test_determinism_across_two_rollouts() -> None:
    """Same fixture and seed, two rollouts including keyed calls, declines
    and expansions produce identical ids, timestamps, change log and
    idempotency_keys contents (section 5.6's test_determinism)."""
    import seahaven_stripe_world

    def rollout(seed: str | None = None) -> dict:
        with seahaven_stripe_world.world.instance(None, now=BLANK_NOW) as inst:
            cus1 = inst.call(
                "stripe_api_write",
                method="POST",
                path="/v1/customers",
                params={"email": "det@example.test"},
                idempotency_key="det-1",
            )
            cus2 = inst.call(
                "stripe_api_write",
                method="POST",
                path="/v1/customers",
                params={"name": "Deterministic"},
            )
            replay = inst.call(
                "stripe_api_write",
                method="POST",
                path="/v1/customers",
                params={"email": "det@example.test"},
                idempotency_key="det-1",
            )
            cus_id = cus1["id"]
            expanded = inst.call(
                "stripe_api_read",
                path=f"/v1/customers/{cus_id}",
                params={"expand": ["default_source"]},
            )
            log = inst.change_log()
            keys = inst.inspect().rows("SELECT * FROM idempotency_keys ORDER BY key")
            return {
                "cus1_id": cus1["id"],
                "cus2_id": cus2["id"],
                "replay_body": replay,
                "expanded_body": expanded,
                "log_len": len(log),
                "log_tables": [r.table for r in log],
                "keys": keys,
            }

    a = rollout()
    b = rollout()
    assert a["cus1_id"] == b["cus1_id"]
    assert a["cus2_id"] == b["cus2_id"]
    assert a["replay_body"] == b["replay_body"]
    assert a["expanded_body"] == b["expanded_body"]
    assert a["log_len"] == b["log_len"]
    assert a["log_tables"] == b["log_tables"]
    assert a["keys"] == b["keys"]
