"""Regenerate the committed spec artifacts under ``src/stripeapi/spec/``.

Reads the pinned Stripe OpenAPI snapshot (``research/stripe-openapi/spec3.json``,
git-ignored; provenance in ``research/MANIFEST.md``) and the route table
(``src/stripeapi/dispatch/routes.py``) and writes:

- ``spec3.min.json`` — only the routed operations, only the reachable schemas,
  with HTML stripped from every summary/description;
- ``spec3.min.json.sha256`` — the sha256 of the exact source blob, so CI can
  check provenance without the 8 MB file;
- ``expandable.py`` — each closure schema's non-empty ``x-expandableFields``;
- ``enums.py`` — the six hand-transcribed doc-only enum sets;
- ``event_types.py`` — the committed 266-entry closed set, verbatim.

Usage::

    python -m tools_dev.prune_spec                     # regenerate and write
    python -m tools_dev.prune_spec --check             # diff in memory, exit 1 on drift
    python -m tools_dev.prune_spec --bootstrap-routes  # rewrite routes.py's table

``--bootstrap-routes`` derives the 148-entry table mechanically from the spec.
It is a bootstrap command: once the dispatcher phase attaches handlers to the
entries by hand it no longer round-trips, and normal mode (which imports the
committed table as the authority) is the only one used thereafter.

The generated subset of Stripe's spec is MIT-licensed material; the notice
lives in ``THIRD_PARTY_LICENSES.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple

from stripeapi.dispatch.routes import Route

REPO = Path(__file__).resolve().parents[1]
SPEC_PATH = REPO / "research/stripe-openapi/spec3.json"
SPEC_DIR = REPO / "src/stripeapi/spec"
ROUTES_PY = REPO / "src/stripeapi/dispatch/routes.py"
EVENT_TYPES_TXT = (
    REPO / "specs/projects/stripe_world/research/stripe-billing-and-payments/"
    "api-surface-and-object-graph/event-types-closed-set.txt"
)

API_VERSION = "2026-08-26.dahlia"
ROUTE_COUNT = 148
# Regression guards against a spec bump silently reintroducing the closure
# inflation (components/discovery.md §3): crossing either forces a human
# decision rather than a quiet multi-hundred-schema regrowth.
SIZE_BUDGET_BYTES = 2_500_000
SCHEMA_BUDGET = 500

# The 22 resource-root prefixes of the routed surface (functional spec §3.1).
ROOTS = (
    "/v1/customers",
    "/v1/payment_methods",
    "/v1/products",
    "/v1/prices",
    "/v1/coupons",
    "/v1/promotion_codes",
    "/v1/tax_rates",
    "/v1/payment_intents",
    "/v1/charges",
    "/v1/refunds",
    "/v1/disputes",
    "/v1/setup_intents",
    "/v1/balance_transactions",
    "/v1/payouts",
    "/v1/balance",
    "/v1/subscriptions",
    "/v1/subscription_items",
    "/v1/invoices",
    "/v1/invoiceitems",
    "/v1/credit_notes",
    "/v1/subscription_schedules",
    "/v1/events",
)

# The customer sub-resource families cut outright (functional spec §3.2). The
# legacy /v1/customers/{customer}/subscriptions* alias family is NOT here: the
# 148 routes it (components/dispatcher.md §3.1.3), as it does
# customers/{customer}/payment_methods (read-only alias) and
# customers/{customer}/discount (embedded object).
CUT_CUSTOMER_SUBRESOURCES = frozenset(
    {
        "bank_accounts",
        "cards",
        "sources",
        "cash_balance",
        "cash_balance_transactions",
        "tax_ids",
        "funding_instructions",
    }
)

# Schemas the closure walker never enters, per the rulings in
# specs/.../api-surface-and-object-graph/scope-boundary-edges.md. The list is
# representative of that document's summary table rather than exhaustive; the
# residual-schema drift test is the backstop for what transcription misses
# (components/discovery.md §2). ``topup`` rides with the Connect/Tax
# stragglers: a balance_transaction.source member for a product this world
# does not route.
STOPLIST_NAMES = frozenset(
    {
        # Connect.
        "account",
        "application",
        "transfer",
        "transfer_reversal",
        "connect_collection_transfer",
        "reserve_transaction",
        "application_fee",
        "application_fee_refund",
        "person",
        # Stripe Tax (the automatic-calculation product, not flat tax_rates).
        "customer_tax",
        "tax_code",
        # Radar.
        "review",
        "radar_radar_options",
        # Checkout Sessions / Payment Links.
        "checkout.session",
        "payment_link",
        # Legacy Sources/Cards/BankAccounts (`card` here is the legacy
        # top-level schema; payment_method_card is kept).
        "source",
        "card",
        "bank_account",
        "deleted_source",
        "deleted_card",
        "deleted_bank_account",
        # Stubbed as id-only strings, never resolved.
        "mandate",
        "setup_attempt",
        # Cash Balance, a separate product from customer_balance_transaction.
        "cash_balance",
        "customer_cash_balance_transaction",
        # Managed Payments, out of declared scope.
        "smor_resource_managed_payments",
        # balance_transaction.source stragglers (components/discovery.md §2).
        "fee_refund",
        "tax_deducted_at_source",
        "topup",
    }
)
STOPLIST_PREFIXES = (
    "tax.",
    "tax_product_",
    "issuing.",
    "issuing_",
    "treasury.",
    "terminal.",
    "capital.",
    "climate.",
    "financial_connections",
    "checkout.",
)

# The payment rails modelled in full; every other rail stays only as a stubbed
# {type} member (scope-boundary-edges.md's rail ruling; `link` rides with card
# and us_bank_account — components/discovery.md §3).
KEEP_RAILS = frozenset({"card", "us_bank_account", "link"})

# The six bare-string-but-closed-set fields, hand-transcribed from
# resource-inventory.md's citations of the spec's own description prose. These
# cannot be regex-extracted reliably (components/discovery.md §5); a cited
# hand transcription pinned by test is the honest mechanism.
# `setup_intent.usage` is deliberately absent: it is an open, forward-compatible
# string with a documented default, not a closed set.
DOC_ONLY_ENUMS: dict[str, dict[str, tuple[str, ...]]] = {
    "balance_transaction": {
        # resource-inventory.md, balance_transaction.status.
        "status": ("available", "pending"),
    },
    "dispute": {
        # resource-inventory.md, dispute.reason (15 values, from description).
        "reason": (
            "bank_cannot_process",
            "check_returned",
            "credit_not_processed",
            "customer_initiated",
            "debit_not_authorized",
            "duplicate",
            "fraudulent",
            "general",
            "incorrect_account_details",
            "insufficient_funds",
            "noncompliant",
            "product_not_received",
            "product_unacceptable",
            "subscription_canceled",
            "unrecognized",
        ),
    },
    "payout": {
        # resource-inventory.md, payout.{method,source_type,status}.
        "method": ("standard", "instant"),
        "source_type": ("card", "fpx", "bank_account"),
        "status": ("paid", "pending", "in_transit", "canceled", "failed"),
    },
    "refund": {
        # resource-inventory.md, refund.status.
        "status": ("pending", "requires_action", "succeeded", "failed", "canceled"),
    },
}

REF_PATTERN = re.compile(r"#/components/schemas/([A-Za-z0-9_.\-]+)")
TAG_PATTERN = re.compile(r"<[^>]+>")
# Block-level tags become a separator; the rest (code, a, em, Stripe's inline
# currency/amount/api marks) vanish, so "<code>x</code>." does not grow a
# space before the period. The sets come from the tags actually present in
# the spec: p, code, a, currency, strong, li, br, amount, ul, em, api, base64.
BLOCK_TAG_PATTERN = re.compile(r"</?(?:p|li|ul|ol|br|div|h[1-6]|table|tr)\b[^>]*>")
OPERATION_KEYS = ("summary", "description", "operationId", "parameters", "requestBody", "responses")

GENERATED_HEADER = (
    "Generated by tools_dev/prune_spec.py from Stripe's OpenAPI spec — do not edit.\n"
    "\n"
    "Regenerate with `python -m tools_dev.prune_spec`. The source spec carries the\n"
    "MIT licence; see THIRD_PARTY_LICENSES.md."
)


def _docstring(body: str) -> str:
    return f'"""{GENERATED_HEADER}\n\n{body}\n"""\n'


class GenerationError(Exception):
    """A route table or spec input the pruner refuses to guess around."""


# --- The routed operation set, derived from the spec -------------------------


def derive_operations(
    full_spec: dict[str, Any], *, expected_count: int = ROUTE_COUNT
) -> tuple[Route, ...]:
    """The 148 routed operations, mechanically: the 22 resource-root prefixes
    minus the 39 cuts (components/dispatcher.md §1.3). The count is asserted
    (`expected_count` exists so a synthetic spec can exercise the cut rules),
    so a spec bump that moves it fails generation instead of drifting scope."""
    routes: list[Route] = []
    for path, path_item in full_spec["paths"].items():
        if "/".join(path.split("/")[:3]) not in ROOTS:
            continue
        for method, operation in path_item.items():
            if method not in ("get", "post", "delete"):
                continue
            segments = [s for s in path.split("/") if s]
            if len(segments) == 3 and segments[2] == "search" and method == "get":
                continue
            if path.startswith("/v1/balance/history"):
                continue
            if segments[1] == "products" and "features" in segments:
                continue
            if (
                len(segments) >= 4
                and segments[1] == "customers"
                and segments[3] in CUT_CUSTOMER_SUBRESOURCES
            ):
                continue
            routes.append(
                Route(method=method.upper(), pattern=path, op_id=operation["operationId"])
            )
    if len(routes) != expected_count:
        msg = f"derived {len(routes)} routed operations, expected exactly {expected_count}"
        raise GenerationError(msg)
    return tuple(routes)


# --- The schema transforms ----------------------------------------------------


def _stoplisted(name: str) -> bool:
    return name in STOPLIST_NAMES or name.startswith(STOPLIST_PREFIXES)


def _member_ref(node: Any) -> str | None:
    """The single schema a union member points at, through one-member
    wrappers; None when the member is a plain type."""
    if isinstance(node, dict):
        if "$ref" in node:
            match = REF_PATTERN.fullmatch(node["$ref"])
            return match.group(1) if match else None
        for key in ("anyOf", "oneOf", "allOf"):
            if isinstance(node.get(key), list) and len(node[key]) == 1:
                target = _member_ref(node[key][0])
                if target:
                    return target
    return None


def _apply_stoplist(node: Any, in_expansion: bool = False) -> Any:
    """Drop stoplisted schema references wherever they appear.

    A union (anyOf/oneOf/allOf) keeps its non-stoplisted members as an array;
    one left empty promotes the id-only string stub onto the node itself (the
    union key goes) — these unions almost always carry a bare string sibling.
    Inside ``x-expansionResources`` an emptied union means the field can no
    longer expand into anything, so the key simply goes. A direct ``$ref`` to
    a stoplisted schema becomes the null stub those rulings describe
    (scope-boundary-edges.md: "null / omit").
    """
    if isinstance(node, dict):
        if "$ref" in node:
            match = REF_PATTERN.fullmatch(node["$ref"])
            if match and _stoplisted(match.group(1)):
                return {"type": "null"}
        out: dict[str, Any] = {}
        stub = False
        for key, value in node.items():
            if key in ("anyOf", "oneOf", "allOf") and isinstance(value, list):
                kept = [
                    member
                    for member in value
                    if not (target := _member_ref(member)) or not _stoplisted(target)
                ]
                processed = [
                    member
                    for member in (_apply_stoplist(m, in_expansion=in_expansion) for m in kept)
                    if member
                ]
                if processed:
                    out[key] = processed
                elif not in_expansion:
                    stub = True
            elif key == "x-expansionResources":
                trimmed = _apply_stoplist(value, in_expansion=True)
                if trimmed:
                    out[key] = trimmed
            else:
                out[key] = _apply_stoplist(value)
        if stub and "type" not in out:
            out["type"] = "string"
        return out
    if isinstance(node, list):
        return [_apply_stoplist(member, in_expansion=in_expansion) for member in node]
    return node


def _drop_rails(node: Any, rails: frozenset[str]) -> Any:
    """Drop properties named for a stubbed payment rail.

    Matching by property name against the payment_method.type enum catches
    every per-rail fan-out hub (payment_method, the *_details hubs, the
    options hubs, and any future wrapper) without keeping a list of hubs
    (components/discovery.md §3). Dropped properties leave ``required`` and
    ``x-expandableFields`` too.
    """
    if isinstance(node, dict):
        node = dict(node)
        properties = node.get("properties")
        if isinstance(properties, dict):
            dropped = {name for name in properties if name in rails and name not in KEEP_RAILS}
            if dropped:
                node["properties"] = {k: v for k, v in properties.items() if k not in dropped}
                if "required" in node:
                    required = [name for name in node["required"] if name not in dropped]
                    if required:
                        node["required"] = required
                    else:
                        node.pop("required")
        if isinstance(node.get("x-expandableFields"), list):
            node["x-expandableFields"] = [
                field
                for field in node["x-expandableFields"]
                if not (field in rails and field not in KEEP_RAILS)
            ]
        return {
            key: (value if key == "x-expandableFields" else _drop_rails(value, rails))
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_drop_rails(item, rails) for item in node]
    return node


def _strip_markup(text: str) -> str:
    """Stripe's prose arrives HTML-wrapped; discovery serves this text
    directly, so the tags go here, once, at generation time. Block tags
    become separators, inline tags vanish, entities unescape, and whitespace
    runs collapse — which is also what makes the no-`<>` drift test clean."""
    untagged = TAG_PATTERN.sub("", BLOCK_TAG_PATTERN.sub(" ", text))
    return re.sub(r"\s+", " ", html.unescape(untagged)).strip()


def _strip_all_markup(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            key: (
                _strip_markup(value)
                if key in ("description", "summary") and isinstance(value, str)
                else _strip_all_markup(value)
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_strip_all_markup(item) for item in node]
    return node


def _harvest_refs(node: Any, out: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref":
                match = REF_PATTERN.fullmatch(value)
                if match:
                    out.add(match.group(1))
            else:
                _harvest_refs(value, out)
    elif isinstance(node, list):
        for item in node:
            _harvest_refs(item, out)


# --- Artifact construction ----------------------------------------------------


class Artifacts(NamedTuple):
    spec3_min: dict[str, Any]
    expandable: dict[str, tuple[str, ...]]
    enums: dict[str, dict[str, tuple[str, ...]]]
    event_types: frozenset[str]


def _filter_request_surface(trimmed: dict[str, Any], route: Route) -> dict[str, Any]:
    """Discovery §8a: a wired route is documented only with the parameters
    this world's `ParamSpec` accepts — in the request body *and* in the spec's
    query `parameters`, which otherwise arrive verbatim (`test_clock` on
    `GET /v1/customers` was documented and then rejected as
    `parameter_unknown`: exactly the "advertised but unimplemented" hole this
    rule exists to close, one level below paths).

    Path placeholders (`in: "path"`) are always kept: they name the pattern's
    own segments. The central parameters are kept where the spec accepts
    them: `expand` when `ParamSpec.expand`, the three pagination parameters
    when `ParamSpec.paginated`, `metadata` when `ParamSpec.metadata`. An
    *unwired* route (no `ParamSpec` yet — its resource phase has not landed)
    keeps everything verbatim until that phase wires it and the artifacts are
    regenerated.
    """
    spec = route.params
    if spec is None:
        return trimmed
    from stripeapi.dispatch.params import body_of

    allowed_body = {param.name for param in body_of(route)}
    if spec.expand:
        allowed_body.add("expand")
    if spec.metadata:
        allowed_body.add("metadata")
    allowed_query = set(allowed_body)
    if spec.paginated:
        allowed_query |= {"limit", "starting_after", "ending_before"}

    parameters = trimmed.get("parameters")
    if isinstance(parameters, list):
        trimmed["parameters"] = [
            parameter
            for parameter in parameters
            if parameter.get("in") == "path" or parameter.get("name") in allowed_query
        ]
    if "requestBody" in trimmed:
        body = trimmed["requestBody"]
        for media in body.get("content", {}).values():
            schema = media.get("schema")
            if not isinstance(schema, dict) or "properties" not in schema:
                continue
            schema["properties"] = {
                name: prop for name, prop in schema["properties"].items() if name in allowed_body
            }
            if "required" in schema:
                schema["required"] = [name for name in schema["required"] if name in allowed_body]
                if not schema["required"]:
                    schema.pop("required")
    return trimmed


def build_artifacts(
    full_spec: dict[str, Any], routes: Sequence[Route], event_types: frozenset[str]
) -> Artifacts:
    """Pure: (full spec3.json, the route table, the event-type set) -> the
    four artifacts' in-memory contents. No filesystem I/O, so ``--check`` can
    diff without writing (components/discovery.md)."""
    if full_spec["info"]["version"] != API_VERSION:
        msg = f"spec version {full_spec['info']['version']!r} != pinned {API_VERSION!r}"
        raise GenerationError(msg)
    paths = full_spec["paths"]
    schemas = full_spec["components"]["schemas"]

    seen: set[tuple[str, str]] = set()
    pruned_paths: dict[str, dict[str, Any]] = {}
    frontier: set[str] = set()
    for route in routes:
        key = (route.method.lower(), route.pattern)
        if key in seen:
            msg = f"duplicate route {route.method} {route.pattern}"
            raise GenerationError(msg)
        seen.add(key)
        path_item = paths.get(route.pattern)
        if not isinstance(path_item, dict) or route.method.lower() not in path_item:
            msg = f"route {route.method} {route.pattern} has no match in the spec"
            raise GenerationError(msg)
        operation = path_item[route.method.lower()]
        if operation.get("operationId") != route.op_id:
            msg = (
                f"route {route.method} {route.pattern} op_id {route.op_id!r} "
                f"!= spec operationId {operation.get('operationId')!r}"
            )
            raise GenerationError(msg)
        trimmed = _strip_all_markup(
            {op_key: operation[op_key] for op_key in OPERATION_KEYS if op_key in operation}
        )
        trimmed = _filter_request_surface(trimmed, route)
        pruned_paths.setdefault(route.pattern, {})[route.method.lower()] = trimmed
        _harvest_refs(trimmed, frontier)

    rails = frozenset(schemas["payment_method"]["properties"]["type"]["enum"])
    closure: set[str] = set()
    bodies: dict[str, Any] = {}
    todo = sorted(name for name in frontier if not _stoplisted(name))
    while todo:
        name = todo.pop()
        if name in closure or name not in schemas or _stoplisted(name):
            continue
        closure.add(name)
        body = _strip_all_markup(_drop_rails(_apply_stoplist(schemas[name]), rails))
        bodies[name] = body
        found: set[str] = set()
        _harvest_refs(body, found)
        for next_name in sorted(found):
            if next_name not in closure:
                todo.append(next_name)

    if len(closure) >= SCHEMA_BUDGET:
        msg = f"closure reached {len(closure)} schemas, over the {SCHEMA_BUDGET} budget"
        raise GenerationError(msg)

    spec3_min = {
        "openapi": full_spec["openapi"],
        "info": full_spec["info"],
        "paths": pruned_paths,
        "components": {"schemas": {name: bodies[name] for name in sorted(closure)}},
    }
    expandable = {
        name: tuple(body["x-expandableFields"])
        for name, body in sorted(bodies.items())
        if body.get("x-expandableFields")
    }
    return Artifacts(
        spec3_min=spec3_min,
        expandable=expandable,
        enums=DOC_ONLY_ENUMS,
        event_types=event_types,
    )


# --- Rendering the committed files --------------------------------------------


def _dump_canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _tuple_literal(values: tuple[str, ...], prefix: str) -> str:
    """A tuple literal as one dict entry, in double quotes — `ruff format`
    normalizes repr's single quotes, so the renderer emits the canonical form
    itself. A one-element tuple keeps its trailing comma (otherwise it is not
    a tuple at all); an entry too long for one line explodes with the magic
    trailing comma that holds that shape stable under the formatter."""
    if not values:
        return "()"
    inline = (
        "(" + ", ".join(f'"{value}"' for value in values) + ("," if len(values) == 1 else "") + ")"
    )
    if len(f"{prefix}{inline},") <= 100:
        return inline
    base = prefix[: len(prefix) - len(prefix.lstrip())]
    inner = f"{base}    "
    return "(\n" + "".join(f'{inner}"{value}",\n' for value in values) + f"{base})"


def _render_expandable(artifacts: Artifacts) -> str:
    docstring = _docstring(
        "Which fields of each object may appear in an `expand[]` path, verbatim\n"
        "from the pruned spec's non-empty `x-expandableFields` arrays. The\n"
        "expansion resolver reads this for legality; the target object type is\n"
        "resolved at request time against spec3.min.json, not precomputed here\n"
        "(components/discovery.md §5)."
    )
    lines = [
        docstring,
        "EXPANDABLE_FIELDS: dict[str, tuple[str, ...]] = {",
    ]
    for name, fields in artifacts.expandable.items():
        prefix = f'    "{name}": '
        literal = _tuple_literal(fields, prefix)
        entry = f"{prefix}{literal},".splitlines()
        # A schema name can alone overflow the line; the formatter cannot
        # split a string, so the generator silences the length lint itself,
        # on the long line.
        if len(entry[0]) > 100:
            entry[0] += "  # noqa: E501"
        lines.append("\n".join(entry))
    lines.append("}")
    return "\n".join(lines) + "\n"


def _render_enums(artifacts: Artifacts) -> str:
    docstring = _docstring(
        "The six fields whose closed value set exists only in the spec's\n"
        "description prose (no machine-readable `enum`), hand-transcribed from\n"
        "resource-inventory.md's citations. The schema's CHECK constraints and\n"
        "the conformance validator both read this, so the two cannot disagree.\n"
        "`setup_intent.usage` is deliberately absent — an open string with a\n"
        "documented default, not a closed set (components/discovery.md §5)."
    )
    lines = [
        docstring,
        "DOC_ONLY_ENUMS: dict[str, dict[str, tuple[str, ...]]] = {",
    ]
    for obj, fields in sorted(artifacts.enums.items()):
        lines.append(f'    "{obj}": {{')
        for field, values in sorted(fields.items()):
            prefix = f'        "{field}": '
            lines.append(f"{prefix}{_tuple_literal(values, prefix)},")
        lines.append("    },")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _render_event_types(artifacts: Artifacts) -> str:
    docstring = _docstring(
        "Every `event.type` value Stripe defines: 266 entries, the union of\n"
        "Stripe's published list and stripe-python's generated enum, verbatim\n"
        "from the committed event-types-closed-set.txt. `emit_event` validates\n"
        "against this whole set at call time; it is deliberately not filtered\n"
        "to routed resources (functional spec §6.6)."
    )
    lines = [
        docstring,
        "EVENT_TYPES: frozenset[str] = frozenset(",
        "    {",
    ]
    lines.extend(f'        "{name}",' for name in sorted(artifacts.event_types))
    lines.append("    }")
    lines.append(")")
    return "\n".join(lines) + "\n"


ROUTES_TEMPLATE = '''"""The routing table: the 148-operation scope of this world. Data, not code.

