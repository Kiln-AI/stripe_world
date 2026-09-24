"""The seven ``/v1/*/search`` endpoints (Phase 22).

Each test creates objects via the write tool, then searches via the read tool
at ``GET /v1/<resource>/search?query=...``. The search-result envelope has its
own shape (``object``, ``data``, ``has_more``, ``next_page``, ``total_count``,
``url``) and its own pagination model (``page`` / ``next_page``).
"""

import pytest
import seahaven

from conftest import BLANK_NOW, api_read, api_write
from seahaven_stripe_world.errors import StripeToolError
from seahaven_stripe_world.search.parser import Combinator, Operator, ParseError, parse

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(instance: seahaven.Instance, path: str, **params: object) -> dict:
    return api_write(instance, "POST", path, dict(params))


def _search(instance: seahaven.Instance, path: str, query: str, **extra: object) -> dict:
    params: dict = {"query": query, **extra}
    return api_read(instance, path, params)


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------


class TestParser:
    def test_single_clause(self) -> None:
        clauses, comb = parse('status:"active"')
        assert len(clauses) == 1
        assert clauses[0].field == "status"
        assert clauses[0].operator == Operator.EXACT
        assert clauses[0].value == "active"
        assert comb == Combinator.AND

    def test_negated_clause(self) -> None:
        clauses, _ = parse('-status:"active"')
        assert clauses[0].negated is True

    def test_and_combinator(self) -> None:
        clauses, comb = parse('status:"active" AND currency:"usd"')
        assert len(clauses) == 2
        assert comb == Combinator.AND

    def test_or_combinator(self) -> None:
        clauses, comb = parse('status:"active" OR status:"trialing"')
        assert len(clauses) == 2
        assert comb == Combinator.OR

    def test_implicit_and(self) -> None:
        clauses, comb = parse('status:"active" currency:"usd"')
        assert len(clauses) == 2
        assert comb == Combinator.AND

    def test_mixed_and_or_raises(self) -> None:
        with pytest.raises(ParseError, match="combine AND and OR"):
            parse('a:"1" AND b:"2" OR c:"3"')

    def test_metadata_field(self) -> None:
        clauses, _ = parse('metadata["order_id"]:"12345"')
        assert clauses[0].field == "metadata"
        assert clauses[0].metadata_key == "order_id"
        assert clauses[0].value == "12345"

    def test_null_value(self) -> None:
        clauses, _ = parse("email:null")
        assert clauses[0].value is None

    def test_numeric_operators(self) -> None:
        for op_str, op_enum in (
            (">", Operator.GT),
            (">=", Operator.GTE),
            ("<", Operator.LT),
            ("<=", Operator.LTE),
        ):
            clauses, _ = parse(f"amount{op_str}1000")
            assert clauses[0].operator == op_enum
            assert clauses[0].value == "1000"

    def test_substring_operator(self) -> None:
        clauses, _ = parse('name~"acme"')
        assert clauses[0].operator == Operator.SUBSTR
        assert clauses[0].value == "acme"

    def test_empty_query_raises(self) -> None:
        with pytest.raises(ParseError, match="at least one clause"):
            parse("")

    def test_max_clauses(self) -> None:
        # 10 is fine.
        q = " ".join(f'f{i}:"v"' for i in range(10))
        clauses, _ = parse(q)
        assert len(clauses) == 10
        # 11 raises.
        q = " ".join(f'f{i}:"v"' for i in range(11))
        with pytest.raises(ParseError, match="up to 10"):
            parse(q)

    def test_bare_number_value(self) -> None:
        clauses, _ = parse("amount:5000")
        assert clauses[0].value == "5000"

    def test_escaped_quote_in_value(self) -> None:
        clauses, _ = parse('name:"O\\"Brien"')
        assert clauses[0].value == 'O"Brien'


# ---------------------------------------------------------------------------
# Search envelope shape
# ---------------------------------------------------------------------------


