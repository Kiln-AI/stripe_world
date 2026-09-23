"""The generated-CRUD engine, exercised against the throwaway customers slice
through the real four-tool chain (`components/dispatcher.md` §6)."""

import pytest
import seahaven

from conftest import BLANK_NOW, dispatch_tool
from seahaven_stripe_world.dispatch.response import ApiResponse

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


@pytest.fixture(autouse=True, scope="module")
def _clean_by_object():
    """Undo this module's probe-resource registrations.

    `_probe_routes()` / `_note_spec()` register into the module-global
    `BY_OBJECT` with no teardown, and a leaked `note` changes another suite's
    verdict: the schema-conformance walker treats a registered-but-unspecced
    discriminator as scaffolding to skip, so its "an unregistered type is
    flagged" assertion fails once this module has run first. Module scope,
    deliberately: a per-test teardown would run before the schema-conformance
    hook's teardown validation and un-register `note` under it mid-check.
    Every test here (re-)registers what it needs, so holding the snapshot
    across the module is safe.
    """
    from seahaven_stripe_world.dispatch import resource

    before = set(resource.BY_OBJECT)
    yield
    for added in set(resource.BY_OBJECT) - before:
        resource.BY_OBJECT.pop(added)


def create(instance: seahaven.Instance, **params: object) -> dict:
    result = instance.call("stripe_api_write", method="POST", path="/v1/customers", params=params)
    assert result["status"] == 200, result
    return result["body"]


def test_crud_round_trip(instance: seahaven.Instance) -> None:
    created = create(instance, email="ada@example.test", name="Ada")
    assert created["object"] == "customer"
    cus = created["id"]

    retrieved = instance.call("stripe_api_read", path=f"/v1/customers/{cus}")
    assert retrieved["status"] == 200
    assert retrieved["body"] == created  # the create answers what a retrieve answers

    updated = instance.call(
        "stripe_api_write", method="POST", path=f"/v1/customers/{cus}", params={"name": "Ada L."}
    )
    assert updated["body"]["name"] == "Ada L."

    listed = instance.call("stripe_api_read", path="/v1/customers")
    assert [item["id"] for item in listed["body"]["data"]] == [cus]

    deleted = instance.call("stripe_api_write", method="DELETE", path=f"/v1/customers/{cus}")
    assert deleted["status"] == 200
    assert deleted["body"] == {"id": cus, "object": "customer", "deleted": True}


def test_created_id_has_the_right_prefix_and_created_is_unix(instance: seahaven.Instance) -> None:
    from conftest import BLANK_NOW as NOW
    from seahaven_stripe_world import _time

    created = create(instance)
    assert created["id"].startswith("cus_")
    # Format A (cus_): 14-char random suffix (id-shapes.md)
    assert len(created["id"]) == len("cus_") + 14
    assert created["created"] == _time.to_unix(NOW)


def test_created_defaults_match_the_live_api(instance: seahaven.Instance) -> None:
    """This phase's probes pinned the defaults of a fresh customer."""
    created = create(instance)
    assert created["tax_exempt"] == "none"
    assert created["delinquent"] is False
    assert created["balance"] == 0
    assert created["preferred_locales"] == []
    assert created["metadata"] == {}
    assert created["next_invoice_sequence"] == 1
    assert len(created["invoice_prefix"]) == 8
    assert created["invoice_prefix"].isupper()
    assert created["invoice_settings"] == {
        "custom_fields": None,
        "default_payment_method": None,
        "footer": None,
        "rendering_options": None,
    }


def test_nulled_and_omitted_fields_follow_the_spec(instance: seahaven.Instance) -> None:
    """`always_present` (required plus nullable) is emitted, null when empty;
    everything else — `business_name`, `sources`, `cash_balance`, … — is
    absent, never null."""
    created = create(instance)
    assert created["livemode"] is True
    assert created["default_source"] is None
    assert created["test_clock"] is None
    assert created["description"] is None
    assert "business_name" not in created
    assert "cash_balance" not in created
    assert "sources" not in created
    assert "subscriptions" not in created


def test_soft_delete_is_still_retrievable_and_filtered_from_lists(
    instance: seahaven.Instance,
) -> None:
    gone = create(instance, name="gone")
    staying = create(instance, name="staying")
    instance.call("stripe_api_write", method="DELETE", path=f"/v1/customers/{gone['id']}")

    stub = instance.call("stripe_api_read", path=f"/v1/customers/{gone['id']}")
    assert stub["status"] == 200
    assert stub["body"]["deleted"] is True

    listed = instance.call("stripe_api_read", path="/v1/customers")
    assert [item["id"] for item in listed["body"]["data"]] == [staying["id"]]


