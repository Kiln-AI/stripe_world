"""The call path: the chain a call descends, the transaction it runs in, what comes back."""

import logging
from collections import deque
from collections.abc import Callable
from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

import pytest
from pydantic import BaseModel, ConfigDict, Field, computed_field

from seahaven.call import Call, Handler, build_chain, invoke, serialise
from seahaven.ctx import Ctx
from seahaven.errors import ArgumentError, ToolError, WorldBug
from seahaven.handles import Worlds
from seahaven.tool import Tool

TABLE = "CREATE TABLE notes (id TEXT PRIMARY KEY) STRICT"


@pytest.fixture
def notes(ctx: Ctx) -> Ctx:
    """A context whose database has one table for a tool to write to."""
    ctx.db.execute(TABLE)
    return ctx


def call_to(fn: Callable[..., Any], /, **arguments: Any) -> Call:
    tool = Tool.from_function(fn)
    return Call(tool.name, arguments, tool)


def run(ctx: Ctx, call: Call, handler: Handler = invoke) -> Any:
    return handler(ctx.with_call(call), call)


def write(ctx: Ctx, id: str) -> dict[str, str]:
    """Write one note."""
    ctx.db.execute("INSERT INTO notes (id) VALUES (?)", id)
    return {"id": id}


def test_middleware_runs_outermost_first_and_unwinds_inwards_last(ctx: Ctx) -> None:
    order: list[str] = []

    def record(label: str) -> Any:
        def middleware(ctx: Ctx, call: Call, next_: Handler) -> Any:
            order.append(f"{label} in")
            result = next_(ctx, call)
            order.append(f"{label} out")
            return result

        return middleware

    def echo(ctx: Ctx, word: str) -> dict[str, str]:
        """Echo."""
        order.append("tool")
        return {"word": word}

    chain = build_chain([record("first"), record("second"), record("third")], invoke)

    assert run(ctx, call_to(echo, word="hi"), chain) == {"word": "hi"}
    assert order == [
        "first in",
        "second in",
        "third in",
        "tool",
        "third out",
        "second out",
        "first out",
    ]


def test_a_middleware_can_answer_without_calling_the_tool(ctx: Ctx) -> None:
    reached = False

    def never(ctx: Ctx, word: str) -> dict[str, str]:
        """Never."""
        nonlocal reached
        reached = True
        return {"word": word}

    def short_circuit(ctx: Ctx, call: Call, next_: Handler) -> Any:
        return {"cached": True}

    chain = build_chain([short_circuit], invoke)

    assert run(ctx, call_to(never, word="hi"), chain) == {"cached": True}
    assert reached is False


def test_rewritten_arguments_are_the_call_and_the_context_everywhere_below(ctx: Ctx) -> None:
    def rewrite(ctx: Ctx, call: Call, next_: Handler) -> Any:
        assert call is ctx.call
        return next_(ctx, call.with_arguments(word="rewritten"))

    def check(ctx: Ctx, call: Call, next_: Handler) -> Any:
        # The chain normalises the context as it descends, so a middleware that
        # passes a new `Call` on does not have to rebuild the context to match.
        assert call is ctx.call
        assert call.arguments["word"] == "rewritten"
        return next_(ctx, call)

    def echo(ctx: Ctx, word: str) -> dict[str, str]:
        """Echo."""
        assert ctx.call is not None
        assert ctx.call.arguments["word"] == "rewritten"
        return {"word": word}

    chain = build_chain([rewrite, check], invoke)

    assert run(ctx, call_to(echo, word="original"), chain) == {"word": "rewritten"}


def test_invalid_arguments_reach_the_middleware_that_wraps_them(ctx: Ctx) -> None:
    seen: list[ArgumentError] = []

    def handler(ctx: Ctx, call: Call, next_: Handler) -> Any:
        try:
            return next_(ctx, call)
        except ArgumentError as error:
            seen.append(error)
            raise

    def echo(ctx: Ctx, word: str) -> dict[str, str]:
        """Echo."""
        return {"word": word}

    chain = build_chain([handler], invoke)

    with pytest.raises(ArgumentError):
        run(ctx, call_to(echo, word=7), chain)
    assert seen[0].tool == "echo"