Every entry's `(method, pattern)` matches the pinned spec3.json verbatim,
placeholder names included, and the spec pipeline validates that on every
regeneration — so a typo'd path or a spec bump that moves the surface fails
generation rather than silently narrowing discovery (components/discovery.md
§1). Handlers and ParamSpecs attach to these entries in the dispatcher phase;
`alias_of` will mark the eight legacy aliases that share a canonical handler
(components/dispatcher.md §3.1.3).

Bootstrapped mechanically by `python -m tools_dev.prune_spec --bootstrap-routes`
and hand-maintained thereafter.
"""

from dataclasses import dataclass
from typing import Final

__all__ = ["ALL", "Route"]


@dataclass(frozen=True, slots=True)
class Route:
    """One routed operation: the HTTP verb, the path pattern with `{placeholder}`
    segments as they appear in the spec, and the spec's own operationId."""

    method: str
    pattern: str
    op_id: str
'''

METHOD_ORDER = {"GET": 0, "POST": 1, "DELETE": 2}


def _render_routes(routes: Sequence[Route]) -> str:
    lines = [ROUTES_TEMPLATE, "", "ALL: Final[tuple[Route, ...]] = ("]
    by_root: dict[str, list[Route]] = {}
    for route in routes:
        root = "/".join(route.pattern.split("/")[:3])
        by_root.setdefault(root, []).append(route)
    for root in sorted(by_root):
        lines.append(f"    # {root.removeprefix('/v1/')}")
        for route in sorted(by_root[root], key=lambda r: (r.pattern, METHOD_ORDER[r.method])):
            inline = (
                f'Route(method="{route.method}", pattern="{route.pattern}", op_id="{route.op_id}"),'
            )
            if len(f"    {inline}") <= 100:
                lines.append(f"    {inline}")
            else:
                # The trailing comma is magic: it holds the one-argument-per-line
                # shape stable under `ruff format --check`.
                lines.append("    Route(")
                lines.append(f'        method="{route.method}",')
                lines.append(f'        pattern="{route.pattern}",')
                lines.append(f'        op_id="{route.op_id}",')
                lines.append("    ),")
    lines.append(")")
    return "\n".join(lines) + "\n"


# --- The CLI ------------------------------------------------------------------


def _load_full_spec() -> tuple[dict[str, Any], str]:
    if not SPEC_PATH.is_file():
        msg = (
            f"{SPEC_PATH} is absent. It is git-ignored; fetch it with the "
            "re-record command in research/MANIFEST.md and verify its sha256."
        )
        raise SystemExit(msg)
    blob = SPEC_PATH.read_bytes()
    return json.loads(blob), hashlib.sha256(blob).hexdigest()


def _load_event_types() -> frozenset[str]:
    return frozenset(
        line.strip() for line in EVENT_TYPES_TXT.read_text().splitlines() if line.strip()
    )


def _committed(name: str) -> str | None:
    path = SPEC_DIR / name
    return path.read_text() if path.is_file() else None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate in memory and exit non-zero if any committed file would change",
    )
    parser.add_argument(
        "--bootstrap-routes",
        action="store_true",
        help="mechanically rewrite dispatch/routes.py's table from the spec (bootstrap only)",
    )
    args = parser.parse_args(argv)

    full_spec, source_sha = _load_full_spec()

    if args.bootstrap_routes:
        ROUTES_PY.write_text(_render_routes(derive_operations(full_spec)))
        print(f"wrote {ROUTES_PY.relative_to(REPO)}")
        return 0

    from stripeapi.dispatch import routes as routes_module

    artifacts = build_artifacts(full_spec, routes_module.ALL, _load_event_types())
    expected: dict[str, str] = {
        "spec3.min.json": _dump_canonical(artifacts.spec3_min) + "\n",
        "spec3.min.json.sha256": f"{source_sha}\n",
        "expandable.py": _render_expandable(artifacts),
        "enums.py": _render_enums(artifacts),
        "event_types.py": _render_event_types(artifacts),
    }

    if args.check:
        drifted = [name for name, content in expected.items() if _committed(name) != content]
        for name in drifted:
            print(f"drifted: src/stripeapi/spec/{name}", file=sys.stderr)
        return 1 if drifted else 0

    for name, content in expected.items():
        (SPEC_DIR / name).write_text(content)
        print(f"wrote src/stripeapi/spec/{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
