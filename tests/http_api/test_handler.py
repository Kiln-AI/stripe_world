"""The HTTP handler called directly, the way `seahaven.http` calls it: inside
`instance.bulk()`, with `ctx.call` set to `None`."""

import json
from collections.abc import Callable
from typing import Any

import pytest
import seahaven
from seahaven.http import HttpRequest

from conftest import BLANK_NOW
from seahaven_stripe_world.dispatch import dispatch
from seahaven_stripe_world.dispatch import router as router_mod
from seahaven_stripe_world.dispatch.params import ParamSpec
from seahaven_stripe_world.dispatch.response import ApiResponse, Request
from seahaven_stripe_world.dispatch.routes import ALL, Route
from seahaven_stripe_world.http_api import handle
from seahaven_stripe_world.stripe_errors import invalid_request

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)

AUTH = ("Authorization", "Bearer sk_test_anything")
FORM = ("Content-Type", "application/x-www-form-urlencoded")


class Answer:
    def __init__(self, status: int, headers: dict[str, str], body: Any) -> None:
        self.status = status
        self.headers = headers
        self.body = body


def send(
    instance: seahaven.Instance,
    method: str,
    path: str,
    body: str = "",
    *,
    query: str = "",
    headers: tuple[tuple[str, str], ...] = (AUTH, FORM),
) -> Answer:
    with instance.bulk() as ctx:
        assert ctx.call is None
        response = handle(
            ctx, HttpRequest(method, path, query=query, headers=headers, body=body.encode())
        )
    names = [name.lower() for name, _ in response.headers]
    assert len(names) == len(set(names)), f"a header is repeated: {names}"
    return Answer(
        response.status,
        {name.lower(): value for name, value in response.headers},
        json.loads(response.body_bytes),
    )


def customers(instance: seahaven.Instance) -> int:
    row = instance.inspect().one("SELECT count(*) AS n FROM customers")
    assert row is not None
    return row["n"]


def key_rows(instance: seahaven.Instance) -> list[dict[str, Any]]:
    return instance.inspect().rows("SELECT key, state, status FROM idempotency_keys ORDER BY key")


@pytest.fixture
def probe_route(monkeypatch: pytest.MonkeyPatch) -> Callable[[Callable[..., Any]], None]:
    """Add `POST /v1/probe_writes`, served by the handler a test gives."""

    def add(handler: Callable[[seahaven.Ctx, Request], Any]) -> None:
        route = Route(
            method="POST",
            pattern="/v1/probe_writes",
            op_id="PostProbeWrites",
            params=ParamSpec(op_id="PostProbeWrites", expand=False),
            handler=handler,
        )
        monkeypatch.setattr(router_mod, "ROUTER", router_mod.Router((*ALL, route)))

    return add


def write_a_customer(ctx: seahaven.Ctx) -> str:
    created = dispatch(
        ctx, "POST", "/v1/customers", {"email": "p@example.test"}, idempotency_key=None
    )
    return created.body["id"]


# --- auth ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "authorization",
    [None, "  ", "Bearer", "Bearer ", "basic   "],
)
def test_a_request_without_a_key_is_stripes_401(
    instance: seahaven.Instance, authorization: str | None
) -> None:
    headers = (FORM,) if authorization is None else (FORM, ("Authorization", authorization))
    answer = send(instance, "POST", "/v1/customers", "email=a%40example.test", headers=headers)
    assert answer.status == 401
    assert answer.body == {
        "error": {
            "type": "invalid_request_error",
            "message": (
                "You did not provide an API key. You need to provide your API key in the "
                "Authorization header, using Bearer auth (e.g. 'Authorization: Bearer "
                "YOUR_SECRET_KEY'). See https://stripe.com/docs/api#authentication for "
                "details, or we can help at https://support.stripe.com/."
            ),
        }
    }
    assert answer.headers["www-authenticate"] == 'Bearer realm="Stripe"'
    assert answer.headers["request-id"].startswith("req_")
    assert customers(instance) == 0


@pytest.mark.parametrize("authorization", ["Basic c2tfbGl2ZV94Og==", "Bearer sk_x", "sk_test_x"])
def test_any_key_is_accepted_and_stripe_account_is_ignored(
    instance: seahaven.Instance, authorization: str
) -> None:
    answer = send(
        instance,
        "GET",
        "/v1/customers",
        headers=(("Authorization", authorization), ("Stripe-Account", "acct_other")),
    )
    assert answer.status == 200
    assert answer.body["object"] == "list"