class TestSearchEnvelope:
    def test_empty_search_returns_correct_shape(self, instance: seahaven.Instance) -> None:
        body = _search(instance, "/v1/products/search", 'name:"nonexistent-xyz"')
        assert body["object"] == "search_result"
        assert body["data"] == []
        assert body["has_more"] is False
        assert body["next_page"] is None
        # total_count is opt-in via expand[]=total_count (functional spec §6.2).
        assert "total_count" not in body
        assert body["url"] == "/v1/products/search"

    def test_search_missing_query_is_400(self, instance: seahaven.Instance) -> None:
        with pytest.raises(StripeToolError) as exc_info:
            api_read(instance, "/v1/products/search", {})
        assert exc_info.value.status == 400

    def test_search_invalid_query_is_400(self, instance: seahaven.Instance) -> None:
        with pytest.raises(StripeToolError) as exc_info:
            api_read(instance, "/v1/products/search", {"query": "status>"})
        assert exc_info.value.status == 400


# ---------------------------------------------------------------------------
# Products search
# ---------------------------------------------------------------------------


class TestProductsSearch:
    def test_search_by_name(self, instance: seahaven.Instance) -> None:
        _write(instance, "/v1/products", name="Alpha Widget")
        _write(instance, "/v1/products", name="Beta Gadget")
        body = _search(instance, "/v1/products/search", 'name:"Alpha"')
        assert len(body["data"]) == 1
        assert body["data"][0]["name"] == "Alpha Widget"

    def test_search_by_active(self, instance: seahaven.Instance) -> None:
        p = _write(instance, "/v1/products", name="Deactivated")
        _write(instance, f"/v1/products/{p['id']}", active=False)
        body = _search(instance, "/v1/products/search", 'active:"false"')
        assert len(body["data"]) >= 1
        assert any(d["id"] == p["id"] for d in body["data"])

    def test_search_by_metadata(self, instance: seahaven.Instance) -> None:
        p = _write(instance, "/v1/products", name="Meta Product", metadata={"sku": "XYZ-123"})
        body = _search(instance, "/v1/products/search", 'metadata["sku"]:"XYZ-123"')
        assert any(d["id"] == p["id"] for d in body["data"])

    def test_substring_search(self, instance: seahaven.Instance) -> None:
        _write(instance, "/v1/products", name="Enterprise License")
        body = _search(instance, "/v1/products/search", 'name~"erpr"')
        assert len(body["data"]) >= 1
        assert any("Enterprise" in d["name"] for d in body["data"])

    def test_substring_too_short(self, instance: seahaven.Instance) -> None:
        with pytest.raises(StripeToolError) as exc_info:
            api_read(instance, "/v1/products/search", {"query": 'name~"ab"'})
        assert exc_info.value.status == 400

    def test_unknown_field_is_400(self, instance: seahaven.Instance) -> None:
        with pytest.raises(StripeToolError) as exc_info:
            api_read(instance, "/v1/products/search", {"query": 'nonexistent:"val"'})
        assert exc_info.value.status == 400


# ---------------------------------------------------------------------------
# Customers search
# ---------------------------------------------------------------------------


class TestCustomersSearch:
    def test_search_by_email(self, instance: seahaven.Instance) -> None:
        c = _write(instance, "/v1/customers", email="alice@example.com", name="Alice")
        _write(instance, "/v1/customers", email="bob@example.com", name="Bob")
        body = _search(instance, "/v1/customers/search", 'email:"alice@example.com"')
        assert len(body["data"]) >= 1
        assert any(d["id"] == c["id"] for d in body["data"])

    def test_search_by_name_substring(self, instance: seahaven.Instance) -> None:
        _write(instance, "/v1/customers", name="Charlie Brown")
        body = _search(instance, "/v1/customers/search", 'name~"Char"')
        assert len(body["data"]) >= 1

    def test_search_by_created(self, instance: seahaven.Instance) -> None:
        _write(instance, "/v1/customers", email="newer@example.com")
        body = _search(
            instance,
            "/v1/customers/search",
            "created>0",
            expand=["total_count"],
        )
        assert body["total_count"] >= 1

    def test_negated_search(self, instance: seahaven.Instance) -> None:
        _write(instance, "/v1/customers", email="keep@example.com", name="Keeper")
        _write(instance, "/v1/customers", email="drop@example.com", name="Dropper")
        body = _search(instance, "/v1/customers/search", '-name:"Dropper"')
        names = [d["name"] for d in body["data"]]
        assert "Dropper" not in names

    def test_or_search(self, instance: seahaven.Instance) -> None:
        a = _write(instance, "/v1/customers", email="or1@example.com", name="OrA")
        b = _write(instance, "/v1/customers", email="or2@example.com", name="OrB")
        body = _search(instance, "/v1/customers/search", 'name:"OrA" OR name:"OrB"')
        ids = {d["id"] for d in body["data"]}
        assert a["id"] in ids
        assert b["id"] in ids

    def test_null_email(self, instance: seahaven.Instance) -> None:
        c = _write(instance, "/v1/customers", name="No Email")
        body = _search(instance, "/v1/customers/search", "email:null")
        assert any(d["id"] == c["id"] for d in body["data"])


