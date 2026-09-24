"""Stripe's form wire in, the binder's typed parameters out (`http_api/wire.py`)."""

from typing import Any

import pytest
from seahaven.http import HttpRequest

from seahaven_stripe_world.dispatch.params import Param, ParamSpec
from seahaven_stripe_world.dispatch.router import ROUTER
from seahaven_stripe_world.dispatch.routes import Route
from seahaven_stripe_world.http_api import wire
from seahaven_stripe_world.stripe_errors import StripeApiError


def route(method: str, path: str) -> Route:
    found = ROUTER.resolve(method, path)
    assert found is not None
    return found


def probe(*body: Param) -> Route:
    """A route that only declares parameters: all `coerce` reads."""
    return Route(
        method="POST",
        pattern="/v1/probe",
        op_id="PostProbe",
        params=ParamSpec(op_id="PostProbe", body=body),
    )


# --- nest ---------------------------------------------------------------------


def test_bracket_keys_nest_into_dicts_in_arrival_order() -> None:
    assert wire.nest(
        [
            ("email", "a@example.test"),
            ("metadata[plan]", "gold"),
            ("items[0][price]", "price_1"),
            ("items[0][quantity]", "2"),
            ("items[1][price]", "price_2"),
            ("invoice_settings[custom_fields][0][name]", "po"),
        ]
    ) == {
        "email": "a@example.test",
        "metadata": {"plan": "gold"},
        "items": {"0": {"price": "price_1", "quantity": "2"}, "1": {"price": "price_2"}},
        "invoice_settings": {"custom_fields": {"0": {"name": "po"}}},
    }


def test_a_trailing_empty_bracket_appends_to_a_list() -> None:
    assert wire.nest([("expand[]", "customer"), ("expand[]", "invoice")]) == {
        "expand": ["customer", "invoice"]
    }


def test_an_inner_empty_bracket_starts_a_new_element() -> None:
    assert wire.nest([("lines[][amount]", "1"), ("lines[][amount]", "2")]) == {
        "lines": [{"amount": "1"}, {"amount": "2"}]
    }


def test_an_empty_value_is_kept_as_the_clear_it_means() -> None:
    assert wire.nest([("description", ""), ("metadata[k]", "")]) == {
        "description": "",
        "metadata": {"k": ""},
    }


def test_a_repeated_plain_key_keeps_its_last_value() -> None:
    assert wire.nest([("email", "a"), ("email", "b")]) == {"email": "b"}


def test_a_key_that_is_not_bracket_notation_passes_through_for_the_binder() -> None:
    assert wire.nest([("a]b", "1")]) == {"a]b": "1"}


@pytest.mark.parametrize(
    "pairs",
    [
        [("metadata", "x"), ("metadata[k]", "v")],
        [("metadata[k]", "v"), ("metadata", "x")],
        [("expand[]", "a"), ("expand[k]", "v")],
    ],
)
def test_a_key_sent_as_a_value_and_as_a_container_is_a_400(pairs: list[tuple[str, str]]) -> None:
    with pytest.raises(StripeApiError) as caught:
        wire.nest(pairs)
    assert caught.value.status == 400
    assert caught.value.param in {"metadata", "expand"}
    assert caught.value.pre_execution


# --- coerce -------------------------------------------------------------------


def test_a_subscription_create_is_coerced_by_its_declarations() -> None:
    form = wire.nest(
        [
            ("customer", "cus_1"),
            ("items[1][price]", "price_2"),
            ("items[0][price]", "price_1"),
            ("items[0][quantity]", "3"),
            ("items[0][tax_rates]", ""),
            ("cancel_at_period_end", "true"),
            ("trial_end", "now"),
            ("default_tax_rates[0]", "txr_1"),
            ("expand[0]", "customer"),
            ("metadata[k]", ""),
        ]
    )
    assert wire.coerce(route("POST", "/v1/subscriptions"), form) == {
        "customer": "cus_1",
        # index order, not arrival order
        "items": [{"price": "price_1", "quantity": 3, "tax_rates": []}, {"price": "price_2"}],
        "cancel_at_period_end": True,
        "trial_end": "now",
        "default_tax_rates": ["txr_1"],
        "expand": ["customer"],
        "metadata": {"k": ""},
    }


def test_list_parameters_from_a_query_string_are_coerced() -> None:
    form = wire.nest(
        [("limit", "3"), ("created[gte]", "1700000000"), ("created[lt]", "1800000000")]
    )
    assert wire.coerce(route("GET", "/v1/customers"), form) == {
        "limit": 3,
        "created": {"gte": 1700000000, "lt": 1800000000},
    }


def test_a_bare_integer_range_is_coerced() -> None:
    assert wire.coerce(route("GET", "/v1/customers"), {"created": "1700000000"}) == {
        "created": 1700000000
    }


