"""The idempotency layer: a keyed POST replays its stored response.

`components/cross_cutting.md` §3.1 is the design; every decision here cites
it. Registered third (innermost), inside the Stripe envelope: that placement
is what lets the layer see `ApiResponse` returns and `StripeApiError` raises
— and therefore the `pre_execution` flag that decides whether Stripe caches
a fault — rather than reconstructing the distinction from rendered bodies.

A middleware write happens outside the per-call transaction, in autocommit,
and is deliberately not atomic with the call (§3.1.6): the reservation
exists so a key is never silently lost, not so the pair commits as one. The
table is untracked, so none of this appears in a graded change log — and a
short-circuit writes nothing anywhere, which is the property the headline
eval's reward function counts on (`inst.change_log()` gains zero records
for a replayed call, not merely zero new `charges` rows).
"""

import hashlib
import json
from typing import Any

import seahaven
from seahaven.world import Handler

from seahaven_stripe_world import _json
from seahaven_stripe_world.dispatch.response import ApiResponse
from seahaven_stripe_world.stripe_errors import (
    StripeApiError,
    idempotency_key_in_use,
    idempotency_mismatch,
)
from seahaven_stripe_world.world import world

__all__ = ["idempotency", "request_hash"]


def request_hash(method: str, path: str, params: Any) -> str:
    """sha256 hex of the canonical request (§3.1.2).

    Three inputs — upper-cased method, the path exactly as given, and the
    raw parameter object before any coercion — serialized with sorted keys
    recursively (arrays stay order-sensitive) and `_json`'s separators, so
    the canonical string is byte-reproducible across runs.
    """
    canonical = json.dumps(
        [method.upper(), path, params if params is not None else {}],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


#: Tools that carry ``idempotency_key``.  Only ``call_stripe`` exposes
#: the parameter (functional spec section 16); the MCP-facing
#: ``stripe_api_write`` dropped it in Phase 5.  ``call_stripe`` is
#: deliberately unregistered on the production world (section 2.5) but
#: test callers register it on probe worlds via ``conftest.dispatch_tool()``.
_IDEMPOTENT_TOOLS = frozenset(("call_stripe",))


@world.middleware
def idempotency(ctx: seahaven.Ctx, call: seahaven.Call, next_: Handler) -> Any:
    """Replay a POST that has been made before (§3.1.4's four outcomes)."""
    if call.name not in _IDEMPOTENT_TOOLS:
        return next_(ctx, call)
    method = call.arguments.get("method")
    if not isinstance(method, str) or method.upper() != "POST":
        # v1 DELETE does not honour keys; the key is ignored silently.
        return next_(ctx, call)
    key = call.arguments.get("idempotency_key")
    if not isinstance(key, str) or not key:
        # Not an error here: validation has not run, and raising would steal
        # the ArgumentError the framework is about to produce (§3.1.1).
        return next_(ctx, call)
    path = call.arguments.get("path")
    if not isinstance(path, str):
        # Same reasoning as the key: a non-string path must reach the agent
        # as the framework's invalid-input error, not as this layer's
        # NOT-NULL DbError on the reservation INSERT (CR round 1) — and no
        # key is reserved for a request that cannot be dispatched.
        return next_(ctx, call)

    params = call.arguments.get("params")
    digest = request_hash(method, path, params)

    stored = ctx.db.one("SELECT * FROM idempotency_keys WHERE key = ?", key)
    if stored is not None:
        if stored["state"] == "in_flight":
            # In flight beats the hash (§3.1.4d): reachable only through a
            # seeded row in a test — this world is synchronous.
            raise idempotency_key_in_use(key)
        if stored["request_hash"] != digest:
            raise idempotency_mismatch(key)
        body = _json.loads(stored["body"])
        if not isinstance(body, dict):
            raise seahaven.WorldBug(f"idempotency key {key!r} holds a non-object body")
        return ApiResponse(int(stored["status"]), body)

    ctx.db.execute(
        "INSERT INTO idempotency_keys (key, method, path, request_hash, state, status,"
        " body, created) VALUES (?, ?, ?, ?, 'in_flight', NULL, NULL, ?)",
        key,
        method.upper(),
        path,
        digest,
        ctx.clock.iso(),
    )
    try:
        result = next_(ctx, call)
    except StripeApiError as error:
        if error.pre_execution:
            # Stripe caches only what execution began; "you can retry these
            # requests" is the retraction making it true.
            ctx.db.execute("DELETE FROM idempotency_keys WHERE key = ?", key)
            raise
        ctx.db.execute(
            "UPDATE idempotency_keys SET state = 'complete', status = ?, body = ? WHERE key = ?",
            error.status,
            _json.dumps(error.envelope()),
            key,
        )
        raise
    except BaseException:
        # An author's bug (ArgumentError, WorldBug) must not burn an agent's
        # key (§3.1.4a).
        ctx.db.execute("DELETE FROM idempotency_keys WHERE key = ?", key)
        raise
    if not isinstance(result, ApiResponse):
        raise seahaven.WorldBug(
            f"stripe_api_write returned {type(result).__name__}, not ApiResponse — "
            "the idempotency layer stores status+body and cannot cache this"
        )
    ctx.db.execute(
        "UPDATE idempotency_keys SET state = 'complete', status = ?, body = ? WHERE key = ?",
        result.status,
        _json.dumps(result.body),
        key,
    )
    return result
