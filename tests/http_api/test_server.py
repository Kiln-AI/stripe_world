"""Stripe's HTTP API over real HTTP requests, through `seahaven.http.app` under uvicorn.

Every 2xx body is also held to the pinned spec, by the suite's schema
conformance hook (`conftest._schema_conformance`).
"""

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from schema_conformance.capture import CapturedCall

KEY = "sk_test_anything"


class Api:
    """One client of one instance, `/worlds/<id>`, that records its 2xx bodies."""

    def __init__(self, client: httpx.Client, captured: list[CapturedCall], id: str) -> None:
        self._client = client
        self._captured = captured
        self._base = f"/worlds/{id}"

    def request(
        self,
        method: str,
        path: str,
        *,
        data: Any = None,
        headers: dict[str, str] | None = None,
        auth: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        sent = {"Authorization": f"Bearer {KEY}"} if auth else {}
        sent.update(headers or {})
        response = self._client.request(
            method, self._base + path, data=data, headers=sent, **kwargs
        )
        if response.is_success:
            self._captured.append(
                CapturedCall(
                    ordinal=len(self._captured) + 1,
                    tool="http",
                    label=f"{method} {path}",
                    body=response.json(),
                )
            )
        return response

    def post(self, path: str, data: Any = None, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, data=data, **kwargs)

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)


@pytest.fixture
def client(server_url: str) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=server_url) as client:
        yield client


@pytest.fixture
def api(client: httpx.Client, _schema_conformance: list[CapturedCall]) -> Api:
    return Api(client, _schema_conformance, "t")


def test_no_key_is_401(api: Api) -> None:
    response = api.get("/v1/customers", auth=False)
    assert response.status_code == 401
    assert response.json()["error"]["message"].startswith("You did not provide an API key.")
    assert response.headers["www-authenticate"] == 'Bearer realm="Stripe"'


def test_create_retrieve_and_list_a_customer_with_form_bodies(api: Api) -> None:
    created = api.post(
        "/v1/customers",
        {
            "email": "ada@example.test",
            "name": "Ada Lovelace",
            "metadata[plan]": "gold",
            "invoice_settings[footer]": "Thanks",
        },
    )
    assert created.status_code == 200
    customer = created.json()
    assert customer["livemode"] is False  # one test-mode account
    assert customer["invoice_settings"]["footer"] == "Thanks"
    assert created.headers["content-type"] == "application/json"
    assert created.headers["stripe-version"] == "2026-08-26.dahlia"
    assert created.headers["request-id"].startswith("req_")

    fetched = api.get(f"/v1/customers/{customer['id']}")
    assert fetched.json() == customer

    listed = api.get("/v1/customers").json()
    assert listed["object"] == "list"
    assert listed["url"] == "/v1/customers"
    assert [each["id"] for each in listed["data"]] == [customer["id"]]


def test_list_parameters_ride_the_query_string(api: Api) -> None:
    ids = [
        api.post("/v1/customers", {"email": f"c{n}@example.test"}).json()["id"] for n in range(3)
    ]
    first = api.get("/v1/customers", params={"limit": 2}).json()
    assert first["has_more"] is True
    rest = api.get(
        "/v1/customers", params={"limit": "2", "starting_after": first["data"][-1]["id"]}
    ).json()
    assert [each["id"] for each in first["data"] + rest["data"]] == ids[::-1]
    filtered = api.get("/v1/customers", params={"email": "c1@example.test"}).json()
    assert [each["id"] for each in filtered["data"]] == [ids[1]]


def test_a_refusal_is_the_error_envelope_and_writes_nothing(api: Api) -> None:
    response = api.post("/v1/customers", {"email": "x@example.test", "nope": "1"})
    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "type": "invalid_request_error",
            "message": "Received unknown parameter: nope",
            "code": "parameter_unknown",
            "param": "nope",
            "doc_url": "https://stripe.com/docs/error-codes/parameter-unknown",
        }
    }
    assert api.get("/v1/customers").json()["data"] == []


def test_a_missing_resource_is_404(api: Api) -> None:
    response = api.get("/v1/customers/cus_nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_missing"


def test_a_decline_is_402_and_keeps_its_rows(api: Api) -> None:
    customer = api.post("/v1/customers", {"email": "pay@example.test"}).json()["id"]
    card = api.post(
        "/v1/payment_methods",
        {
            "type": "card",
            "card[number]": "4000000000000341",
            "card[exp_month]": "9",
            "card[exp_year]": "2027",
        },
    ).json()["id"]
    declined = api.post(
        "/v1/payment_intents",
        {
            "amount": "1000",
            "currency": "usd",
            "customer": customer,
            "payment_method": card,
            "confirm": "true",
        },
    )
    assert declined.status_code == 402
    error = declined.json()["error"]
    assert error["type"] == "card_error"
    assert error["code"] == "card_declined"
    charge = api.get(f"/v1/charges/{error['charge']}").json()
    assert charge["status"] == "failed"
    intent = api.get(f"/v1/payment_intents/{error['payment_intent']['id']}").json()
    assert intent["status"] == "requires_payment_method"


def test_an_idempotency_key_replays_over_http(api: Api) -> None:
    body = {"email": "once@example.test"}
    first = api.post("/v1/customers", body, headers={"Idempotency-Key": "abc"})
    again = api.post("/v1/customers", body, headers={"Idempotency-Key": "abc"})
    assert again.json() == first.json()
    assert first.headers["idempotency-key"] == again.headers["idempotency-key"] == "abc"
    assert "idempotent-replayed" not in first.headers
    assert again.headers["idempotent-replayed"] == "true"
    assert len(api.get("/v1/customers").json()["data"]) == 1
    other = api.post(
        "/v1/customers", {"email": "x@example.test"}, headers={"Idempotency-Key": "abc"}
    )
    assert other.status_code == 400
    assert other.json()["error"]["type"] == "idempotency_error"


def test_a_json_body_works_over_http(api: Api) -> None:
    response = api.request(
        "POST", "/v1/products", json={"name": "Widget", "active": False, "metadata": {"k": "v"}}
    )
    assert response.status_code == 200
    assert response.json()["active"] is False


def test_each_instance_id_is_its_own_account(
    client: httpx.Client, _schema_conformance: list[CapturedCall]
) -> None:
    one = Api(client, _schema_conformance, "one")
    two = Api(client, _schema_conformance, "two")
    one.post("/v1/customers", {"email": "one@example.test"})
    assert two.get("/v1/customers").json()["data"] == []
    assert len(one.get("/v1/customers").json()["data"]) == 1


def test_the_script_serves_this_world_with_the_fixed_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runpy
    from pathlib import Path

    import seahaven.http

    from seahaven_stripe_world import world
    from seahaven_stripe_world.http_api import RESET_OPTIONS, handle

    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        seahaven.http, "serve", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    runpy.run_path(str(Path(__file__).parents[2] / "serve_http.py"), run_name="__main__")
    assert calls == [
        (
            (world, handle),
            {"host": "127.0.0.1", "port": 8000, "reset_options": RESET_OPTIONS},
        )
    ]
