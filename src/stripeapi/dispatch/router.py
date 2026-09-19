"""The compiled path trie: `(method, path)` → `Route`.

A trie rather than an ordered regex list because precedence must be a property
of the structure, not of the order of a literal: at each node, exact children
are tried before the placeholder child, always, and no entry can be written in
a way that changes that (`components/dispatcher.md` §3.2).

A placeholder node stores no name. Two nodes in the routed 148 have two
differently-named placeholder children (`/v1/credit_notes` carries both
`{credit_note}` and `{id}`; `/v1/subscriptions` both `{subscription_exposed_id}`
and `{subscription}`), so captured segments are collected positionally into
`Match.path_values` and zipped against the route's own `ParamSpec.path` — the
name is a property of the route, which is where it belongs.

`ROUTER` is built at import, not per call. It is immutable and holds no `ctx`,
so one instance serves every instance of the world.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

import seahaven

from stripeapi.dispatch import params as params_mod
from stripeapi.dispatch import routes
from stripeapi.dispatch.routes import Route
from stripeapi.stripe_errors import invalid_request

__all__ = ["ROUTER", "Match", "Router"]

_PLACEHOLDER = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")
_SEGMENT = re.compile(r"[a-zA-Z0-9_\-]+")


@dataclass(frozen=True, slots=True)
class Match:
    """A route and the segments its placeholders captured, in pattern order."""

    route: Route
    path_values: tuple[str, ...]


@dataclass(slots=True)
class _Node:
    exact: dict[str, _Node] = field(default_factory=dict)
    placeholder: _Node | None = None
    routes: dict[str, Route] = field(default_factory=dict)


class Router:
    """The compiled trie over a route table, with import-time table checks."""

    def __init__(self, table: Sequence[Route]) -> None:
        self._root = _Node()
        self._by_key: dict[tuple[str, str], Route] = {}
        op_ids = {route.op_id for route in table}
        seen: set[tuple[str, str]] = set()
        for route in table:
            self._check_shape(route)
            key = (route.method, route.pattern)
            if key in seen:
                raise seahaven.WorldBug(f"duplicate route {route.method} {route.pattern}")
            seen.add(key)
            if route.alias_of is not None and route.alias_of not in op_ids:
                raise seahaven.WorldBug(
                    f"route {route.op_id} aliases {route.alias_of!r}, which is not in the table"
                )
            node = self._root
            for segment in self._segments(route.pattern):
                if segment.startswith("{"):
                    if node.placeholder is None:
                        node.placeholder = _Node()
                    node = node.placeholder
                else:
                    node = node.exact.setdefault(segment, _Node())
            if route.method in node.routes:
                raise seahaven.WorldBug(f"duplicate route {route.method} {route.pattern}")
            node.routes[route.method] = route
            self._by_key[key] = route

    # --- construction ----------------------------------------------------------

    @staticmethod
    def _segments(pattern: str) -> list[str]:
        if not pattern.startswith("/v1/"):
            raise seahaven.WorldBug(f"route pattern {pattern!r} is not an absolute /v1/ path")
        segments = pattern.split("/")[1:]  # drop the leading empty string
        if not segments or any(not segment for segment in segments):
            raise seahaven.WorldBug(f"route pattern {pattern!r} has an empty segment")
        for segment in segments:
            if segment.startswith("{") or segment.endswith("}"):
                if not _PLACEHOLDER.fullmatch(segment):
                    raise seahaven.WorldBug(
                        f"route pattern {pattern!r} has a malformed placeholder"
                    )
            elif not _SEGMENT.fullmatch(segment):
                raise seahaven.WorldBug(f"route pattern {pattern!r} has a malformed segment")
        return segments

    @staticmethod
    def _check_shape(route: Route) -> None:
        """The table invariants, checked per route at import.

        An *unwired* route (`params is None`) is mid-build data — the resource
        phases fill the table in — so the wiring checks apply only once a
        route carries a `ParamSpec`.
        """
        spec = route.params
        if spec is None:
            if route.handler is not None or route.resource is not None or route.action is not None:
                raise seahaven.WorldBug(
                    f"route {route.op_id} carries a handler or resource but no ParamSpec"
                )
            return
        if spec.op_id != route.op_id:
            raise seahaven.WorldBug(
                f"route {route.op_id} carries ParamSpec {spec.op_id!r}: ids must agree"
            )
        placeholders = tuple(
            segment[1:-1] for segment in Router._segments(route.pattern) if segment.startswith("{")
        )
        if spec.path != placeholders:
            raise seahaven.WorldBug(
                f"route {route.op_id}: ParamSpec.path {spec.path} != pattern placeholders "
                f"{placeholders}"
            )
        for param in spec.body:
            if param.name in params_mod.CENTRAL_PARAMETERS:
                raise seahaven.WorldBug(
                    f"route {route.op_id}: body parameter {param.name!r} is handled centrally"
                )
        has_handler = route.handler is not None
        has_engine = route.resource is not None or route.action is not None
        if has_handler == has_engine:
            raise seahaven.WorldBug(
                f"route {route.op_id} must set exactly one of handler and (resource, action)"
            )
        if route.action is not None and route.resource is None:
            raise seahaven.WorldBug(f"route {route.op_id} sets action without resource")
        if route.scope is not None and route.resource is None:
            raise seahaven.WorldBug(f"route {route.op_id} sets scope without resource")
        if route.resource is not None and route.action in ("create", "update"):
            # `ResourceSpec.creatable`/`updatable` would otherwise be declared
            # but unenforced — the route's ParamSpec alone is what `bind`
            # checks — so the two are refused if they drift apart.
            declared = (
                route.resource.creatable if route.action == "create" else route.resource.updatable
            )
            if declared is not None and spec.body != declared.body:
                which = "creatable" if route.action == "create" else "updatable"
                raise seahaven.WorldBug(
                    f"route {route.op_id}: params.body has drifted from "
                    f"{route.resource.object}'s ResourceSpec.{which}"
                )
        if spec.expand:
            # Expand-path validation is static (cross_cutting.md §3.3.1): it
            # runs in the parameter layer against this declaration, before any
            # row is written — so accepting `expand[]` without declaring what
            # the operation answers is refused here rather than discovered as
            # a payload-coupled fallback later.
            if route.response_object is None or route.envelope is None:
                raise seahaven.WorldBug(
                    f"route {route.op_id} accepts expand but declares no response_object/envelope"
                )
        elif route.response_object is not None or route.envelope is not None:
            raise seahaven.WorldBug(
                f"route {route.op_id} refuses expand but declares a response object"
            )

    # --- matching ----------------------------------------------------------------

    def match(self, method: str, path: str) -> Match:
        """The route for this call.

        Raises `StripeApiError` 404 (`Unrecognized request URL (<METHOD>:
        <path>).`, a wire-quoted Stripe string) when no pattern matches the
        path at all, and 405 (`Not allowed: <METHOD> <path>`, this world's
        declared choice) when a pattern matches but no route anywhere along
        any matching path carries this method.
        """
        route, captured = self._walk(method, path)
        return Match(route, tuple(captured))

    def resolve(self, method: str, path: str) -> Route | None:
        """`match` without the raising: a pattern spelled exactly, or a
        concrete path the trie can match. Discovery's door."""
        direct = self._by_key.get((method, path))
        if direct is not None:
            return direct
        hit, _, _ = self._safe_walk(method, path)
        return hit

    def _walk(self, method: str, path: str) -> tuple[Route, list[str]]:
        hit, captured, mismatch = self._safe_walk(method, path)
        if hit is not None:
            return hit, captured
        if mismatch:
            raise invalid_request(f"Not allowed: {method} {path}", status=405, pre_execution=True)
        raise invalid_request(
            f"Unrecognized request URL ({method}: {path}).", status=404, pre_execution=True
        )

    def _safe_walk(self, method: str, path: str) -> tuple[Route | None, list[str], bool]:
        """Depth-first, exact before placeholder, with backtracking.

        Returns the route for `(method, path)` with its captured segments, or
        `(None, …, mismatch)` where `mismatch` says some terminal matched the
        path but not the verb — the 405 case. Depth is at most six segments
        and each node has at most two children, so the walk is bounded by
        structure rather than by table size.
        """
        stripped = path.lstrip("/")
        segments = stripped.split("/") if stripped else []
        captured: list[str] = []
        method_mismatch = False

        def walk(node: _Node, index: int) -> Route | None:
            nonlocal method_mismatch
            if index == len(segments):
                if method in node.routes:
                    return node.routes[method]
                if node.routes:
                    method_mismatch = True
                return None
            segment = segments[index]
            if segment in node.exact:  # exact first, always
                hit = walk(node.exact[segment], index + 1)
                if hit is not None:
                    return hit
            if node.placeholder is not None and segment != "":
                captured.append(segment)
                hit = walk(node.placeholder, index + 1)
                if hit is not None:
                    return hit
                captured.pop()
            return None

        hit = walk(self._root, 0)
        return hit, captured, method_mismatch


ROUTER: Final = Router(routes.ALL)
