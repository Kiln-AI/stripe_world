"""Stripe's wire format in, the dispatcher's typed parameter object out.

Stripe's API takes `application/x-www-form-urlencoded` bodies and query strings
with bracket notation: `items[0][price]=price_1`, `metadata[k]=v`,
`expand[]=customer`, and an empty value to clear a field. Every value on that
wire is a string. The dispatcher's binder (`dispatch/params.py`) is strict on
purpose, because the MCP tools hand it JSON: `"5"` is not an integer there.

So the form side is decoded into nested dicts and lists of strings, then
coerced to the binder's types, driven by the operation's own `Param`
declarations. A value that does not coerce is passed on unchanged for the
binder to refuse in Stripe's words (`Invalid integer: abc`). A JSON body is
already typed and is passed through as it is.
"""

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import parse_qsl

from seahaven.http import HttpRequest

from seahaven_stripe_world.dispatch import params as params_mod
from seahaven_stripe_world.dispatch.params import Param
from seahaven_stripe_world.dispatch.routes import Route
from seahaven_stripe_world.stripe_errors import StripeApiError, invalid_request

__all__ = ["WireParams", "coerce", "decode", "nest"]

# `name` followed by any number of `[segment]`s; `[]` is an empty segment.
_KEY: Final = re.compile(r"([^\[\]]+)((?:\[[^\[\]]*\])*)")
_SEGMENT: Final = re.compile(r"\[([^\[\]]*)\]")
_INTEGER: Final = re.compile(r"-?[0-9]+")
_NUMBER: Final = re.compile(r"-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
_BOOLEANS: Final = {"true": True, "false": False}

_INDEX: Final = re.compile(r"[0-9]+")

#: The central parameters that have a `Param`, so `coerce` walks them like any
#: other. `metadata` has none and needs none: its values are strings on both
#: wires, and `""` clears it on both.
_CENTRAL: Final[dict[str, Param]] = {
    param.name: param
    for param in (
        params_mod.EXPAND,
        params_mod.LIMIT,
        params_mod.STARTING_AFTER,
        params_mod.ENDING_BEFORE,
    )
}


@dataclass(frozen=True, slots=True)
class WireParams:
    """One request's parameters: `form` from the query string and a form body,
    all strings; `json` from a JSON body, already typed."""

    form: dict[str, Any] = field(default_factory=dict)
    json: dict[str, Any] = field(default_factory=dict)

    def raw(self) -> dict[str, Any]:
        """The parameters as sent, before coercion: what an idempotency key hashes."""
        return {**self.form, **self.json}

    def typed(self, route: Route) -> dict[str, Any]:
        """The parameter object `dispatch` takes for `route`."""
        return {**coerce(route, self.form), **self.json}


def decode(request: HttpRequest) -> WireParams:
    """The query string and the body, whichever the request carries.

    A body is JSON when its content type says so, and a form otherwise, which is
    what Stripe's SDKs send. Raises `StripeApiError` 400 for a body that cannot
    be read.
    """
    pairs = _pairs(request.query)
    json_params: dict[str, Any] = {}
    if request.body.strip():
        content_type = (request.header("content-type") or "").split(";")[0].strip().lower()
        if content_type == "application/json" or content_type.endswith("+json"):
            json_params = _json_object(request.body)
        else:
            try:
                text = request.body.decode("utf-8")
            except UnicodeDecodeError:
                raise invalid_request(
                    "Invalid request body: it must be UTF-8 encoded.", pre_execution=True
                ) from None
            pairs += _pairs(text)
    return WireParams(form=nest(pairs), json=json_params)


def nest(pairs: Iterable[tuple[str, str]]) -> dict[str, Any]:
    """Bracket notation to nested dicts and lists, in the order the pairs came.

    `a[b][c]=v` nests dicts; a trailing `[]` appends to a list, and a `[]` inside
    a key starts a new list element. Indexed arrays (`items[0][price]`) stay
    dicts keyed by `"0"`, `"1"`, … until `coerce` knows the parameter is an
    array. A repeated plain key keeps its last value.
    """
    root: dict[str, Any] = {}
    for key, value in pairs:
        matched = _KEY.fullmatch(key)
        if matched is None:
            # Not bracket notation at all: the binder names it as unknown.
            root[key] = value
            continue
        segments = [matched[1], *_SEGMENT.findall(matched[2])]
        _insert(root, segments, value, key)
    return root


def _insert(root: dict[str, Any], segments: list[str], value: str, key: str) -> None:
    node: Any = root
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if isinstance(node, list):
            # The container a `[]` opened: a scalar appends, a deeper key goes
            # into a fresh element.
            if last:
                node.append(value)
                return
            child: Any = {}
            node.append(child)
            node = child
            continue
        if last:
            if isinstance(node.get(segment), dict | list):
                raise _conflict(key)
            node[segment] = value
            return
        wanted: type = list if segments[index + 1] == "" else dict
        existing = node.get(segment)
        if existing is None:
            existing = node[segment] = wanted()
        elif not isinstance(existing, wanted):
            raise _conflict(key)
        node = existing


def _conflict(key: str) -> StripeApiError:
    name = key.split("[", 1)[0]
    return invalid_request(
        f"Invalid {name}: {key} is sent both as a value and as nested parameters",
        param=name,
        pre_execution=True,
    )


def coerce(route: Route, form: Mapping[str, Any]) -> dict[str, Any]:
    """`form`'s strings as the types `route`'s parameters declare.

    A name the operation does not declare is passed on as it is, for the binder
    to refuse. A route with no parameter declaration is left alone.
    """
    if route.params is None:
        return dict(form)
    declared = {param.name: param for param in params_mod.body_of(route)}
    out: dict[str, Any] = {}
    for name, value in form.items():
        param = declared.get(name) or _CENTRAL.get(name)
        out[name] = value if param is None else _coerce(param, value)
    return out


def _coerce(param: Param, value: Any) -> Any:
    kind = param.kind
    if isinstance(value, str):
        return _scalar(kind, value)
    if isinstance(value, dict):
        if kind == "array":
            indexed = _indexed(value)
            return value if indexed is None else _coerce(param, indexed)
        if kind == "object":
            shape = {each.name: each for each in param.shape}
            return {
                key: item if key not in shape else _coerce(shape[key], item)
                for key, item in value.items()
            }
        if kind == "map" and param.item is not None:
            item = param.item
            return {key: _coerce(item, each) for key, each in value.items()}
        if kind == "range":
            return {key: _integer(each) for key, each in value.items()}
        return value
    if isinstance(value, list) and kind == "array" and param.item is not None:
        item = param.item
        return [_coerce(item, each) for each in value]
    return value


def _scalar(kind: str, value: str) -> Any:
    if kind in ("integer", "timestamp", "int_literal", "range"):
        return _integer(value)
    if kind == "boolean":
        return _BOOLEANS.get(value, value)
    if kind == "number":
        if _INTEGER.fullmatch(value):
            return int(value)
        return float(value) if _NUMBER.fullmatch(value) else value
    if kind == "array" and value == "":
        # Stripe's form spelling of an empty list, which is how the SDKs clear
        # one (`default_tax_rates=`); the binder's JSON spelling is `[]`.
        return []
    return value


def _integer(value: Any) -> Any:
    if isinstance(value, str) and _INTEGER.fullmatch(value):
        return int(value)
    return value


def _indexed(value: dict[str, Any]) -> list[Any] | None:
    """`{"0": a, "1": b}` as `[a, b]`, in index order; `None` for any other dict."""
    if value and all(_INDEX.fullmatch(key) for key in value):
        return [value[key] for key in sorted(value, key=int)]
    return None


def _pairs(text: str) -> list[tuple[str, str]]:
    return parse_qsl(text, keep_blank_values=True)


def _json_object(body: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(body)
    except ValueError:
        raise invalid_request(
            "Invalid request body: it is not valid JSON.", pre_execution=True
        ) from None
    if not isinstance(parsed, dict):
        raise invalid_request(
            "Invalid request body: a JSON body must be an object of parameters.",
            pre_execution=True,
        )
    return parsed