# ---------------------------------------------------------------------------
# Prices search
# ---------------------------------------------------------------------------


class TestPricesSearch:
    def test_search_by_currency(self, instance: seahaven.Instance) -> None:
        prod = _write(instance, "/v1/products", name="Price Test")
        _write(
            instance,
            "/v1/prices",
            currency="eur",
            unit_amount=2000,
            product=prod["id"],
        )
        body = _search(instance, "/v1/prices/search", 'currency:"eur"')
        assert len(body["data"]) >= 1
        assert all(d["currency"] == "eur" for d in body["data"])

    def test_search_by_type(self, instance: seahaven.Instance) -> None:
        prod = _write(instance, "/v1/products", name="Recurring Price Test")
        _write(
            instance,
            "/v1/prices",
            currency="usd",
            unit_amount=1000,
            product=prod["id"],
            recurring={"interval": "month"},
        )
        body = _search(instance, "/v1/prices/search", 'type:"recurring"')
        assert all(d["type"] == "recurring" for d in body["data"])


# ---------------------------------------------------------------------------
# Charges / Payment Intents search
# ---------------------------------------------------------------------------


class TestChargesSearch:
    def test_search_by_status(self, instance: seahaven.Instance) -> None:
        prod = _write(instance, "/v1/products", name="Charge Test Prod")
        _write(
            instance,
            "/v1/prices",
            currency="usd",
            unit_amount=5000,
            product=prod["id"],
        )
        cust = _write(instance, "/v1/customers")
        pm = _write(
            instance,
            "/v1/payment_methods",
            type="card",
            card={"number": "4242424242424242", "exp_month": 12, "exp_year": 2030, "cvc": "123"},
        )
        _write(instance, f"/v1/payment_methods/{pm['id']}/attach", customer=cust["id"])
        pi = _write(
            instance,
            "/v1/payment_intents",
            amount=5000,
            currency="usd",
            customer=cust["id"],
            payment_method=pm["id"],
        )
        _write(instance, f"/v1/payment_intents/{pi['id']}/confirm")
        body = _search(instance, "/v1/charges/search", 'status:"succeeded"')
        assert len(body["data"]) >= 1
        assert body["data"][0]["status"] == "succeeded"

    def test_search_by_amount(self, instance: seahaven.Instance) -> None:
        body = _search(instance, "/v1/charges/search", "amount>=1000")
        # Just testing the query parses and returns valid shape.
        assert body["object"] == "search_result"


class TestPaymentIntentsSearch:
    def test_search_by_currency(self, instance: seahaven.Instance) -> None:
        cust = _write(instance, "/v1/customers")
        _write(instance, "/v1/payment_intents", amount=1000, currency="gbp", customer=cust["id"])
        body = _search(instance, "/v1/payment_intents/search", 'currency:"gbp"')
        assert len(body["data"]) >= 1
        assert all(d["currency"] == "gbp" for d in body["data"])


# ---------------------------------------------------------------------------
# Subscriptions search
# ---------------------------------------------------------------------------