def test_a_tool_is_called_with_exactly_the_validated_arguments(ctx: Ctx) -> None:
    """What `validate` returns is what the function is called with, and nothing else.

    The wire name of an aliased argument is not a parameter of the function, so a
    call carrying one must not reach it; the tool sees its Python name, a default
    it declared and did not receive, a relaxed argument coerced, and a nested
    model rather than a dict. `ctx.call` carries the same, which is what every
    reader of the current call downstream -- an error handler, the per-call log,
    control dispatch -- sees.
    """

    class Point(BaseModel):
        x: int
        y: int

    def move(
        ctx: Ctx,
        from_: Annotated[str, Field(alias="from")],
        origin: Point,
        limit: Annotated[int, Field(strict=False)] = 10,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Move."""
        # `ctx.call` is the call the tool is running, and it holds what the tool
        # was given: the same arguments, under the same names.
        assert ctx.call is not None
        assert ctx.call.arguments == {
            "from_": "here",
            "origin": Point(x=1, y=2),
            "limit": 5,
            "dry_run": False,
        }
        return {"from": from_, "origin": origin.x + origin.y, "limit": limit, "dry": dry_run}

    tool = Tool.from_function(move)
    call = Call(tool.name, {"from": "here", "origin": {"x": 1, "y": 2}, "limit": "5"}, tool)

    assert run(ctx, call) == {"from": "here", "origin": 3, "limit": 5, "dry": False}


def test_a_tool_that_raises_leaves_nothing_behind(notes: Ctx) -> None:
    def write_then_fail(ctx: Ctx, id: str) -> dict[str, str]:
        """Write, then fail."""
        write(ctx, id)
        raise ToolError("nope", "no")

    with pytest.raises(ToolError):
        run(notes, call_to(write_then_fail, id="a"))

    assert notes.db.rows("SELECT id FROM notes") == []


def test_a_tool_outside_a_transaction_keeps_what_it_wrote(notes: Ctx) -> None:
    def write_then_fail(ctx: Ctx, id: str) -> dict[str, str]:
        """Write, then fail."""
        write(ctx, id)
        raise ToolError("nope", "no")

    tool = Tool.from_function(write_then_fail, transaction=False)

    with pytest.raises(ToolError):
        run(notes, Call(tool.name, {"id": "a"}, tool))

    assert notes.db.rows("SELECT id FROM notes") == [{"id": "a"}]


def test_a_result_that_cannot_be_serialised_rolls_the_call_back(notes: Ctx) -> None:
    def write_and_return_bytes(ctx: Ctx, id: str) -> Any:
        """Write, and answer with something that cannot go on the wire."""
        write(ctx, id)
        return {"blob": b"\x00"}

    with pytest.raises(WorldBug, match="bytes"):
        run(notes, call_to(write_and_return_bytes, id="a"))

    assert notes.db.rows("SELECT id FROM notes") == []


def test_a_result_is_proved_to_render_and_then_handed_back_as_it_is(ctx: Ctx) -> None:
    """The object the tool returned, not its rendering (architecture section 8.4).

    A host tool calling an added world's tool receives what that tool built, which
    is what makes the typed call path's `R` true; the rendering is the wire's, and
    `openenv/env.py` does it there.
    """

    class Note(BaseModel):
        id: str
        at: datetime

    @dataclass
    class Page:
        notes: list[Note]
        next: str | None

    page_out = Page(notes=[Note(id="n1", at=datetime(2024, 3, 5, 12, tzinfo=UTC))], next=None)

    def page(ctx: Ctx) -> Any:
        """Page."""
        return page_out

    def nothing(ctx: Ctx) -> Any:
        """Nothing."""
        return None

    result = run(ctx, call_to(page))

    assert result is page_out
    assert serialise(result) == {
        "notes": [{"id": "n1", "at": "2024-03-05T12:00:00Z"}],
        "next": None,
    }
    assert run(ctx, call_to(nothing)) is None


@pytest.mark.parametrize(
    ("result", "refused"),
    [
        (b"raw", "bytes"),
        ({"blob": bytearray(b"raw")}, "bytes"),
        ([{"nested": [b"raw"]}], "bytes"),
        ({"tags": {"a", "b"}}, "sets"),
        (frozenset({"a"}), "sets"),
    ],
)
def test_values_that_would_not_replay_are_refused(ctx: Ctx, result: Any, refused: str) -> None:
    def answer(ctx: Ctx) -> Any:
        """Answer."""
        return result

    with pytest.raises(WorldBug, match=refused):
        run(ctx, call_to(answer))


def test_a_dataclasses_fields_are_walked_too(ctx: Ctx) -> None:
    @dataclass
    class Attachment:
        blob: bytes

    def attached(ctx: Ctx) -> Any:
        """Attached."""
        return {"attachment": Attachment(blob=b"raw")}

    with pytest.raises(WorldBug, match="bytes"):
        run(ctx, call_to(attached))


def test_a_mappings_keys_are_walked_as_well_as_its_values(ctx: Ctx) -> None:
    """`to_jsonable_python` renders a `bytes` key as text like any other bytes."""

    def keyed(ctx: Ctx) -> Any:
        """Keyed."""
        return {b"raw": 1}

    with pytest.raises(WorldBug, match="bytes"):
        run(ctx, call_to(keyed))


def test_a_result_pydantic_cannot_render_at_all_is_a_world_bug(notes: Ctx) -> None:
    class Connection:
        pass

    def answer(ctx: Ctx, id: str) -> Any:
        """Answer."""
        write(ctx, id)
        return {"connection": Connection()}

    with pytest.raises(WorldBug, match="JSON-able"):
        run(notes, call_to(answer, id="a"))

    assert notes.db.rows("SELECT id FROM notes") == []


def test_a_result_inside_a_model_is_checked_too(ctx: Ctx) -> None:
    class Blob(BaseModel):
        data: bytes

    def answer(ctx: Ctx) -> Any:
        """Answer."""
        return Blob(data=b"raw")

    with pytest.raises(WorldBug, match="bytes"):
        run(ctx, call_to(answer))


def test_what_a_model_serialises_beyond_its_fields_is_checked_too(ctx: Ctx) -> None:
    """Extras and computed fields are rendered as fields and are not in `__dict__`."""

    class Loose(BaseModel):
        model_config = ConfigDict(extra="allow")

        id: str

    class Computed(BaseModel):
        id: str

        @computed_field
        def blob(self) -> bytes:
            return b"raw"

    def loose(ctx: Ctx) -> Any:
        """Loose."""
        return Loose(id="n1", blob=b"raw")

    def computed(ctx: Ctx) -> Any:
        """Computed."""
        return Computed(id="n1")

    for fn in (loose, computed):
        with pytest.raises(WorldBug, match="bytes"):
            run(ctx, call_to(fn))


def test_a_result_that_has_to_be_consumed_to_be_read_is_refused(ctx: Ctx) -> None:
    """A generator renders as a list, in which a set would arrive silently ordered."""

    def streamed(ctx: Ctx) -> Any:
        """Streamed."""
        return (row for row in [{"id": "n1"}])

    with pytest.raises(WorldBug, match="generator"):
        run(ctx, call_to(streamed))


def test_a_container_a_model_declares_is_walked_like_any_other(ctx: Ctx) -> None:
    """pydantic renders a declared `deque`; the check in front of it descends one too."""

    class Page(BaseModel):
        rows: deque[bytes]

    def page(ctx: Ctx) -> Any:
        """Page."""
        return Page(rows=deque([b"raw"]))

    with pytest.raises(WorldBug, match="bytes"):
        run(ctx, call_to(page))


def test_a_result_that_contains_itself_is_refused_and_a_shared_one_is_not(ctx: Ctx) -> None:
    def circular(ctx: Ctx) -> Any:
        """Circular."""
        result: dict[str, Any] = {}
        result["self"] = result
        return result

    def shared(ctx: Ctx) -> Any:
        """Shared."""
        one = {"id": "n1"}
        return {"first": one, "second": one}

    with pytest.raises(WorldBug, match="itself"):
        run(ctx, call_to(circular))
    assert run(ctx, call_to(shared)) == {"first": {"id": "n1"}, "second": {"id": "n1"}}


def test_state_outlives_a_call_and_the_call_does_not(ctx: Ctx) -> None:
    seen: list[Call | None] = []

    def count(ctx: Ctx) -> dict[str, int]:
        """Count."""
        ctx.state["calls"] = ctx.state.get("calls", 0) + 1
        seen.append(ctx.call)
        return {"calls": ctx.state["calls"]}

    assert run(ctx, call_to(count)) == {"calls": 1}
    assert run(ctx, call_to(count)) == {"calls": 2}
    # The instance's state is one object; the call is a new one each time.
    assert ctx.state == {"calls": 2}
    assert seen[0] is not seen[1]
    assert ctx.call is None


def test_with_call_keeps_the_contexts_worlds_unless_it_is_handed_another(ctx: Ctx) -> None:
    """The two answers the two overloads describe (architecture section 6.3).

    Only the second widens the context's type parameter, which is why they are
    two: a world that declared `Ctx[CompanyWorlds]` keeps that declaration inside
    its own middleware, where the only thing being changed is the call.
    """
    call = call_to(write, id="a")
    elsewhere = Worlds()

    bound = ctx.with_call(call)
    rebound = ctx.with_call(call, worlds=elsewhere)

    assert bound.call is call
    assert bound.worlds is ctx.worlds
    assert rebound.worlds is elsewhere
    assert bound.db is ctx.db and bound.state is ctx.state


def test_an_unexpected_exception_is_logged_with_its_traceback_and_re_raised(
    ctx: Ctx, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(ctx: Ctx) -> dict[str, str]:
        """Broken."""
        raise ValueError("a bug in world code")

    with caplog.at_level(logging.ERROR, logger="seahaven.call"), pytest.raises(ValueError):
        run(ctx, call_to(broken))

    record = caplog.records[0]
    assert "broken" in record.getMessage()
    assert ctx.instance.id in record.getMessage()
    assert record.exc_info is not None


def test_an_error_the_agent_is_meant_to_read_is_not_logged(
    ctx: Ctx, caplog: pytest.LogCaptureFixture
) -> None:
    def refuses(ctx: Ctx) -> dict[str, str]:
        """Refuse."""
        raise ToolError("not_found", "no such note")

    with caplog.at_level(logging.ERROR, logger="seahaven.call"), pytest.raises(ToolError):
        run(ctx, call_to(refuses))

    assert caplog.records == []


def test_the_objects_one_call_is_made_of_are_frozen(ctx: Ctx) -> None:
    """A `Call` and the `Ctx` around it are values: a layer rewrites by copying."""
    call = call_to(write, id="a")

    for target, attribute in (
        (call, "name"),
        (ctx, "state"),
        (ctx.instance, "id"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(target, attribute, "changed")


def test_with_arguments_merges_and_leaves_the_original_alone(ctx: Ctx) -> None:
    def echo(ctx: Ctx, word: str, times: int = 1) -> dict[str, Any]:
        """Echo."""
        return {"word": word, "times": times}

    call = call_to(echo, word="hi", times=2)

    changed = call.with_arguments(times=3)

    assert changed.arguments == {"word": "hi", "times": 3}
    assert call.arguments == {"word": "hi", "times": 2}
    assert changed.tool is call.tool
