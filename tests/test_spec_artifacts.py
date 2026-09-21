"""The drift tests: CI proves internal consistency (routes ↔ spec3.min.json ↔
enums ↔ event types ↔ the MANIFEST's pinned hash) without the 8 MB source
spec, and the regeneration byte-diff runs whenever the spec is on disk
(components/discovery.md §4's two tiers).
"""

import json
import re
from pathlib import Path

import pytest
from tools_dev.prune_spec import (
    API_VERSION,
    KEEP_RAILS,
    SCHEMA_BUDGET,
    SIZE_BUDGET_BYTES,
    SPEC_PATH,
    STOPLIST_NAMES,
    STOPLIST_PREFIXES,
)
from tools_dev.prune_spec import (
    main as prune_main,
)

from seahaven_stripe_world.dispatch import routes
from seahaven_stripe_world.spec.enums import DECLINE_CODES, DOC_ONLY_ENUMS
from seahaven_stripe_world.spec.event_types import EVENT_TYPES
from seahaven_stripe_world.spec.expandable import EXPANDABLE_FIELDS

REPO = Path(__file__).resolve().parents[1]
SPEC_DIR = REPO / "src/seahaven_stripe_world/spec"
MIN_JSON = SPEC_DIR / "spec3.min.json"
EVENT_TYPES_TXT = (
    REPO / "specs/projects/stripe_world/research/stripe-billing-and-payments/"
    "api-surface-and-object-graph/event-types-closed-set.txt"
)
MANIFEST = REPO / "research/MANIFEST.md"

MIN = json.loads(MIN_JSON.read_text())


def min_path_keys() -> set[tuple[str, str]]:
    return {
        (method.upper(), path)
        for path, item in MIN["paths"].items()
        for method in ("get", "post", "delete")
        if method in item
    }


def walk(node: object):
    """Every dict, recursively — for scans that must see all of a tree."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item)


# --- tier 1: runs in every CI build -------------------------------------------


def test_min_paths_match_routes_both_directions() -> None:
    route_keys = {(route.method, route.pattern) for route in routes.ALL}
    spec_keys = min_path_keys()
    assert spec_keys == route_keys


def test_api_version_is_pinned() -> None:
    assert MIN["info"]["version"] == API_VERSION == "2026-08-26.dahlia"


def test_no_stoplisted_schema_survives_anywhere() -> None:
    """The completeness backstop for the hand-transcribed stoplist: no schema
    key and no $ref target in the artifact may be out of scope."""
    blob = MIN["components"]["schemas"]
    for name in blob:
        assert name not in STOPLIST_NAMES, name
        assert not name.startswith(STOPLIST_PREFIXES), name
    for node in walk(MIN):
        target = node.get("$ref")
        if isinstance(target, str):
            match = re.fullmatch(r"#/components/schemas/(.+)", target)
            assert match is not None, target
            assert match.group(1) in blob, f"dangling $ref {target}"


def test_only_kept_rails_remain() -> None:
    """No schema may carry a property named for a stubbed rail — by name,
    which catches every fan-out hub (discovery.md §3)."""
    rails = set(MIN["components"]["schemas"]["payment_method"]["properties"]["type"]["enum"])
    stubbed = rails - set(KEEP_RAILS)
    for name, schema in MIN["components"]["schemas"].items():
        properties = schema.get("properties", {})
        assert not (set(properties) & stubbed), name
        assert not (set(schema.get("x-expandableFields", [])) & stubbed), name


def test_unions_are_non_empty_arrays() -> None:
    """A union the transforms emptied must have been replaced by the string
    stub, never shipped as an empty or non-array anyOf/oneOf/allOf."""
    for node in walk(MIN):
        for key in ("anyOf", "oneOf", "allOf"):
            if key in node:
                assert isinstance(node[key], list) and node[key], key


def test_no_markup_survives_in_prose() -> None:
    for node in walk(MIN):
        for key in ("description", "summary"):
            if isinstance(node.get(key), str):
                assert "<" not in node[key] and ">" not in node[key], node[key]


def test_size_budget() -> None:
    """Regression guard against a spec bump silently reintroducing the
    closure inflation (discovery.md §3)."""
    assert MIN_JSON.stat().st_size < SIZE_BUDGET_BYTES
    assert len(MIN["components"]["schemas"]) < SCHEMA_BUDGET


def test_provenance_sidecar_matches_the_manifest() -> None:
    """The committed artifact claims descent from the exact spec blob the
    manifest pins — checkable without the 8 MB file itself."""
    sidecar = (SPEC_DIR / "spec3.min.json.sha256").read_text().strip()
    manifest_row = next(
        line for line in MANIFEST.read_text().splitlines() if "spec3.json |" in line
    )
    pinned = re.search(r"`([0-9a-f]{64})`", manifest_row)
    assert pinned is not None
    assert sidecar == pinned.group(1)


def test_event_types_match_the_committed_txt() -> None:
    committed = frozenset(
        line.strip() for line in EVENT_TYPES_TXT.read_text().splitlines() if line.strip()
    )
    assert committed == EVENT_TYPES
    assert len(EVENT_TYPES) == 266


def test_decline_codes_are_the_fifty_transcribed() -> None:
    """`decline_code` has no machine-readable enum anywhere (docs table only,
    gap-closure-2026-09-18.md item 7), so the committed count is the drift
    guard — the promise `spec/enums.py`'s docstring makes."""
    assert len(DECLINE_CODES) == 50
    assert frozenset(DECLINE_CODES) == DECLINE_CODES  # no duplicates inflating it