class TestSubscriptionsSearch:
    def test_search_by_status(self, instance: seahaven.Instance) -> None:
        prod = _write(instance, "/v1/products", name="Sub Search Prod")
        price = _write(
            instance,
            "/v1/prices",
            currency="usd",
            unit_amount=1000,
            product=prod["id"],
            recurring={"interval": "month"},
        )
        cust = _write(instance, "/v1/customers")
        pm = _write(
            instance,
            "/v1/payment_methods",
            type="card",
            card={"number": "4242424242424242", "exp_month": 12, "exp_year": 2030, "cvc": "123"},
        )
        _write(instance, f"/v1/payment_methods/{pm['id']}/attach", customer=cust["id"])
        _write(
            instance,
            "/v1/subscriptions",
            customer=cust["id"],
            items=[{"price": price["id"]}],
            default_payment_method=pm["id"],
        )
        body = _search(instance, "/v1/subscriptions/search", 'status:"active"')
        assert body["object"] == "search_result"
        assert len(body["data"]) >= 1
        assert body["data"][0]["status"] == "active"


# ---------------------------------------------------------------------------
# Invoices search
# ---------------------------------------------------------------------------


class TestInvoicesSearch:
    def test_search_by_customer(self, instance: seahaven.Instance) -> None:
        cust = _write(instance, "/v1/customers")
        inv = _write(instance, "/v1/invoices", customer=cust["id"])
        body = _search(instance, "/v1/invoices/search", f'customer:"{cust["id"]}"')
        assert any(d["id"] == inv["id"] for d in body["data"])

    def test_search_by_status(self, instance: seahaven.Instance) -> None:
        cust = _write(instance, "/v1/customers")
        _write(instance, "/v1/invoices", customer=cust["id"])
        body = _search(instance, "/v1/invoices/search", 'status:"draft"')
        assert all(d["status"] == "draft" for d in body["data"])


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class TestSearchPagination:
    def test_limit_and_next_page(self, instance: seahaven.Instance) -> None:
        """Create 3 products, search with limit=2, paginate to get the third."""
        for i in range(3):
            _write(instance, "/v1/products", name=f"PagTest {i}")
        body = _search(instance, "/v1/products/search", 'name~"PagTest"', limit=2)
        assert len(body["data"]) == 2
        assert body["has_more"] is True
        assert body["next_page"] is not None
        # Follow the cursor.
        body2 = _search(
            instance, "/v1/products/search", 'name~"PagTest"', limit=2, page=body["next_page"]
        )
        assert len(body2["data"]) >= 1
        # Combined data should have all 3 items.
        all_ids = {d["id"] for d in body["data"]} | {d["id"] for d in body2["data"]}
        assert len(all_ids) == 3

    def test_invalid_page_cursor_is_400(self, instance: seahaven.Instance) -> None:
        with pytest.raises(StripeToolError) as exc_info:
            api_read(
                instance, "/v1/products/search", {"query": 'name:"x"', "page": "bogus-not-base64"}
            )
        assert exc_info.value.status == 400

    def test_total_count_absent_by_default(self, instance: seahaven.Instance) -> None:
        """total_count is not returned unless expand[]=total_count is set."""
        _write(instance, "/v1/products", name="AbsentTest")
        body = _search(instance, "/v1/products/search", 'name~"AbsentTest"')
        assert "total_count" not in body

    def test_total_count_present_when_expanded(self, instance: seahaven.Instance) -> None:
        """total_count appears and is accurate when opted-in via expand."""
        for i in range(5):
            _write(instance, "/v1/products", name=f"CountTest {i}")
        body = _search(
            instance,
            "/v1/products/search",
            'name~"CountTest"',
            limit=2,
            expand=["total_count"],
        )
        assert body["total_count"] == 5


# ---------------------------------------------------------------------------
# CR round 1 regression tests: post-filter correctness
# ---------------------------------------------------------------------------