def test_update_absent_means_unchanged(instance: seahaven.Instance) -> None:
    created = create(instance, email="keep@example.test", name="Keep", description="kept")
    updated = instance.call(
        "stripe_api_write",
        method="POST",
        path=f"/v1/customers/{created['id']}",
        params={"name": "Changed"},
    )
    assert updated["body"]["name"] == "Changed"
    assert updated["body"]["email"] == "keep@example.test"
    assert updated["body"]["description"] == "kept"


def test_update_of_a_missing_row_is_resource_missing(instance: seahaven.Instance) -> None:
    result = instance.call(
        "stripe_api_write", method="POST", path="/v1/customers/cus_missing", params={"name": "x"}
    )
    assert result["status"] == 404
    assert result["body"]["error"]["code"] == "resource_missing"


def test_list_filters(instance: seahaven.Instance) -> None:
    from seahaven_stripe_world import _time

    one = create(instance, email="one@example.test")
    create(instance, email="two@example.test")
    listed = instance.call(
        "stripe_api_read", path="/v1/customers", params={"email": "one@example.test"}
    )
    assert [item["id"] for item in listed["body"]["data"]] == [one["id"]]
    assert listed["body"]["url"] == "/v1/customers"

    # A `created` range that excludes the frozen instant excludes everything.
    before = _time.to_unix(BLANK_NOW) - 3600
    empty = instance.call(
        "stripe_api_read", path="/v1/customers", params={"created": {"lt": before}}
    )
    assert empty["body"]["data"] == []
    # …and one that includes it includes the rows.
    present = instance.call(
        "stripe_api_read", path="/v1/customers", params={"created": {"gte": before}}
    )
    assert len(present["body"]["data"]) == 2


def test_list_is_reverse_chronological_under_a_frozen_clock(instance: seahaven.Instance) -> None:
    """Every row shares one `created` to the millisecond; `x_seq` — not the
    timestamp, not the seeded-random id — is what makes the order creation
    order (`components/data_model.md` §3.11)."""
    ids = [create(instance, name=f"c{i}")["id"] for i in range(3)]
    listed = instance.call("stripe_api_read", path="/v1/customers")
    assert [item["id"] for item in listed["body"]["data"]] == list(reversed(ids))


def test_generated_writes_emit_their_events(instance: seahaven.Instance) -> None:
    created = create(instance, name="eventful")
    cus = created["id"]
    instance.call(
        "stripe_api_write", method="POST", path=f"/v1/customers/{cus}", params={"name": "e2"}
    )
    instance.call("stripe_api_write", method="DELETE", path=f"/v1/customers/{cus}")

    rows = instance.inspect().rows("SELECT type, data FROM events ORDER BY x_seq")
    assert [row["type"] for row in rows] == [
        "customer.created",
        "customer.updated",
        "customer.deleted",
    ]
    # The snapshot is the object as of the change, not as it stands now: the
    # created event still shows the original name after the update.
    import json

    assert json.loads(rows[0]["data"])["object"]["name"] == "eventful"
    assert json.loads(rows[1]["data"])["previous_attributes"] == {"name": "eventful"}


def test_event_request_id_is_minted_per_call(instance: seahaven.Instance) -> None:
    create(instance)
    create(instance)
    rows = instance.inspect().rows("SELECT request_id FROM events ORDER BY x_seq")
    assert [row["request_id"][:4] for row in rows] == ["req_", "req_"]
    assert rows[0]["request_id"] != rows[1]["request_id"]


def test_delete_emits_after_the_write_and_the_snapshot_has_no_deleted_key(
    instance: seahaven.Instance,
) -> None:
    """The Phase 3 carry-over, settled by recording (cassette
    `probe_customer_payment_method_events`): `customer.deleted`'s
    `data.object` is the full pre-delete object with no `deleted` key, and
    emission follows the write like every other action — observable here as
    the event ordering within the call: a delete that followed a create and
    an update lists third, after both."""
    import json

    created = create(instance, name="gone soon")
    cus = created["id"]
    instance.call(
        "stripe_api_write", method="POST", path=f"/v1/customers/{cus}", params={"name": "gone"}
    )
    instance.call("stripe_api_write", method="DELETE", path=f"/v1/customers/{cus}")

    rows = instance.inspect().rows("SELECT type, data FROM events ORDER BY x_seq")
    assert [row["type"] for row in rows] == [
        "customer.created",
        "customer.updated",
        "customer.deleted",
    ]
    obj = json.loads(rows[2]["data"])["object"]
    assert "deleted" not in obj
    assert obj["name"] == "gone"