@pytest.mark.parametrize(
    ("param", "sent", "typed"),
    [
        (Param(name="n", kind="integer"), "-4", -4),
        (Param(name="n", kind="integer"), "abc", "abc"),
        (Param(name="n", kind="integer"), "1.5", "1.5"),
        (Param(name="n", kind="boolean"), "false", False),
        (Param(name="n", kind="boolean"), "yes", "yes"),
        (Param(name="n", kind="number"), "7", 7),
        (Param(name="n", kind="number"), "0.25", 0.25),
        (Param(name="n", kind="number"), "1e3", "1e3"),
        (Param(name="n", kind="timestamp"), "1700000000", 1700000000),
        (Param(name="n", kind="timestamp"), "", ""),
        (Param(name="n", kind="int_literal", choices=("inf",)), "inf", "inf"),
        (Param(name="n", kind="int_literal", choices=("inf",)), "10", 10),
        (Param(name="n", kind="string"), "12", "12"),
        (Param(name="n", kind="id"), "", ""),
    ],
)
def test_a_scalar_is_coerced_only_when_it_is_the_declared_type(
    param: Param, sent: str, typed: Any
) -> None:
    assert wire.coerce(probe(param), {"n": sent}) == {"n": typed}


def test_a_currency_map_coerces_each_entry() -> None:
    options = Param(
        name="currency_options",
        kind="map",
        item=Param(name="", kind="object", shape=(Param(name="unit_amount", kind="integer"),)),
    )
    form = wire.nest([("currency_options[eur][unit_amount]", "900")])
    assert wire.coerce(probe(options), form) == {"currency_options": {"eur": {"unit_amount": 900}}}


def test_undeclared_names_and_shapes_pass_through_for_the_binder_to_refuse() -> None:
    items = Param(name="items", kind="array", item=Param(name="", kind="integer"))
    shaped = Param(name="shaped", kind="object", shape=(Param(name="n", kind="integer"),))
    form = {
        "nope": "1",
        "items": {"a": "1"},  # not index keys: not an array
        "shaped": {"n": "1", "extra": "2"},
        "plain": {"k": "v"},
    }
    assert wire.coerce(probe(items, shaped, Param(name="plain", kind="string")), form) == {
        "nope": "1",
        "items": {"a": "1"},
        "shaped": {"n": 1, "extra": "2"},
        "plain": {"k": "v"},
    }


def test_a_route_with_no_declaration_is_left_alone() -> None:
    bare = Route(method="GET", pattern="/v1/probe", op_id="GetProbe")
    assert wire.coerce(bare, {"limit": "3"}) == {"limit": "3"}


# --- decode -------------------------------------------------------------------


def request(body: bytes = b"", query: str = "", content_type: str | None = None) -> HttpRequest:
    headers = () if content_type is None else (("Content-Type", content_type),)
    return HttpRequest("POST", "/v1/customers", query=query, headers=headers, body=body)


def test_query_and_form_body_are_read_together() -> None:
    decoded = wire.decode(
        request(
            b"email=a%40example.test&name=Ada+Lovelace&metadata[k]=v",
            query="expand[]=test_clock",
            content_type="application/x-www-form-urlencoded",
        )
    )
    assert decoded.form == {
        "expand": ["test_clock"],
        "email": "a@example.test",
        "name": "Ada Lovelace",
        "metadata": {"k": "v"},
    }
    assert decoded.json == {}


def test_a_body_with_no_content_type_is_a_form() -> None:
    assert wire.decode(request(b"limit=3")).form == {"limit": "3"}


def test_a_json_body_is_passed_through_typed() -> None:
    decoded = wire.decode(
        request(b'{"limit": 3, "expand": ["x"]}', query="email=a", content_type="application/json")
    )
    assert decoded.form == {"email": "a"}
    assert decoded.json == {"limit": 3, "expand": ["x"]}
    assert decoded.raw() == {"email": "a", "limit": 3, "expand": ["x"]}
    # the JSON side is not coerced, and wins over the query string
    assert wire.WireParams(form={"limit": "2"}, json={"limit": 3}).typed(
        route("GET", "/v1/customers")
    ) == {"limit": 3}


@pytest.mark.parametrize(
    ("body", "content_type", "message"),
    [
        (b"{nope", "application/json", "not valid JSON"),
        (b"[1, 2]", "application/json; charset=utf-8", "must be an object"),
        (b"email=\xff", None, "UTF-8"),
    ],
)
def test_an_unreadable_body_is_a_400(body: bytes, content_type: str | None, message: str) -> None:
    with pytest.raises(StripeApiError) as caught:
        wire.decode(request(body, content_type=content_type))
    assert caught.value.status == 400
    assert message in caught.value.message
