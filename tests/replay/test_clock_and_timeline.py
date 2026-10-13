"""The historical clock and the timeline (replay.md §1, §2, §8)."""

import pytest
import seahaven
from fixtures_src.replay.client import Client
from fixtures_src.replay.clock import at, plus
from fixtures_src.replay.timeline import FixtureBuildError, Timeline

from replay.support import NOW, blank, rows

T = "2026-03-02T15:45:00.000Z"


def test_at_reads_the_instant_and_leaves_the_base_alone() -> None:
    with blank() as inst, inst.bulk() as ctx:
        shifted = at(ctx, T)
        assert shifted.clock.iso() == T
        assert ctx.clock.iso() == NOW
        assert shifted.db is ctx.db and shifted.state is ctx.state


def test_plus_renders_canonically() -> None:
    assert plus(T, days=1, hours=2) == "2026-03-03T17:45:00.000Z"
    assert plus(NOW, days=-30) == "2026-08-02T14:00:00.000Z"


def test_rows_ids_and_events_carry_the_instant() -> None:
    with blank() as inst:
        with inst.bulk() as ctx:
            customer = Client().call(at(ctx, T), "POST", "/v1/customers", {"name": "Ines Duarte"})
        assert customer["created"] == 1772466300  # T in Unix seconds
        stored = rows(inst, "SELECT created FROM customers WHERE id = ?", customer["id"])
        assert stored == [{"created": T}]
        events = rows(inst, "SELECT created, request_id FROM events ORDER BY x_seq")
        assert events and all(e["created"] == T for e in events)
        assert all(str(e["request_id"]).startswith("req_") for e in events)


def test_writes_land_in_the_bulk_transaction() -> None:
    with blank() as inst:
        with pytest.raises(RuntimeError), inst.bulk() as ctx:
            Client().call(at(ctx, T), "POST", "/v1/customers", {"name": "Lost Row"})
            raise RuntimeError("abort")
        assert rows(inst, "SELECT id FROM customers") == []


def _recorder(ran: list[str], name: str):
    def run(ctx: seahaven.Ctx) -> None:
        ran.append(f"{name}@{ctx.clock.iso()}")

    return run


def test_timeline_order_ties_and_the_future() -> None:
    with blank() as inst, inst.bulk() as ctx:
        ran: list[str] = []
        tl = Timeline(ctx, until=NOW)
        assert tl.add("2026-05-01T00:00:00.000Z", "late", _recorder(ran, "late"))
        assert tl.add("2026-04-01T00:00:00.000Z", "first", _recorder(ran, "first"))
        assert tl.add("2026-04-01T00:00:00.000Z", "second", _recorder(ran, "second"))
        assert not tl.add("2026-09-02T00:00:00.000Z", "future", _recorder(ran, "future"))
        assert tl.now == "2026-04-01T00:00:00.000Z"
        tl.run()
        assert ran == [
            "first@2026-04-01T00:00:00.000Z",
            "second@2026-04-01T00:00:00.000Z",
            "late@2026-05-01T00:00:00.000Z",
        ]


def test_timeline_refuses_the_past_and_names_failing_steps() -> None:
    with blank() as inst, inst.bulk() as ctx:
        tl = Timeline(ctx, until=NOW)

        def backwards(_ctx: seahaven.Ctx) -> None:
            tl.add("2026-01-01T00:00:00.000Z", "too early", lambda _c: None)

        tl.add("2026-02-01T00:00:00.000Z", "backwards", backwards)
        with pytest.raises(FixtureBuildError, match=r"backwards at 2026-02-01T00:00:00.000Z"):
            tl.run()


def test_a_refused_call_raises_with_its_status() -> None:
    with blank() as inst, inst.bulk() as ctx:
        with pytest.raises(FixtureBuildError, match="404"):
            Client().call(at(ctx, T), "GET", "/v1/customers/cus_missing")
        response = Client().attempt(at(ctx, T), "GET", "/v1/customers/cus_missing")
        assert response.status == 404
