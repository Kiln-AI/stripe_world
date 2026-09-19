"""`ParamSpec` binding: the allowlist, coercion, and the central five.

The error paths an agent actually provokes run through `instance.call`, so
the chain and the envelope are in play; the deeper unit shapes (bracket
paths for array elements, strict types, `required_one_of`) bind directly —
`bind` is pure by design, which is what makes that possible.
"""

from typing import Any

import pytest
import seahaven

from conftest import BLANK_NOW
from stripeapi.dispatch.params import Param, ParamSpec, bind
from stripeapi.dispatch.response import Request
from stripeapi.dispatch.routes import Route
from stripeapi.stripe_errors import StripeApiError

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def read(instance: seahaven.Instance, path: str, params: dict | None = None) -> dict:
    return instance.call("stripe_api_read", path=path, params=params)


def write(instance: seahaven.Instance, path: str, params: dict | None = None) -> dict:
    return instance.call("stripe_api_write", method="POST", path=path, params=params)


def error_of(result: dict) -> dict:
    assert result["status"] == 400, result
    return result["body"]["error"]


def test_unknown_parameter_is_rejected(instance: seahaven.Instance) -> None:
    error = error_of(write(instance, "/v1/customers", {"nope": 1}))
    assert error["code"] == "parameter_unknown"
    assert error["param"] == "nope"
    assert error["message"] == "Received unknown parameter: nope"


def test_nested_unknown_parameter_uses_bracket_notation(instance: seahaven.Instance) -> None:
    """Probed verbatim this phase: `invoice_settings[nope]`."""
    error = error_of(write(instance, "/v1/customers", {"invoice_settings": {"nope": 1}}))
    assert error["code"] == "parameter_unknown"
    assert error["param"] == "invoice_settings[nope]"


def test_a_lifted_parameter_the_operation_refuses_is_unknown(instance: seahaven.Instance) -> None:
    """`limit` on a retrieve, `expand` on a stub DELETE: `parameter_unknown`,
    the same as any other."""
    error = error_of(read(instance, "/v1/customers/cus_1", {"limit": 5}))
    assert error["code"] == "parameter_unknown"
    assert error["param"] == "limit"


def test_literal_choice_rejected(instance: seahaven.Instance) -> None:
    error = error_of(write(instance, "/v1/customers", {"tax_exempt": "sometimes"}))
    assert error["code"] == "parameter_invalid_string"
    assert error["param"] == "tax_exempt"
    assert "must be one of" in error["message"]


def test_strict_types(instance: seahaven.Instance) -> None:
    error = error_of(write(instance, "/v1/customers", {"next_invoice_sequence": "5"}))
    assert error["code"] == "parameter_invalid_integer"
    error = error_of(write(instance, "/v1/customers", {"next_invoice_sequence": 5.0}))
    assert error["code"] == "parameter_invalid_integer"
    error = error_of(write(instance, "/v1/customers", {"next_invoice_sequence": True}))
    assert error["code"] == "parameter_invalid_integer"


def test_empty_string_where_none_is_allowed(instance: seahaven.Instance) -> None:
    error = error_of(write(instance, "/v1/customers", {"email": ""}))
    assert error["code"] == "parameter_invalid_empty"
    assert error["param"] == "email"
    # The one parameter where "" is meaningful — clearing — accepts it.
    created = write(instance, "/v1/customers", {"description": "x"})
    cleared = write(instance, f"/v1/customers/{created['body']['id']}", {"description": ""})
    assert cleared["body"]["description"] is None


def test_id_prefix_checked_before_any_query(instance: seahaven.Instance) -> None:
    """`No such customer: 'ch_123'`, naming `id` — the recorded spelling for
    every top-level customers route (Phase 5 cassettes, scenario 02), not the
    `{customer}` placeholder a nested path would name."""
    result = read(instance, "/v1/customers/ch_123")
    assert result["status"] == 404
    error = result["body"]["error"]
    assert error["code"] == "resource_missing"
    assert error["param"] == "id"
    assert error["message"] == "No such customer: 'ch_123'"