class TestPostFilterCorrectness:
    """Tests for the bugs found in CR round 1: total_count with post-filters,
    OR with mixed SQL/post-filter clauses, pagination with post-filters, and
    punctuation in phrase matching."""

    def test_total_count_accurate_with_phrase_filter(self, instance: seahaven.Instance) -> None:
        """CR finding 1: total_count must reflect true matches, not a
        pre-filter count.  With FTS5, everything resolves in one SQL layer."""
        # Create products: 3 have "John" but only 1 has the phrase "John Smith".
        _write(instance, "/v1/products", name="John Smith Widget")
        _write(instance, "/v1/products", name="John Doe Gadget")
        _write(instance, "/v1/products", name="John Adams Tool")
        body = _search(
            instance,
            "/v1/products/search",
            'name:"John Smith"',
            expand=["total_count"],
        )
        assert body["total_count"] == 1
        assert len(body["data"]) == 1
        assert "John Smith" in body["data"][0]["name"]

    def test_or_mixed_clauses_keeps_sql_only_matches(self, instance: seahaven.Instance) -> None:
        """CR finding 2: OR with a phrase clause and a token clause must not
        drop rows that matched only the token clause."""
        _write(instance, "/v1/customers", name="John Smith", email="john@example.com")
        bob = _write(instance, "/v1/customers", name="Bob Jones", email="bob@example.com")
        # name:"John Smith" has a post-filter; email:"bob@example.com" is
        # string-type exact match (single word, so SQL-only).  Bob must appear.
        body = _search(
            instance,
            "/v1/customers/search",
            'name:"John Smith" OR email:"bob@example.com"',
        )
        ids = {d["id"] for d in body["data"]}
        assert bob["id"] in ids

    def test_pagination_with_fts5_is_complete(self, instance: seahaven.Instance) -> None:
        """CR finding 3: has_more and pagination must not silently stop early.
        With FTS5, everything resolves in SQL — no post-filter to drop rows."""
        # Create 5 products with the two-word phrase; search with limit=2.
        for i in range(5):
            _write(instance, "/v1/products", name=f"Great Product {i}")
        body = _search(
            instance,
            "/v1/products/search",
            'name:"Great Product"',
            limit=2,
            expand=["total_count"],
        )
        assert body["total_count"] == 5
        assert body["has_more"] is True
        # Walk all pages.
        all_ids: set[str] = {d["id"] for d in body["data"]}
        cursor = body["next_page"]
        while cursor:
            page = _search(
                instance,
                "/v1/products/search",
                'name:"Great Product"',
                limit=2,
                page=cursor,
            )
            all_ids |= {d["id"] for d in page["data"]}
            cursor = page["next_page"]
        assert len(all_ids) == 5

    def test_phrase_match_ignores_trailing_punctuation(self, instance: seahaven.Instance) -> None:
        """CR finding 4: punctuation at word boundaries must not prevent a
        phrase match."""
        _write(instance, "/v1/products", name="A great product.")
        body = _search(instance, "/v1/products/search", 'name:"great product"')
        assert len(body["data"]) >= 1
        assert any("great product" in d["name"].lower() for d in body["data"])


# ---------------------------------------------------------------------------
# Cross-resource: all 7 endpoints respond
# ---------------------------------------------------------------------------


class TestAllEndpointsRespond:
    """Smoke test: every search endpoint parses a trivial query without error."""

    @pytest.mark.parametrize(
        "path,query",
        [
            ("/v1/charges/search", "created>0"),
            ("/v1/customers/search", "created>0"),
            ("/v1/invoices/search", "created>0"),
            ("/v1/payment_intents/search", "created>0"),
            ("/v1/prices/search", 'currency:"usd"'),
            ("/v1/products/search", 'active:"true"'),
            ("/v1/subscriptions/search", "created>0"),
        ],
    )
    def test_endpoint_responds(self, instance: seahaven.Instance, path: str, query: str) -> None:
        body = _search(instance, path, query)
        assert body["object"] == "search_result"
        assert isinstance(body["data"], list)


# ---------------------------------------------------------------------------
# CR round 2 regression tests
# ---------------------------------------------------------------------------


