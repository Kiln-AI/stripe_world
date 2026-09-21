"""Cursor pagination: the envelope, the cursors, and the walk."""

import pytest
import seahaven

from conftest import BLANK_NOW

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def create(instance: seahaven.Instance, name: str) -> str:
    result = instance.call(
        "stripe_api_write", method="POST", path="/v1/customers", params={"name": name}
    )
    assert result["status"] == 200
    return result["body"]["id"]


def list_customers(instance: seahaven.Instance, **params: object) -> dict:
    return instance.call("stripe_api_read", path="/v1/customers", params=params)


def test_envelope_has_exactly_four_keys(instance: seahaven.Instance) -> None:
    create(instance, "one")
    body = list_customers(instance)["body"]
    assert set(body) == {"object", "data", "has_more", "url"}
    assert body["object"] == "list"
    assert body["url"] == "/v1/customers"


def test_walk_forward_covers_every_row_exactly_once(instance: seahaven.Instance) -> None:
    ids = [create(instance, f"c{i:02}") for i in range(7)]
    newest_first = list(reversed(ids))

    seen: list[str] = []
    cursor = None
    for _ in range(10):
        params: dict[str, object] = {"limit": 3}
        if cursor is not None:
            params["starting_after"] = cursor
        page = list_customers(instance, **params)["body"]
        seen.extend(item["id"] for item in page["data"])
        if not page["has_more"]:
            break
        cursor = page["data"][-1]["id"]
    assert seen == newest_first


def test_ending_before_returns_the_previous_page_newest_first(instance: seahaven.Instance) -> None:
    ids = [create(instance, f"c{i:02}") for i in range(5)]
    newest_first = list(reversed(ids))

    page = list_customers(instance, ending_before=newest_first[2], limit=2)["body"]
    # The two rows *newer* than the cursor — the page above it — newest first.
    assert [item["id"] for item in page["data"]] == newest_first[0:2]
    # Nothing newer than the newest: the direction of travel is exhausted.
    assert page["has_more"] is False


def test_page_embedded_backward_page_is_adjacent_to_the_cursor() -> None:
    """The embedded-list twin of the scan-and-reverse rule: 9 items newest
    first, `ending_before=il_3, limit=2` returns the two items immediately
    above the cursor — not the newest two of the newer-than-cursor set, which
    would strand `il_5`/`il_4` behind an advancing walk.

    Probed live this round for the exhausted end: the cursor row never
    appears, however close to the top the walk is.
    """
    from seahaven_stripe_world.dispatch.resource import page_embedded
    from seahaven_stripe_world.dispatch.response import Page

    items = [{"id": f"il_{i}", "object": "line_item"} for i in range(9, 0, -1)]
    page = page_embedded(
        items,
        Page(limit=2, starting_after=None, ending_before="il_3"),
        url="/v1/invoices/in_1/lines",
        object_name="line_item",
    )
    assert page["data"] == [
        {"id": "il_5", "object": "line_item"},
        {"id": "il_4", "object": "line_item"},
    ]
    assert page["has_more"] is True

    exhausted = page_embedded(
        items,
        Page(limit=2, starting_after=None, ending_before="il_8"),
        url="/v1/invoices/in_1/lines",
        object_name="line_item",
    )
    # Only `il_9` is newer than the cursor, and the cursor itself never
    # appears — the exclusivity the live API showed for the same shape.
    assert exhausted["data"] == [{"id": "il_9", "object": "line_item"}]
    assert exhausted["has_more"] is False


def test_page_embedded_forward_page_and_envelope() -> None:
    from seahaven_stripe_world.dispatch.resource import page_embedded
    from seahaven_stripe_world.dispatch.response import Page

    items = [{"id": f"il_{i}"} for i in range(9, 0, -1)]
    page = page_embedded(
        items,
        Page(limit=2, starting_after="il_7", ending_before=None),
        url="/v1/invoices/in_1/lines",
        object_name="line_item",
    )
    assert page == {
        "object": "list",
        "data": [{"id": "il_6"}, {"id": "il_5"}],
        "has_more": True,
        "url": "/v1/invoices/in_1/lines",
    }


def test_page_embedded_bogus_cursor_is_400_resource_missing() -> None:
    """The embedded twin of the table-backed rule: a query-side
    `resource_missing` is a 400, not the 404 a path id earns (recorded
    Phase 5, scenario 09)."""
    from seahaven_stripe_world.dispatch.resource import page_embedded
    from seahaven_stripe_world.dispatch.response import Page
    from seahaven_stripe_world.stripe_errors import StripeApiError

    items = [{"id": f"il_{i}"} for i in range(9, 0, -1)]
    with pytest.raises(StripeApiError) as raised:
        page_embedded(
            items,
            Page(limit=2, starting_after="il_nope", ending_before=None),
            url="/v1/invoices/in_1/lines",
            object_name="line_item",
        )
    assert raised.value.status == 400
    assert raised.value.code == "resource_missing"
    assert raised.value.param == "starting_after"
    assert raised.value.message == "No such line_item: 'il_nope'"


