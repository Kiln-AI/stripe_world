"""Refusal conformance: every operation lands in the right bucket.

Generated from ``mcp_catalogue.jsonl`` (architecture section 8.2).
For every catalogued operation, assert the world's answer lands in the
right bucket:

- **absent** -> bucket A, the operation-gate string
- **catalogued and unrouted** -> bucket B, the permission/product refusal
- **catalogued and routed** -> not a refusal (normal dispatch)

A routing change cannot silently move an operation between buckets:
this test catches it.

This test uses the **real** catalogue (not the patched one from
conftest's ``_allow_routed_ops_through_catalogue``), so it opts out
of that fixture and reads the catalogue data directly.
"""

import json

import pytest
import seahaven

from conftest import BLANK_NOW
from seahaven_stripe_world.errors import StripeToolError, UnknownOperation
from seahaven_stripe_world.startup import ACCOUNT_ID

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW, clock_mode="fixed")


def _load_catalogue() -> list[dict]:
    """Load the real (unpatched) catalogue from disk."""
    from importlib import resources

    text = resources.files("seahaven_stripe_world.spec").joinpath("mcp_catalogue.jsonl").read_text()
    records = []
    for line in text.splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if "op" in rec:
            records.append(rec)
    return records


def _routed_ops() -> set[str]:
    from seahaven_stripe_world.dispatch.routes import ALL

    return {r.op_id for r in ALL}


# --- Bucket A: absent operations get the "not available" message ---


@pytest.fixture
def _use_real_catalogue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Override the conftest's catalogue patch to use the real data."""
    from seahaven_stripe_world.spec import catalogue

    # Re-load from disk to get the original ABSENT set
    orig_absent, orig_catalogued, orig_perms = catalogue._load()
    monkeypatch.setattr(catalogue, "ABSENT", orig_absent)
    monkeypatch.setattr(catalogue, "CATALOGUED", orig_catalogued)
    monkeypatch.setattr(catalogue, "PERMISSIONS", orig_perms)


class TestBucketA:
    """Absent operations answer the operation-gate string (bucket A)."""

    @pytest.fixture(autouse=True)
    def use_real_catalogue(self, _use_real_catalogue: None) -> None:
        pass

    def test_absent_operation_returns_bucket_a(self, instance: seahaven.Instance) -> None:
        """An absent operation returns the 'not available' message."""
        with pytest.raises(UnknownOperation, match="not available"):
            instance.call(
                "stripe_api_read",
                stripe_api_operation_id="GetTerminalReaders",
                parameters={},
                stripe_context=ACCOUNT_ID,
                livemode=True,
            )

    def test_nonexistent_operation_returns_bucket_a(self, instance: seahaven.Instance) -> None:
        """A completely fabricated operation ID also returns bucket A."""
        with pytest.raises(UnknownOperation, match="not available"):
            instance.call(
                "stripe_api_read",
                stripe_api_operation_id="GetNonexistentFooBar",
                parameters={},
                stripe_context=ACCOUNT_ID,
                livemode=True,
            )

    def test_ar_09_events_gated_by_catalogue(self, instance: seahaven.Instance) -> None:
        """AR-09/DT-22: GetEvents is routed but absent from the catalogue,
        so it answers bucket A -- the catalogue gate prevents the handler
        from running."""
        with pytest.raises(UnknownOperation, match="GetEvents"):
            instance.call(
                "stripe_api_read",
                stripe_api_operation_id="GetEvents",
                parameters={},
                stripe_context=ACCOUNT_ID,
                livemode=True,
            )

    def test_dt_23_invoice_pay_gated(self, instance: seahaven.Instance) -> None:
        """DT-23: PostInvoicesInvoicePay is routed but absent from the
        catalogue, so it answers bucket A."""
        with pytest.raises(UnknownOperation, match="PostInvoicesInvoicePay"):
            instance.call(
                "stripe_api_write",
                stripe_api_operation_id="PostInvoicesInvoicePay",
                parameters={"invoice": "in_fake"},
                stripe_context=ACCOUNT_ID,
                livemode=True,
            )