def test_doc_only_enums_are_exactly_the_six_pinned() -> None:
    """A value silently changing is exactly the drift this test catches."""
    assert DOC_ONLY_ENUMS == {
        "balance_transaction": {"status": ("available", "pending")},
        "dispute": {
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
            )
        },
        "payout": {
            "method": ("standard", "instant"),
            "source_type": ("card", "fpx", "bank_account"),
            "status": ("paid", "pending", "in_transit", "canceled", "failed"),
        },
        "refund": {"status": ("pending", "requires_action", "succeeded", "failed", "canceled")},
    }
    assert len({(obj, field) for obj, fields in DOC_ONLY_ENUMS.items() for field in fields}) == 6


def test_expandable_spot_checks() -> None:
    assert EXPANDABLE_FIELDS["customer"] == (
        "address",
        "cash_balance",
        "default_source",
        "discount",
        "invoice_settings",
        "shipping",
        "sources",
        "subscriptions",
        "tax",
        "tax_ids",
        "test_clock",
    )
    assert EXPANDABLE_FIELDS["subscription"] == (
        "application",
        "automatic_tax",
        "billing_cycle_anchor_config",
        "billing_mode",
        "billing_schedules",
        "billing_thresholds",
        "cancellation_details",
        "customer",
        "default_payment_method",
        "default_source",
        "default_tax_rates",
        "discounts",
        "invoice_settings",
        "items",
        "latest_invoice",
        "managed_payments",
        "on_behalf_of",
        "pause_collection",
        "payment_settings",
        "pending_invoice_item_interval",
        "pending_setup_intent",
        "pending_update",
        "presentment_details",
        "schedule",
        "test_clock",
        "transfer_data",
        "trial_settings",
    )
    assert EXPANDABLE_FIELDS["invoice"] == (
        "account_tax_ids",
        "application",
        "automatic_tax",
        "confirmation_secret",
        "custom_fields",
        "customer",
        "customer_address",
        "customer_shipping",
        "customer_tax_ids",
        "default_payment_method",
        "default_source",
        "default_tax_rates",
        "discounts",
        "from_invoice",
        "issuer",
        "last_finalization_error",
        "latest_revision",
        "lines",
        "on_behalf_of",
        "parent",
        "payment_settings",
        "payments",
        "rendering",
        "shipping_cost",
        "shipping_details",
        "status_transitions",
        "test_clock",
        "threshold_reason",
        "total_discount_amounts",
        "total_pretax_credit_amounts",
        "total_taxes",
    )


# --- tier 2: regeneration-and-diff, only where the source spec exists ---------


@pytest.mark.skipif(
    not SPEC_PATH.is_file(),
    reason="research/stripe-openapi/spec3.json is git-ignored; fetch it per research/MANIFEST.md",
)
def test_check_mode_matches_every_committed_file() -> None:
    assert prune_main(["--check"]) == 0