def test_page_embedded_resolves_both_cursors_before_refusing_the_pair() -> None:
    """Mirrors `page()`: a bogus cursor 400s even alongside a second cursor,
    and only a pair that both resolves is refused — message wire-verbatim,
    no `code` (recorded Phase 5, scenario 09)."""
    from seahaven_stripe_world.dispatch.resource import page_embedded
    from seahaven_stripe_world.dispatch.response import Page
    from seahaven_stripe_world.stripe_errors import StripeApiError

    items = [{"id": f"il_{i}"} for i in range(9, 0, -1)]

    with pytest.raises(StripeApiError) as bogus:
        page_embedded(
            items,
            Page(limit=2, starting_after="il_nope", ending_before="il_5"),
            url="/v1/invoices/in_1/lines",
            object_name="line_item",
        )
    assert bogus.value.status == 400
    assert bogus.value.code == "resource_missing"
    assert bogus.value.param == "starting_after"

    with pytest.raises(StripeApiError) as pair:
        page_embedded(
            items,
            Page(limit=2, starting_after="il_7", ending_before="il_5"),
            url="/v1/invoices/in_1/lines",
            object_name="line_item",
        )
    assert pair.value.status == 400
    assert pair.value.code is None
    assert (
        pair.value.message
        == "Received both starting_after and ending_before parameters. Please pass in only one."
    )


def test_has_more_on_an_exactly_full_last_page(instance: seahaven.Instance) -> None:
    for i in range(4):
        create(instance, f"c{i:02}")
    full = list_customers(instance, limit=4)["body"]
    assert full["has_more"] is False  # exactly full, and the last: the probe row earns its keep
    partial = list_customers(instance, limit=3)["body"]
    assert partial["has_more"] is True


def test_has_more_on_an_empty_page_past_the_end(instance: seahaven.Instance) -> None:
    newest = create(instance, "only")
    past_end = list_customers(instance, starting_after=newest)["body"]
    assert past_end == {
        "object": "list",
        "data": [],
        "has_more": False,
        "url": "/v1/customers",
    }


def test_starting_after_is_exclusive(instance: seahaven.Instance) -> None:
    """The cursor walks toward older rows, and the named object never appears."""
    older = create(instance, "older")
    newest = create(instance, "newest")
    page = list_customers(instance, starting_after=newest)["body"]
    assert [item["id"] for item in page["data"]] == [older]
    page = list_customers(instance, starting_after=older)["body"]
    assert page["data"] == []


def test_unknown_cursor_is_400_resource_missing(instance: seahaven.Instance) -> None:
    """A query-side `resource_missing` is a 400, unlike the 404 a path id
    earns — recorded Phase 5, scenario 09."""
    result = list_customers(instance, starting_after="cus_nope")
    assert result["status"] == 400
    error = result["body"]["error"]
    assert error["code"] == "resource_missing"
    assert error["param"] == "starting_after"
    assert error["message"] == "No such customer: 'cus_nope'"


def test_deleted_cursor_resolves_and_excludes_the_object(instance: seahaven.Instance) -> None:
    """The designed answer to a documented silence: a cursor is a coordinate,
    not a membership test (`components/cross_cutting.md` §3.2.4) — the deleted
    row's position still cuts the page, and it never appears in one."""
    oldest = create(instance, "oldest")
    middle = create(instance, "middle")
    create(instance, "newest")
    instance.call("stripe_api_write", method="DELETE", path=f"/v1/customers/{middle}")

    # The deleted row's coordinate still cuts the page: below it sits the
    # oldest row, and the deleted row itself never appears.
    page = list_customers(instance, starting_after=middle)["body"]
    assert [item["id"] for item in page["data"]] == [oldest]
    every = list_customers(instance, limit=10)["body"]
    assert middle not in [item["id"] for item in every["data"]]


def test_listing_writes_nothing(instance: seahaven.Instance) -> None:
    """Pagination is read-only, on the success path and the failure path alike
    (`components/cross_cutting.md` §3.2.7): the change log gains nothing."""
    create(instance, "seed")
    marker = len(instance.change_log())
    list_customers(instance, limit=1)
    assert len(instance.change_log()) == marker
    result = list_customers(instance, starting_after="cus_missing")
    assert result["status"] == 400
    assert len(instance.change_log()) == marker
