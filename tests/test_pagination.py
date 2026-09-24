"""Cursor pagination: the envelope, the cursors, and the walk."""

import pytest
import seahaven

from conftest import BLANK_NOW, api_read, api_write
from seahaven_stripe_world.errors import StripeToolError

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW, clock_mode="fixed")


def create(instance: seahaven.Instance, name: str) -> str:
    result = api_write(instance, "POST", "/v1/customers", {"name": name})
    return result["id"]


def list_customers(instance: seahaven.Instance, **params: object) -> dict:
    return api_read(instance, "/v1/customers", params)


def test_envelope_has_exactly_four_keys(instance: seahaven.Instance) -> None:
    create(instance, "one")
    body = list_customers(instance)
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
        page = list_customers(instance, **params)
        seen.extend(item["id"] for item in page["data"])
        if not page["has_more"]:
            break
        cursor = page["data"][-1]["id"]
    assert seen == newest_first


def test_ending_before_returns_the_previous_page_newest_first(instance: seahaven.Instance) -> None:
    ids = [create(instance, f"c{i:02}") for i in range(5)]
    newest_first = list(reversed(ids))

    page = list_customers(instance, ending_before=newest_first[2], limit=2)
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
    full = list_customers(instance, limit=4)
    assert full["has_more"] is False  # exactly full, and the last: the probe row earns its keep
    partial = list_customers(instance, limit=3)
    assert partial["has_more"] is True


def test_has_more_on_an_empty_page_past_the_end(instance: seahaven.Instance) -> None:
    newest = create(instance, "only")
    past_end = list_customers(instance, starting_after=newest)
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
    page = list_customers(instance, starting_after=newest)
    assert [item["id"] for item in page["data"]] == [older]
    page = list_customers(instance, starting_after=older)
    assert page["data"] == []


def test_unknown_cursor_is_400_resource_missing(instance: seahaven.Instance) -> None:
    """A query-side `resource_missing` is a 400, unlike the 404 a path id
    earns — recorded Phase 5, scenario 09."""
    with pytest.raises(StripeToolError) as exc_info:
        list_customers(instance, starting_after="cus_nope")
    error = exc_info.value.stripe_body["error"]
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
    api_write(instance, "DELETE", f"/v1/customers/{middle}")

    # The deleted row's coordinate still cuts the page: below it sits the
    # oldest row, and the deleted row itself never appears.
    page = list_customers(instance, starting_after=middle)
    assert [item["id"] for item in page["data"]] == [oldest]
    every = list_customers(instance, limit=10)
    assert middle not in [item["id"] for item in every["data"]]


def test_listing_writes_nothing(instance: seahaven.Instance) -> None:
    """Pagination is read-only, on the success path and the failure path alike
    (`components/cross_cutting.md` §3.2.7): the change log gains nothing."""
    create(instance, "seed")
    marker = len(instance.change_log())
    list_customers(instance, limit=1)
    assert len(instance.change_log()) == marker
    with pytest.raises(StripeToolError):
        list_customers(instance, starting_after="cus_missing")
    assert len(instance.change_log()) == marker


# --- missing tests from §5.2 ------------------------------------------------------------------


def test_default_limit_is_ten(instance: seahaven.Instance) -> None:
    """Absent `limit` defaults to 10."""
    for i in range(12):
        create(instance, f"c{i:02}")
    page = list_customers(instance)
    assert len(page["data"]) == 10
    assert page["has_more"] is True


def test_limit_bounds_are_clamped(instance: seahaven.Instance) -> None:
    """The live API clamps `limit` into [1, 100] rather than rejecting
    (settled by Phase 5 cassettes): `limit=0` returns 1 item, `limit=101`
    returns at most 100, both 200."""
    for i in range(3):
        create(instance, f"c{i}")
    # limit=0 is clamped to 1
    result = list_customers(instance, limit=0)
    assert len(result["data"]) == 1
    # limit=101 is clamped to 100
    result = list_customers(instance, limit=101)
    assert len(result["data"]) == 3  # only 3 exist, all returned
    # Explicit valid bounds
    for good in (1, 100):
        result = list_customers(instance, limit=good)
        assert isinstance(result["data"], list)


def test_walk_backward_covers_every_row_exactly_once(instance: seahaven.Instance) -> None:
    """The mirror of the forward walk: `ending_before` pages cover every
    row exactly once. Start from the oldest (walk from bottom up)."""
    ids = [create(instance, f"c{i:02}") for i in range(7)]
    newest_first = list(reversed(ids))

    # Walk forward to get the oldest cursor, then walk back up
    # First, get the last page's last item to walk backward from
    seen: list[str] = []
    # Start from the very oldest by getting the last item through forward walk
    all_items = list_customers(instance, limit=100)["data"]
    oldest = all_items[-1]["id"]

    # Walk backward from oldest, collecting all items above it
    cursor = oldest
    for _ in range(10):
        page = list_customers(instance, ending_before=cursor, limit=3)
        if not page["data"]:
            break
        seen.extend(item["id"] for item in page["data"])
        cursor = page["data"][0]["id"]  # the newest on this page

    # We collected everything except the oldest (used as cursor)
    seen.append(oldest)
    assert sorted(seen) == sorted(newest_first)


def test_both_cursors_is_parameters_exclusive(instance: seahaven.Instance) -> None:
    """Sending both `starting_after` and `ending_before` is a 400."""
    a = create(instance, "a")
    b = create(instance, "b")
    with pytest.raises(StripeToolError) as exc_info:
        list_customers(instance, starting_after=a, ending_before=b)
    assert "starting_after" in exc_info.value.message
    assert "ending_before" in exc_info.value.message


def test_cursor_resolution_ignores_list_filters(instance: seahaven.Instance) -> None:
    """A cursor on a customer whose email does not match the filter still
    resolves: a cursor is a coordinate, not a membership test (§3.2.3)."""
    a = create(instance, "alice")
    api_write(
        instance,
        "POST",
        f"/v1/customers/{a}",
        {"email": "alice@example.test"},
    )
    b = create(instance, "bob")
    api_write(
        instance,
        "POST",
        f"/v1/customers/{b}",
        {"email": "bob@example.test"},
    )
    # Cursor on bob, filter for alice: should resolve bob as coordinate
    # and return page of alice (if she is older than bob).
    result = api_read(
        instance,
        "/v1/customers",
        {"starting_after": b, "email": "alice@example.test"},
    )
    # Alice should appear since she was created before bob
    found = [item["id"] for item in result["data"]]
    assert a in found


def test_url_is_the_concrete_path_including_nested(instance: seahaven.Instance) -> None:
    """The list envelope's `url` is the concrete path, including nested lists."""
    create(instance, "root")
    page = list_customers(instance)
    assert page["url"] == "/v1/customers"
    # A nested list's url carries the parent id
    cus = page["data"][0]["id"]
    result = api_read(instance, f"/v1/customers/{cus}/balance_transactions")
    assert result["url"] == f"/v1/customers/{cus}/balance_transactions"