class TestExpandTotalCountGating:
    """Finding 1: expand[]=total_count must be rejected on non-search endpoints."""

    def test_total_count_rejected_on_list_endpoint(self, instance: seahaven.Instance) -> None:
        """Non-search list endpoints must return 400 for expand[]=total_count."""
        with pytest.raises(StripeToolError) as exc_info:
            api_read(instance, "/v1/products", {"expand": ["total_count"]})
        assert exc_info.value.status == 400

    def test_total_count_accepted_on_search_endpoint(self, instance: seahaven.Instance) -> None:
        """Search endpoints accept expand[]=total_count and return the count."""
        _write(instance, "/v1/products", name="GateTestProd")
        body = _search(
            instance,
            "/v1/products/search",
            'name:"GateTestProd"',
            expand=["total_count"],
        )
        assert "total_count" in body
        assert body["total_count"] >= 1


class TestCursorFingerprint:
    """Finding 2: offset cursor must contain a query fingerprint."""

    def test_cursor_from_different_query_rejected(self, instance: seahaven.Instance) -> None:
        """A page cursor obtained from one FTS query must be rejected when
        replayed against a different FTS query."""
        for i in range(3):
            _write(instance, "/v1/products", name=f"FpTest Alpha {i}")
        _write(instance, "/v1/products", name="FpTest Beta unique")

        # Get a cursor from a search for "Alpha".
        body = _search(
            instance,
            "/v1/products/search",
            'name:"FpTest Alpha"',
            limit=1,
        )
        assert body["has_more"] is True
        cursor = body["next_page"]

        # Replay that cursor against a different query — must fail.
        with pytest.raises(StripeToolError) as exc_info:
            api_read(
                instance, "/v1/products/search", {"query": 'name:"FpTest Beta"', "page": cursor}
            )
        assert exc_info.value.status == 400

    def test_same_fts_different_sql_filter_rejected(self, instance: seahaven.Instance) -> None:
        """Same FTS term but different SQL-side filter must invalidate the
        cursor.  This covers the case where the fingerprint only hashed FTS
        terms and missed token/numeric clauses."""
        # Customers have both FTS string fields (name) and numeric fields
        # (created), so the query hits the bm25 offset path.
        for i in range(3):
            _write(
                instance,
                "/v1/customers",
                name=f"FpSql Widget {i}",
                email=f"fpsql{i}@example.com",
            )

        # Page 1 with created>0 — all 3 match.
        body = _search(
            instance,
            "/v1/customers/search",
            'name:"FpSql Widget" AND created>0',
            limit=1,
        )
        assert body["has_more"] is True
        cursor = body["next_page"]

        # Replay with same FTS term but different numeric filter — must fail.
        with pytest.raises(StripeToolError) as exc_info:
            api_read(
                instance,
                "/v1/customers/search",
                {"query": 'name:"FpSql Widget" AND created>9999999999', "page": cursor},
            )
        assert exc_info.value.status == 400


class TestSearchRejectsListCursors:
    """Finding: starting_after and ending_before must be rejected on search."""

    def test_starting_after_rejected(self, instance: seahaven.Instance) -> None:
        with pytest.raises(StripeToolError) as exc_info:
            api_read(
                instance,
                "/v1/products/search",
                {"query": 'name:"anything"', "starting_after": "prod_fake"},
            )
        assert exc_info.value.status == 400
        assert "starting_after" in exc_info.value.stripe_body["error"].get("message", "")

    def test_ending_before_rejected(self, instance: seahaven.Instance) -> None:
        with pytest.raises(StripeToolError) as exc_info:
            api_read(
                instance,
                "/v1/products/search",
                {"query": 'name:"anything"', "ending_before": "prod_fake"},
            )
        assert exc_info.value.status == 400
        assert "ending_before" in exc_info.value.stripe_body["error"].get("message", "")


class TestNegatedFtsBindParam:
    """Finding 3: negated FTS MATCH must use bind parameters."""

    def test_negated_fts_with_special_chars(self, instance: seahaven.Instance) -> None:
        """Negated FTS with a value that contains a single quote must work
        correctly (bind params prevent SQL injection)."""
        _write(instance, "/v1/products", name="Safe Widget")
        _write(instance, "/v1/products", name="O'Malley Gadget")
        body = _search(
            instance,
            "/v1/products/search",
            '-name:"O\'Malley"',
        )
        names = [d["name"] for d in body["data"]]
        assert "Safe Widget" in names