def test_an_empty_without_filter_yields_an_empty_page_not_an_error(probe, monkeypatch) -> None:
    """`ListFilter.empty_without` (this phase's one list-semantics knob):
    without the filter the page is empty — probed on
    `GET /v1/payment_methods` — while cursors still resolve and a scoped
    route never sees the clause."""
    from conftest import BLANK_NOW as NOW
    from seahaven_stripe_world.dispatch import routes as routes_module
    from seahaven_stripe_world.dispatch.params import ParamSpec
    from seahaven_stripe_world.dispatch.router import Router
    from seahaven_stripe_world.dispatch.routes import Route

    routes = (
        Route(
            method="GET",
            pattern="/v1/probe_notes",
            op_id="GetProbeNotes",
            params=ParamSpec(op_id="GetProbeNotes", paginated=True),
            resource=_note_spec(),
            action="list",
            response_object="note",
            envelope="list",
        ),
        *_probe_routes(),
    )
    monkeypatch.setattr(
        "seahaven_stripe_world.dispatch.router.ROUTER", Router((*routes_module.ALL, *routes))
    )
    world = probe(dispatch_tool(), schema=SCOPED_SCHEMA)
    with world.instance(None, now=NOW) as instance:
        cus = instance.call("call_stripe", method="POST", path="/v1/customers")["body"]["id"]
        with instance.bulk() as ctx:
            ctx.db.execute(
                "INSERT INTO probe_notes (id, x_seq, created, body, customer)"
                " VALUES ('note_a', 1, ?, 'a', ?)",
                NOW,
                cus,
            )

        empty = instance.call("call_stripe", method="GET", path="/v1/probe_notes")
        assert empty["status"] == 200
        assert empty["body"]["data"] == []

        # A bogus cursor still 400s on the empty page: resolution precedes
        # the clauses, exactly as on a populated one.
        bogus = instance.call(
            "call_stripe", method="GET", path="/v1/probe_notes", params={"starting_after": "note_z"}
        )
        assert bogus["status"] == 400

        filtered = instance.call(
            "call_stripe", method="GET", path="/v1/probe_notes", params={"customer": cus}
        )
        assert [item["id"] for item in filtered["body"]["data"]] == ["note_a"]

        # A scoped route carrying the same filter serves its parent's rows:
        # the scope pins the column, so `empty_without` must not fire.
        scoped = instance.call("call_stripe", method="GET", path=f"/v1/customers/{cus}/probe_notes")
        assert [item["id"] for item in scoped["body"]["data"]] == ["note_a"]


# --- A scoped child list and hand-written handler semantics, on a probe world ---


SCOPED_SCHEMA = """
CREATE TABLE probe_notes (
    id        TEXT PRIMARY KEY,
    x_seq     INTEGER NOT NULL,
    created   TEXT NOT NULL,
    body      TEXT NOT NULL,
    customer  TEXT NOT NULL REFERENCES customers (id)
) STRICT;
CREATE UNIQUE INDEX probe_notes_by_seq ON probe_notes (x_seq DESC);
"""


def _probe_routes() -> tuple:
    from seahaven_stripe_world.dispatch.params import ParamSpec
    from seahaven_stripe_world.dispatch.resource import Scope
    from seahaven_stripe_world.dispatch.routes import Route
    from seahaven_stripe_world.resources.customers import SPEC

    return (
        Route(
            method="GET",
            pattern="/v1/customers/{customer}/probe_notes",
            op_id="GetCustomersCustomerProbeNotes",
            params=ParamSpec(
                op_id="GetCustomersCustomerProbeNotes",
                path=("customer",),
                paginated=True,
            ),
            resource=_note_spec(),
            action="list",
            response_object="note",
            envelope="list",
            scope=Scope(path_param="customer", column="customer", parent=SPEC),
        ),
    )


_NOTE_SPEC = {}


def _note_spec():
    from seahaven_stripe_world.dispatch.resource import ListFilter, ResourceSpec, register
    from seahaven_stripe_world.serialize.fields import FieldMap, serializer_for

    if not _NOTE_SPEC:
        _NOTE_SPEC["spec"] = register(
            ResourceSpec(
                object="note",
                table="probe_notes",
                id_prefix="note_",
                collection_url="/v1/customers/{customer}/probe_notes",
                serializer=serializer_for(
                    FieldMap(
                        object="note",
                        table="probe_notes",
                        columns={"id": "id", "created": "created", "body": "body"},
                        timestamps=frozenset({"created"}),
                    )
                ),
                columns=("id", "created", "body", "customer"),
                list_filters=(
                    # `empty_without` is what the new test exercises; the
                    # scoped test above must keep working despite it.
                    ListFilter(
                        name="customer", column="customer", kind="exact", empty_without=True
                    ),
                ),
                creatable=None,
                updatable=None,
                delete=None,
                metadata=False,
            )
        )
    return _NOTE_SPEC["spec"]