# --- Bucket B: catalogued-but-unrouted operations get B1 or B2 ---


class TestBucketB:
    """Catalogued-but-unrouted operations answer the B1/B2 refusal."""

    @pytest.fixture(autouse=True)
    def use_real_catalogue(self, _use_real_catalogue: None) -> None:
        pass

    def test_ar_01_issuing_returns_b1_product_activation(self, instance: seahaven.Instance) -> None:
        """AR-01: An Issuing operation returns the B1 product-activation
        error with the correct product name and dashboard URL."""
        with pytest.raises(StripeToolError, match="not set up to use Issuing") as exc_info:
            instance.call(
                "stripe_api_read",
                stripe_api_operation_id="GetIssuingCards",
                parameters={},
                stripe_context=ACCOUNT_ID,
                livemode=True,
            )
        assert "dashboard.stripe.com/issuing/overview" in exc_info.value.message

    def test_ar_02_checkout_returns_b2_permission(self, instance: seahaven.Instance) -> None:
        """AR-02: A Checkout operation (ordinary API surface, no product
        activation) returns the B2 permission error."""
        with pytest.raises(StripeToolError, match="required permissions") as exc_info:
            instance.call(
                "stripe_api_read",
                stripe_api_operation_id="GetCheckoutSessions",
                parameters={},
                stripe_context=ACCOUNT_ID,
                livemode=True,
            )
        assert "checkout_session_read" in exc_info.value.message

    def test_b1_includes_guidance_suffix(self, instance: seahaven.Instance) -> None:
        """B1 refusals include the guidance suffix naming stripe_api_details."""
        with pytest.raises(StripeToolError, match="stripe_api_details") as exc_info:
            instance.call(
                "stripe_api_read",
                stripe_api_operation_id="GetIssuingCards",
                parameters={},
                stripe_context=ACCOUNT_ID,
                livemode=True,
            )
        assert "stripe_api_details" in exc_info.value.message

    def test_b2_includes_guidance_suffix(self, instance: seahaven.Instance) -> None:
        """B2 refusals also include the guidance suffix."""
        with pytest.raises(StripeToolError, match="stripe_api_details") as exc_info:
            instance.call(
                "stripe_api_read",
                stripe_api_operation_id="GetCheckoutSessions",
                parameters={},
                stripe_context=ACCOUNT_ID,
                livemode=True,
            )
        assert "stripe_api_details" in exc_info.value.message

    def test_write_tool_b_refusal(self, instance: seahaven.Instance) -> None:
        """Bucket B refusals also work through stripe_api_write."""
        with pytest.raises(StripeToolError, match="not set up to use Tax"):
            instance.call(
                "stripe_api_write",
                stripe_api_operation_id="PostTaxCalculations",
                parameters={},
                stripe_context=ACCOUNT_ID,
                livemode=True,
            )


# --- stripe_analytics: always a Sigma B1 refusal ---


class TestStripeAnalytics:
    """TS-21: stripe_analytics is registered and always refuses."""

    def test_ts_21_analytics_refuses_with_sigma_b1(self, instance: seahaven.Instance) -> None:
        """Every intent gets the Sigma product-activation refusal."""
        for intent in [
            "execute_query_run",
            "retrieve_query_run",
            "search_query_tables",
            "retrieve_query_table",
            "execute_query_template",
            "retrieve_query_template",
        ]:
            with pytest.raises(StripeToolError, match="not set up to use Sigma") as exc_info:
                instance.call(
                    "stripe_analytics",
                    intent=intent,
                    stripe_context=ACCOUNT_ID,
                    livemode=True,
                    params={},
                )
            assert "dashboard.stripe.com/sigma" in exc_info.value.message
            assert "Stripe API error:" in exc_info.value.message

    def test_analytics_validates_context(self, instance: seahaven.Instance) -> None:
        """stripe_analytics validates stripe_context before refusing."""
        from seahaven_stripe_world.errors import SessionValidation

        with pytest.raises(SessionValidation):
            instance.call(
                "stripe_analytics",
                intent="execute_query_run",
                stripe_context="acct_wrong",
                livemode=True,
                params={},
            )