def test_limit_clamps_into_the_documented_range(instance: seahaven.Instance) -> None:
    """The live API never rejects an out-of-range `limit`: 0 and negatives
    answer one item, anything above 100 answers one hundred (recorded Phase 5,
    scenario 08 — superseding the documented 1-100 contract Phase 3 enforced
    as a 400)."""
    for _ in range(3):
        write(instance, "/v1/customers", {})
    for low in (0, -1, -50):
        body = read(instance, "/v1/customers", {"limit": low})["body"]
        assert len(body["data"]) == 1
        assert body["has_more"] is True
    body = read(instance, "/v1/customers", {"limit": 101})["body"]
    assert len(body["data"]) == 3  # clamped to 100, then to what exists
    assert body["has_more"] is False
    assert read(instance, "/v1/customers")["body"]["data"]  # the default 10


def test_cursor_length_bound_is_enforced(instance: seahaven.Instance) -> None:
    """The cursors carry the spec's own `maxLength: 5000`, and the central
    `STARTING_AFTER`/`ENDING_BEFORE` Params — not a bare type check — enforce
    it."""
    error = error_of(read(instance, "/v1/customers", {"starting_after": "cus_" + "x" * 5000}))
    assert error["code"] == "parameter_invalid_string"
    assert error["param"] == "starting_after"
    error = error_of(read(instance, "/v1/customers", {"ending_before": ""}))
    assert error["code"] == "parameter_invalid_empty"
    assert error["param"] == "ending_before"


def test_mutually_exclusive_cursors(instance: seahaven.Instance) -> None:
    """Two real ids → the exclusivity refusal, message wire-verbatim with **no
    `code`** (recorded Phase 5, scenario 09). A bogus cursor alongside a real
    one is the bogus one's `resource_missing` at 400 first — resolution
    precedes exclusivity on the live API."""
    first = write(instance, "/v1/customers", {})["body"]["id"]
    second = write(instance, "/v1/customers", {})["body"]["id"]
    result = read(instance, "/v1/customers", {"starting_after": first, "ending_before": second})
    assert result["status"] == 400
    error = result["body"]["error"]
    assert "code" not in error
    assert (
        error["message"]
        == "Received both starting_after and ending_before parameters. Please pass in only one."
    )

    bogus = read(instance, "/v1/customers", {"starting_after": "cus_aaa", "ending_before": second})
    assert bogus["status"] == 400
    assert bogus["body"]["error"]["code"] == "resource_missing"
    assert bogus["body"]["error"]["param"] == "starting_after"


# --- bind() directly: the shapes only a synthetic spec can provoke -------------


def spec(body: tuple[Param, ...], **kwargs: Any) -> ParamSpec:
    defaults: dict[str, Any] = {"op_id": "PostProbe", "body": body}
    defaults.update(kwargs)
    return ParamSpec(**defaults)  # type: ignore[arg-type]


def bound(spec_: ParamSpec, raw: dict, path: str = "/v1/probe") -> Request:
    """`bind` is pure, so the synthetic specs bind directly without a router.

    The table-shape checks those specs would also pass through live in
    `test_router.py`'s synthetic-table cases.
    """
    route = Route(method="POST", pattern=path, op_id="PostProbe", params=spec_)
    return bind(route, (), path, raw, idempotency_key=None)


def test_missing_required_parameter() -> None:
    try:
        bound(spec((Param(name="currency", kind="string", required=True),)), {})
    except StripeApiError as raised:
        assert raised.code == "parameter_missing"
        assert raised.message == "Missing required param: currency."
        assert raised.param == "currency"
    else:
        raise AssertionError


def test_nested_param_uses_bracket_notation() -> None:
    inner = Param(name="default_payment_method", kind="string")
    try:
        bound(
            spec((Param(name="invoice_settings", kind="object", shape=(inner,)),)),
            {"invoice_settings": {"default_payment_method": 7}},
        )
    except StripeApiError as raised:
        assert raised.param == "invoice_settings[default_payment_method]"
    else:
        raise AssertionError