# --- success ------------------------------------------------------------------


def test_a_form_create_answers_the_bare_object_with_stripes_headers(
    instance: seahaven.Instance,
) -> None:
    answer = send(
        instance,
        "POST",
        "/v1/customers",
        "email=a%40example.test&metadata[plan]=gold&preferred_locales[0]=en",
    )
    assert answer.status == 200
    assert answer.body["object"] == "customer"
    assert answer.body["email"] == "a@example.test"
    assert answer.body["metadata"] == {"plan": "gold"}
    assert answer.body["preferred_locales"] == ["en"]
    assert answer.headers["content-type"] == "application/json"
    assert answer.headers["stripe-version"] == "2026-08-26.dahlia"
    assert "idempotency-key" not in answer.headers
    assert "idempotent-replayed" not in answer.headers
    # the Request-Id is the one the change's event records
    event = instance.inspect().one("SELECT request_id, request_idempotency_key FROM events")
    assert event == {"request_id": answer.headers["request-id"], "request_idempotency_key": None}


def test_get_parameters_come_from_the_query_string(instance: seahaven.Instance) -> None:
    for n in range(3):
        send(instance, "POST", "/v1/customers", f"email=c{n}%40example.test")
    page = send(instance, "GET", "/v1/customers", query="limit=2&expand[]=data.test_clock")
    assert page.status == 200
    assert len(page.body["data"]) == 2
    assert page.body["has_more"] is True
    after = page.body["data"][-1]["id"]
    rest = send(instance, "GET", "/v1/customers", query=f"limit=2&starting_after={after}")
    assert [c["email"] for c in rest.body["data"]] == ["c0@example.test"]


def test_delete_parameters_and_path_ids_work(instance: seahaven.Instance) -> None:
    created = send(instance, "POST", "/v1/customers", "email=d%40example.test").body
    deleted = send(instance, "DELETE", f"/v1/customers/{created['id']}")
    assert deleted.status == 200
    assert deleted.body == {"id": created["id"], "object": "customer", "deleted": True}


def test_a_json_body_is_accepted(instance: seahaven.Instance) -> None:
    answer = send(
        instance,
        "POST",
        "/v1/customers",
        json.dumps({"email": "j@example.test", "metadata": {"k": "v"}}),
        headers=(AUTH, ("Content-Type", "application/json")),
    )
    assert answer.status == 200
    assert answer.body["metadata"] == {"k": "v"}


# --- Stripe errors ------------------------------------------------------------


def test_a_parameter_fault_is_the_error_envelope(instance: seahaven.Instance) -> None:
    answer = send(instance, "GET", "/v1/customers", query="limit=abc")
    assert answer.status == 400
    assert answer.body["error"]["message"] == "Invalid integer: abc"
    assert answer.body["error"]["param"] == "limit"
    assert answer.headers["request-id"].startswith("req_")


def test_an_unrouted_path_is_stripes_404(instance: seahaven.Instance) -> None:
    answer = send(instance, "POST", "/v1/checkout/sessions", "mode=payment")
    assert answer.status == 404
    assert answer.body == {
        "error": {
            "type": "invalid_request_error",
            "message": "Unrecognized request URL (POST: /v1/checkout/sessions).",
        }
    }


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/v1/customers/cus_x/discount"),
        ("GET", "/v1/customers/cus_x/subscriptions"),
        ("POST", "/v1/invoices/create_preview"),
    ],
)
def test_an_operation_the_table_carries_but_this_world_has_not_built_is_404(
    instance: seahaven.Instance, method: str, path: str
) -> None:
    assert router_mod.ROUTER.match(method, path).route.params is None
    answer = send(instance, method, path)
    assert answer.status == 404
    assert answer.body == {
        "error": {
            "type": "invalid_request_error",
            "message": f"Unrecognized request URL ({method}: {path}).",
        }
    }


def test_a_raised_stripe_error_rolls_back_only_this_requests_writes(
    instance: seahaven.Instance, probe_route: Callable[..., None]
) -> None:
    def write_then_refuse(ctx: seahaven.Ctx, _request: Request) -> Any:
        write_a_customer(ctx)
        raise invalid_request("refused after writing")

    probe_route(write_then_refuse)
    send(instance, "POST", "/v1/customers", "email=kept%40example.test")
    answer = send(instance, "POST", "/v1/probe_writes")
    assert answer.status == 400
    assert answer.body == {
        "error": {"type": "invalid_request_error", "message": "refused after writing"}
    }
    assert customers(instance) == 1


