"""Phase 9: serialization completeness — the 20 fields, the pruner fix,
and the exclusion test (functional spec §11, architecture §7).

Two guards here, both generated from the spec rather than hand-written:

1. **Field coverage** (``test_field_coverage_by_fieldmap``): for every object
   type this world returns through a FieldMap, every property the pruned spec
   declares must be accounted for.  A gap is a tell waiting to happen; a new
   field the spec adds after a re-pin fails here rather than going unnoticed.

2. **Product exclusion** (``test_as_14_product_excluded_fields``): the three
   product fields the live API emits but the spec does not declare
   (functional spec §11.2) are asserted absent from our output.

3. **Pruner coverage** (``test_pruner_includes_account_schema``): the pruned
   spec includes the ``account`` schema (architecture §7's pruner fix).
"""

import importlib

import pytest
import seahaven

from conftest import BLANK_NOW, api_read, api_write
from schema_conformance.validate import BY_OBJECT, RULES
from seahaven_stripe_world.serialize.fields import FieldMap

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


# The fields injected centrally by to_api / the framework, not by any FieldMap.
_AUTO_FIELDS = frozenset({"object", "livemode"})

# Resource modules that export a FIELDS constant.
_FIELDMAP_MODULES: tuple[str, ...] = (
    "seahaven_stripe_world.resources.balance_transactions",
    "seahaven_stripe_world.resources.charges",
    "seahaven_stripe_world.resources.coupons",
    "seahaven_stripe_world.resources.credit_notes",
    "seahaven_stripe_world.resources.customer_balance_transactions",
    "seahaven_stripe_world.resources.customers",
    "seahaven_stripe_world.resources.disputes",
    "seahaven_stripe_world.resources.invoiceitems",
    "seahaven_stripe_world.resources.invoices",
    "seahaven_stripe_world.resources.payment_intents",
    "seahaven_stripe_world.resources.payouts",
    "seahaven_stripe_world.resources.prices",
    "seahaven_stripe_world.resources.products",
    "seahaven_stripe_world.resources.promotion_codes",
    "seahaven_stripe_world.resources.refunds",
    "seahaven_stripe_world.resources.setup_intents",
    "seahaven_stripe_world.resources.subscription_items",
    "seahaven_stripe_world.resources.subscription_schedules",
    "seahaven_stripe_world.resources.subscriptions",
    "seahaven_stripe_world.resources.tax_rates",
)


def _load_fieldmaps() -> dict[str, FieldMap]:
    """Load all FieldMaps from resource modules, keyed by object name."""
    result: dict[str, FieldMap] = {}
    for mod_path in _FIELDMAP_MODULES:
        mod = importlib.import_module(mod_path)
        fmap = getattr(mod, "FIELDS", None)
        if isinstance(fmap, FieldMap):
            result[fmap.object] = fmap
    return result


def _covered_fields(fmap: FieldMap) -> frozenset[str]:
    """The set of API field names a FieldMap covers, including OMIT constants."""
    covered = set(fmap.columns.values())
    covered |= set(fmap.constants.keys())
    covered |= set(fmap.derived.keys())
    covered |= _AUTO_FIELDS
    return frozenset(covered)


def test_field_coverage_by_fieldmap() -> None:
    """Every schema-declared property of every FieldMap-registered object is
    accounted for.  A gap here is a field the live API returns and the spec
    declares, but our serialiser silently omits — a tell (functional spec
    §11).
    """
    fieldmaps = _load_fieldmaps()
    gaps: list[str] = []
    for obj_name, fmap in sorted(fieldmaps.items()):
        schema_name = BY_OBJECT.get(obj_name)
        if schema_name is None:
            continue
        rule = RULES.get(schema_name)
        if not isinstance(rule, dict) or "props" not in rule:
            continue
        schema_props = frozenset(rule["props"].keys())
        covered = _covered_fields(fmap)
        missing = sorted(schema_props - covered)
        if missing:
            gaps.append(f"  {obj_name}: {missing}")
    assert not gaps, "Fields in the schema but not in the FieldMap:\n" + "\n".join(gaps)


def test_as_14_product_excluded_fields(instance: seahaven.Instance) -> None:
    """AS-14/AS-15: product.attributes, product.type, product.tax_details are
    absent from our output — the spec does not declare them, so emitting them
    would break schema conformance (functional spec §11.2)."""
    product = api_write(instance, "POST", "/v1/products", {"name": "Exclusion test"})
    for field in ("attributes", "type", "tax_details"):
        assert field not in product, f"product.{field} must not be emitted"
    read = api_read(instance, f"/v1/products/{product['id']}")
    for field in ("attributes", "type", "tax_details"):
        assert field not in read, f"product.{field} must not be emitted on read"


def test_pruner_includes_account_schema() -> None:
    """The pruner's tool-output seed brings the account schema into the
    pruned spec (architecture §7, functional spec §11.1's pruner gap)."""
    assert "account" in BY_OBJECT, "account missing from the schema rules' by_object table"
    rule = RULES[BY_OBJECT["account"]]
    assert isinstance(rule, dict) and "props" in rule
    # The six fields that were the pruner gap (functional spec §11.1):
    for field in (
        "controller",
        "external_accounts",
        "requirements",
        "future_requirements",
        "tos_acceptance",
    ):
        assert field in rule["props"], f"account.{field} missing from pruned schema"


def test_as_26_payment_details_in_fieldmap() -> None:
    """AS-26: payment_intent.payment_details is in the FieldMap constants.
    The spec marks it nullable + not required, so ``to_api`` omits it when
    null (the ``omit_when_none`` set).  On a subscription-created PI the
    invoice engine would populate it; the constant covers the bare-PI case
    where the field is legitimately absent."""
    fieldmaps = _load_fieldmaps()
    fmap = fieldmaps["payment_intent"]
    assert "payment_details" in fmap.constants


def test_as_28_radar_options_present(instance: seahaven.Instance) -> None:
    """AS-28: charge.radar_options is present (as null, per the spec's type)."""
    cus = api_write(instance, "POST", "/v1/customers", {"email": "r@example.test"})["id"]
    pm = api_write(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_visa"}},
    )["id"]
    api_write(instance, "POST", f"/v1/payment_methods/{pm}/attach", {"customer": cus})
    pi = api_write(
        instance,
        "POST",
        "/v1/payment_intents",
        {"amount": 1000, "currency": "usd", "customer": cus, "payment_method": pm, "confirm": True},
    )
    charge = api_read(instance, f"/v1/charges/{pi['latest_charge']}")
    assert "radar_options" in charge
    # The spec types radar_options as {"type": "null"}, so the spec-legal
    # value is null.  The live API emits {} but the spec is the authority.
    assert charge["radar_options"] is None