def test_scoped_list_filters_by_parent_and_404s_a_missing_one(probe, monkeypatch) -> None:
    from conftest import BLANK_NOW as NOW
    from seahaven_stripe_world.dispatch import routes as routes_module
    from seahaven_stripe_world.dispatch.router import Router

    monkeypatch.setattr(
        "seahaven_stripe_world.dispatch.router.ROUTER",
        Router((*routes_module.ALL, *_probe_routes())),
    )
    world = probe(dispatch_tool(), schema=SCOPED_SCHEMA)
    with world.instance(None, now=NOW) as instance:
        cus = instance.call("call_stripe", method="POST", path="/v1/customers")["body"]["id"]
        # Seeded by hand: a probe table has no counters row, and the list
        # path under test reads rather than writes.
        with instance.bulk() as ctx:
            for seq, body in ((1, "first"), (2, "second")):
                ctx.db.execute(
                    "INSERT INTO probe_notes (id, x_seq, created, body, customer)"
                    " VALUES (?, ?, ?, ?, ?)",
                    f"note_{body}",
                    seq,
                    NOW,
                    body,
                    cus,
                )

        listed = instance.call("call_stripe", method="GET", path=f"/v1/customers/{cus}/probe_notes")
        assert listed["status"] == 200
        assert [item["body"] for item in listed["body"]["data"]] == ["second", "first"]
        # The envelope's url is the concrete scoped path.
        assert listed["body"]["url"] == f"/v1/customers/{cus}/probe_notes"

        missing = instance.call(
            "call_stripe", method="GET", path="/v1/customers/cus_nope/probe_notes"
        )
        assert missing["status"] == 404
        assert missing["body"]["error"]["code"] == "resource_missing"
        assert missing["body"]["error"]["param"] == "customer"


def test_a_raised_handler_rolls_back_and_a_returned_failure_commits(probe, monkeypatch) -> None:
    """The dispatcher's one rule, through a real chain: raise loses the
    writes (zero change-log records), return keeps them
    (`components/cross_cutting.md` §3.5.4)."""
    from conftest import BLANK_NOW as NOW
    from seahaven_stripe_world.dispatch import routes as routes_module
    from seahaven_stripe_world.dispatch.params import ParamSpec
    from seahaven_stripe_world.dispatch.response import Request
    from seahaven_stripe_world.dispatch.router import Router
    from seahaven_stripe_world.dispatch.routes import Route
    from seahaven_stripe_world.stripe_errors import resource_missing

    def refusing(ctx: seahaven.Ctx, req: Request) -> dict:
        cus = req.path_params["customer"]
        ctx.db.execute(
            "INSERT INTO probe_notes (id, x_seq, created, body, customer)"
            " VALUES ('note_doomed', 99, ?, 'doomed', ?)",
            NOW,
            cus,
        )
        raise resource_missing("customer", cus)

    def declining(ctx: seahaven.Ctx, req: Request) -> ApiResponse:
        cus = req.path_params["customer"]
        ctx.db.execute(
            "INSERT INTO probe_notes (id, x_seq, created, body, customer)"
            " VALUES ('note_kept', 98, ?, 'kept', ?)",
            NOW,
            cus,
        )
        return ApiResponse(
            402, {"error": {"type": "card_error", "message": "Your card was declined."}}
        )

    routes = (
        Route(
            method="POST",
            pattern="/v1/customers/{customer}/probe_refuse",
            op_id="PostRefuse",
            params=ParamSpec(op_id="PostRefuse", path=("customer",), expand=False),
            handler=refusing,
        ),
        Route(
            method="POST",
            pattern="/v1/customers/{customer}/probe_decline",
            op_id="PostDecline",
            params=ParamSpec(op_id="PostDecline", path=("customer",), expand=False),
            handler=declining,
        ),
    )
    monkeypatch.setattr(
        "seahaven_stripe_world.dispatch.router.ROUTER", Router((*routes_module.ALL, *routes))
    )
    world = probe(dispatch_tool(), schema=SCOPED_SCHEMA)
    with world.instance(None, now=NOW) as instance:
        cus = instance.call("call_stripe", method="POST", path="/v1/customers")["body"]["id"]

        refused = instance.call(
            "call_stripe", method="POST", path=f"/v1/customers/{cus}/probe_refuse"
        )
        assert refused["status"] == 404
        assert instance.inspect().one(
            "SELECT count(*) AS n FROM probe_notes WHERE id = 'note_doomed'"
        ) == {"n": 0}
        # The whole call left nothing behind.
        assert [record for record in instance.change_log() if record.table == "probe_notes"] == []

        declined = instance.call(
            "call_stripe", method="POST", path=f"/v1/customers/{cus}/probe_decline"
        )
        assert declined["status"] == 402
        assert declined["body"] == {
            "error": {"type": "card_error", "message": "Your card was declined."}
        }
        assert instance.inspect().one(
            "SELECT count(*) AS n FROM probe_notes WHERE id = 'note_kept'"
        ) == {"n": 1}
        kept = [record for record in instance.change_log() if record.table == "probe_notes"]
        assert [record.op for record in kept] == ["insert"]