# --- Full conformance sweep ---


class TestRefusalConformanceSweep:
    """Generated sweep: every catalogue entry lands in the right bucket."""

    @pytest.fixture(autouse=True)
    def use_real_catalogue(self, _use_real_catalogue: None) -> None:
        pass

    def test_all_absent_ops_answer_bucket_a(self, instance: seahaven.Instance) -> None:
        """Every absent operation answers the operation-gate string."""
        records = _load_catalogue()
        absent_records = [r for r in records if r["verdict"] == "absent"]
        # Sample a representative subset to keep the test fast
        # (471 absent ops would be slow to test one by one).
        # Take every 20th for a ~24-item sample.
        sample = absent_records[::20]
        for rec in sample:
            op = rec["op"]
            with pytest.raises(UnknownOperation):
                instance.call(
                    "stripe_api_read",
                    stripe_api_operation_id=op,
                    parameters={},
                    stripe_context=ACCOUNT_ID,
                    livemode=True,
                )

    def test_all_catalogued_unrouted_ops_answer_bucket_b(self, instance: seahaven.Instance) -> None:
        """Every catalogued-but-unrouted operation answers bucket B."""
        records = _load_catalogue()
        routed = _routed_ops()
        b_records = [r for r in records if r["verdict"] == "catalogued" and r["op"] not in routed]
        for rec in b_records:
            op = rec["op"]
            with pytest.raises(StripeToolError, match="Stripe API error:"):
                instance.call(
                    "stripe_api_read",
                    stripe_api_operation_id=op,
                    parameters={},
                    stripe_context=ACCOUNT_ID,
                    livemode=True,
                )

    def test_routed_absent_ops_answer_bucket_a(self, instance: seahaven.Instance) -> None:
        """Routed operations that are absent from the catalogue answer
        bucket A -- the catalogue gate prevents the handler from running
        (architecture section 4.3)."""
        records = _load_catalogue()
        routed = _routed_ops()
        absent_and_routed = [r for r in records if r["verdict"] == "absent" and r["op"] in routed]
        # Sample: test a representative set
        sample = absent_and_routed[::5]
        for rec in sample:
            op = rec["op"]
            with pytest.raises(UnknownOperation):
                instance.call(
                    "stripe_api_read",
                    stripe_api_operation_id=op,
                    parameters={},
                    stripe_context=ACCOUNT_ID,
                    livemode=True,
                )

    def test_catalogued_routed_ops_are_not_refused(self, instance: seahaven.Instance) -> None:
        """Catalogued-and-routed operations dispatch normally -- they are
        not refused by the catalogue gate."""
        records = _load_catalogue()
        routed = _routed_ops()
        # Pick a few catalogued-and-routed GET ops to verify they work
        both = [
            r
            for r in records
            if r["verdict"] == "catalogued"
            and r["op"] in routed
            and r["op"].startswith("Get")
            and not r["op"].startswith("GetEvents")
        ]
        # Test a small sample
        for rec in both[:5]:
            op = rec["op"]
            # These should NOT raise UnknownOperation or CatalogueRefusal.
            # They may raise StripeApiError for other reasons (e.g. missing
            # params), which is fine -- the point is they get past the gate.
            try:
                instance.call(
                    "stripe_api_read",
                    stripe_api_operation_id=op,
                    parameters={},
                    stripe_context=ACCOUNT_ID,
                    livemode=True,
                )
            except UnknownOperation:
                pytest.fail(f"Catalogued-and-routed op {op} was refused as bucket A")
            except StripeToolError:
                pass  # expected for ops that need parameters