def test_array_item_param_is_indexed() -> None:
    item = Param(name="price", kind="string", required=True)
    array = Param(name="items", kind="array", item=Param(name="", kind="object", shape=(item,)))
    try:
        bound(spec((array,)), {"items": [{"price": "p1"}, {"price": 2}]})
    except StripeApiError as raised:
        assert raised.param == "items[1][price]"
    else:
        raise AssertionError


def test_required_one_of() -> None:
    try:
        bound(spec((), required_one_of=(("charge", "payment_intent"),)), {})
    except StripeApiError as raised:
        assert "One of charge or payment_intent is required" in raised.message
    else:
        raise AssertionError


def test_a_range_filter_accepts_object_and_integer() -> None:
    from stripeapi import _time

    coerced = bound(
        spec((Param(name="created", kind="range"),)),
        {"created": {"gte": 1_700_000_000, "lt": 1_800_000_000}},
    )
    assert coerced.params == {
        "created": {
            "gte": _time.from_unix(1_700_000_000),
            "lt": _time.from_unix(1_800_000_000),
        }
    }
    exact = bound(spec((Param(name="created", kind="range"),)), {"created": 1_700_000_000})
    assert exact.params == {"created": {"eq": _time.from_unix(1_700_000_000)}}


def test_metadata_update_semantics() -> None:
    from stripeapi.dispatch.params import MetadataUpdate

    update = MetadataUpdate(clear=False, set={"a": "1"}, unset=frozenset({"b"}))
    assert update.apply({"b": "old", "c": "keep"}) == {"a": "1", "c": "keep"}
    assert MetadataUpdate(clear=True, set={}, unset=frozenset()).apply({"x": "y"}) == {}


def test_metadata_limits(instance: seahaven.Instance) -> None:
    error = error_of(write(instance, "/v1/customers", {"metadata": {"k": "x" * 501}}))
    assert error["param"] == "metadata[k]"
    error = error_of(write(instance, "/v1/customers", {"metadata": {"k" * 41: "x"}}))
    assert error["param"] == f"metadata[{'k' * 41}]"
    error = error_of(
        write(instance, "/v1/customers", {"metadata": {f"k{i}": "x" for i in range(51)}})
    )
    assert error["param"] == "metadata"


def test_metadata_clear_all_both_spellings(instance: seahaven.Instance) -> None:
    created = write(instance, "/v1/customers", {"metadata": {"a": "1"}})
    cus = created["body"]["id"]
    cleared = write(instance, f"/v1/customers/{cus}", {"metadata": ""})
    assert cleared["body"]["metadata"] == {}
    again = write(instance, f"/v1/customers/{cus}", {"metadata": {"b": "2"}})
    assert again["body"]["metadata"] == {"b": "2"}
    cleared_again = write(instance, f"/v1/customers/{cus}", {"metadata": {}})
    assert cleared_again["body"]["metadata"] == {}


def test_metadata_merge_and_unset(instance: seahaven.Instance) -> None:
    created = write(instance, "/v1/customers", {"metadata": {"a": "1", "b": "2"}})
    cus = created["body"]["id"]
    updated = write(instance, f"/v1/customers/{cus}", {"metadata": {"b": "", "c": "3"}})
    assert updated["body"]["metadata"] == {"a": "1", "c": "3"}


def test_metadata_refused_where_unsupported(instance: seahaven.Instance) -> None:
    """Every route of the probe resource accepts metadata except the stub
    DELETE, which accepts nothing at all (`expand=False`, no body)."""
    created = write(instance, "/v1/customers", {})
    result = instance.call(
        "stripe_api_write",
        method="DELETE",
        path=f"/v1/customers/{created['body']['id']}",
        params={"metadata": {"a": "1"}},
    )
    assert result["status"] == 400
    assert result["body"]["error"]["param"] == "metadata"