def test_a_returned_error_status_keeps_its_writes(
    instance: seahaven.Instance, probe_route: Callable[..., None]
) -> None:
    def write_then_decline(ctx: seahaven.Ctx, _request: Request) -> Any:
        write_a_customer(ctx)
        return ApiResponse(402, {"error": {"type": "card_error", "message": "declined"}})

    probe_route(write_then_decline)
    answer = send(instance, "POST", "/v1/probe_writes")
    assert answer.status == 402
    assert customers(instance) == 1


def test_a_bug_in_world_code_is_left_to_the_server(
    instance: seahaven.Instance, probe_route: Callable[..., None]
) -> None:
    def broken(_ctx: seahaven.Ctx, _request: Request) -> Any:
        raise RuntimeError("a bug")

    probe_route(broken)
    with pytest.raises(RuntimeError, match="a bug"):
        send(instance, "POST", "/v1/probe_writes")


# --- idempotency --------------------------------------------------------------


def keyed(key: str) -> tuple[tuple[str, str], ...]:
    return (AUTH, FORM, ("Idempotency-Key", key))


def test_a_keyed_post_replays_its_response(instance: seahaven.Instance) -> None:
    first = send(instance, "POST", "/v1/customers", "email=i%40example.test", headers=keyed("k1"))
    again = send(instance, "POST", "/v1/customers", "email=i%40example.test", headers=keyed("k1"))
    assert again.status == first.status == 200
    assert again.body == first.body
    assert first.headers["idempotency-key"] == again.headers["idempotency-key"] == "k1"
    assert "idempotent-replayed" not in first.headers
    assert again.headers["idempotent-replayed"] == "true"
    assert again.headers["request-id"] != first.headers["request-id"]
    assert customers(instance) == 1
    event = instance.inspect().one("SELECT request_idempotency_key FROM events")
    assert event == {"request_idempotency_key": "k1"}


def test_a_key_reused_with_other_parameters_is_refused(instance: seahaven.Instance) -> None:
    send(instance, "POST", "/v1/customers", "email=i%40example.test", headers=keyed("k1"))
    other = send(instance, "POST", "/v1/customers", "email=o%40example.test", headers=keyed("k1"))
    assert other.status == 400
    assert other.body["error"]["type"] == "idempotency_error"
    assert customers(instance) == 1


def test_a_key_in_flight_is_refused(instance: seahaven.Instance) -> None:
    with instance.bulk() as ctx:
        ctx.db.execute(
            "INSERT INTO idempotency_keys (key, method, path, request_hash, state, status, body,"
            " created) VALUES ('k1', 'POST', '/v1/customers', 'x', 'in_flight', NULL, NULL, ?)",
            BLANK_NOW,
        )
    answer = send(instance, "POST", "/v1/customers", "email=i%40example.test", headers=keyed("k1"))
    assert answer.status == 409
    assert answer.body["error"]["type"] == "idempotency_error"


def test_a_stripe_error_after_execution_began_is_stored_and_replayed(
    instance: seahaven.Instance, probe_route: Callable[..., None]
) -> None:
    def write_then_refuse(ctx: seahaven.Ctx, _request: Request) -> Any:
        write_a_customer(ctx)
        raise invalid_request("refused after writing")

    probe_route(write_then_refuse)
    first = send(instance, "POST", "/v1/probe_writes", headers=keyed("k1"))
    again = send(instance, "POST", "/v1/probe_writes", headers=keyed("k1"))
    assert first.status == again.status == 400
    assert again.body == first.body
    assert again.headers["idempotent-replayed"] == "true"
    assert key_rows(instance) == [{"key": "k1", "state": "complete", "status": 400}]
    assert customers(instance) == 0


def test_a_pre_execution_fault_releases_the_key(instance: seahaven.Instance) -> None:
    refused = send(instance, "POST", "/v1/customers", "nope=1", headers=keyed("k1"))
    assert refused.status == 400
    assert key_rows(instance) == []
    retried = send(instance, "POST", "/v1/customers", "email=r%40example.test", headers=keyed("k1"))
    assert retried.status == 200


def test_a_key_on_a_get_is_echoed_and_not_stored(instance: seahaven.Instance) -> None:
    answer = send(instance, "GET", "/v1/customers", headers=keyed("k1"))
    assert answer.status == 200
    assert answer.headers["idempotency-key"] == "k1"
    assert key_rows(instance) == []
