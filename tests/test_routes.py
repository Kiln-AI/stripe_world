"""The routing table's invariants: the 148 count is the scope tripwire, and the
table is the single source of which operations exist anywhere in this world
(architecture.md §3.1).
"""

import json
import re
from pathlib import Path

import pytest

from seahaven_stripe_world.dispatch import routes

REPO = Path(__file__).resolve().parents[1]
SPEC_PATH = REPO / "research/stripe-openapi/spec3.json"

PLACEHOLDER = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")


def route_keys(table: tuple[routes.Route, ...]) -> set[tuple[str, str]]:
    return {(route.method, route.pattern) for route in table}


def test_route_count_is_148() -> None:
    """The scope-drift tripwire: becomes 155 when search lands (§3.3)."""
    assert len(routes.ALL) == 148


def test_method_pattern_pairs_are_unique() -> None:
    assert len(route_keys(routes.ALL)) == len(routes.ALL)


def test_op_ids_are_unique() -> None:
    op_ids = [route.op_id for route in routes.ALL]
    assert len(set(op_ids)) == len(op_ids)


def test_methods_are_the_three_routed_verbs() -> None:
    assert {route.method for route in routes.ALL} == {"GET", "POST", "DELETE"}


def test_patterns_are_v1_paths_with_identifier_placeholders() -> None:
    """Patterns must match the spec's paths verbatim, placeholder names
    included — the pruner validates this on every regeneration, and this keeps
    the table honest between regenerations."""
    for route in routes.ALL:
        assert route.pattern.startswith("/v1/")
        for segment in route.pattern.split("/")[2:]:
            assert segment.isascii() and ("{" not in segment or PLACEHOLDER.fullmatch(segment)), (
                route.pattern
            )


def test_legacy_alias_families_are_routed() -> None:
    """The eight legacy aliases the 148 includes (dispatcher.md §3.1.3), plus
    the read-only payment_methods alias and the embedded-discount endpoints."""
    keys = route_keys(routes.ALL)
    for method, pattern in [
        ("POST", "/v1/charges/{charge}/refund"),
        ("POST", "/v1/charges/{charge}/refunds"),
        ("POST", "/v1/customers/{customer}/subscriptions"),
        ("POST", "/v1/customers/{customer}/subscriptions/{subscription_exposed_id}"),
        ("DELETE", "/v1/customers/{customer}/subscriptions/{subscription_exposed_id}"),
        (
            "DELETE",
            "/v1/customers/{customer}/subscriptions/{subscription_exposed_id}/discount",
        ),
        ("POST", "/v1/charges/{charge}/dispute"),
        ("POST", "/v1/charges/{charge}/dispute/close"),
        ("GET", "/v1/customers/{customer}/payment_methods"),
        ("GET", "/v1/customers/{customer}/payment_methods/{payment_method}"),
        ("GET", "/v1/customers/{customer}/discount"),
        ("DELETE", "/v1/customers/{customer}/discount"),
        ("GET", "/v1/customers/{customer}/balance_transactions"),
        ("POST", "/v1/customers/{customer}/balance_transactions"),
    ]:
        assert (method, pattern) in keys


def test_cut_operations_are_absent() -> None:
    """The 39 cuts (§3.2/§3.3): search, legacy sub-resources, features,
    balance/history — none of them may appear in the table."""
    keys = route_keys(routes.ALL)
    assert ("GET", "/v1/customers/search") not in keys
    assert ("GET", "/v1/subscriptions/search") not in keys
    assert ("GET", "/v1/balance/history") not in keys
    assert ("GET", "/v1/balance/history/{id}") not in keys
    for sub in ("sources", "cards", "bank_accounts", "tax_ids", "cash_balance"):
        assert ("GET", f"/v1/customers/{{customer}}/{sub}") not in keys
    assert ("GET", "/v1/customers/{customer}/funding_instructions") not in keys
    assert ("GET", "/v1/products/{product}/features") not in keys


@pytest.mark.skipif(
    not SPEC_PATH.is_file(),
    reason="research/stripe-openapi/spec3.json is git-ignored; fetch it per research/MANIFEST.md",
)
def test_table_equals_the_mechanical_derivation() -> None:
    """The committed table is exactly what derive_operations produces from the
    pinned spec, both directions — no hand drift between bootstraps.

    Compared on `(method, pattern, op_id)`: from the dispatcher phase on the
    hand-maintained entries carry wiring the mechanical derivation does not,
    which is why `--bootstrap-routes` stopped being round-trippable.
    """
    from tools_dev.prune_spec import derive_operations

    derived = derive_operations(json.loads(SPEC_PATH.read_text()))

    def sort_key(route: routes.Route) -> tuple[str, str, str]:
        return (route.method, route.pattern, route.op_id)

    assert route_keys(derived) == route_keys(routes.ALL)
    assert sorted(map(sort_key, derived)) == sorted(map(sort_key, routes.ALL))
