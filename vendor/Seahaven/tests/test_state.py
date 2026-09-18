"""The state document: the envelope the framework writes, and the format that fills `state`.

`inst.state()` is the one thing an eval saves. What is pinned here is the
provenance -- every field of `functional_spec.md` §3.1, on a blank instance and
on one made from a fixture, on one node and on the committed composite -- the
three built-in formats over the change log `test_change_log.py` pins, the
registration and resolution of a world's own format, and the three refusals that
keep a formatter a reader: it never writes, it never runs in a transaction, and
the document it hands back is the caller's to edit.
"""

import copy
import json
import re
import threading
from pathlib import Path
from typing import Any

import emporium
import jsonschema
import payments
import pytest

from seahaven.ctx import Ctx
from seahaven.errors import WorldBug
from seahaven.instances import Instance
from seahaven.state import (
    SEAHAVEN_STATE_CALLS_V1,
    SEAHAVEN_STATE_LAST_STEP_V1,
    SEAHAVEN_STATE_V1,
)
from seahaven.world import World
from tests.conftest import INSTANT_ISO, WAIT, Boom, Caller, build_world, composable_world
from tests.test_change_log import counting_statements
from tests.test_changes import add

SCHEMA = json.loads((Path(__file__).parent / "state_v1.schema.json").read_text())

# The one field of the envelope that is a new UUID per instance, so two documents
# of two identical episodes are compared without it.
PER_INSTANCE = "episode_id"


def rooted(name: str, tmp_path: Path, **options: Any) -> World:
    """A world that can make instances and freeze fixtures, on a throwaway directory."""
    return composable_world(
        name, fixtures_dir=tmp_path / "fixtures", work_dir=tmp_path / "work", **options
    )


def log_of(document: dict[str, Any]) -> list[dict[str, Any]]:
    return document["state"]["db"]["log"]


def emporium_world(tmp_path: Path) -> World:
    """The committed composite, pointed at a throwaway working directory."""
    world = copy.copy(emporium.world)
    world.work_dir = tmp_path / "work"
    world.fixtures_dir = tmp_path / "fixtures"
    return world


# --------------------------------------------------------------------- the envelope


def test_the_envelope_reports_the_format_the_versions_and_the_world(instance: Instance) -> None:
    document = instance.state()

    assert list(document) == [
        "format",
        "seahaven_version",
        "world",
        "composition",
        "fixture",
        "episode_id",
        "seed",
        "now",
        "startup",
        "call_count",
        "state",
    ]
    assert document["format"] == SEAHAVEN_STATE_V1
    assert document["world"] == {"name": "testworld", "version": "1.0.0"}
    assert document["now"] == INSTANT_ISO
    assert document["call_count"] == 0


def test_the_episode_id_is_the_instance_id_in_process(instance: Instance) -> None:
    """In process an instance is an episode; over OpenEnv `reset` mints the id."""
    assert instance.state()["episode_id"] == instance.id


def test_the_seed_is_the_one_the_caller_gave(world: World) -> None:
    """Not the derived seed the id streams run on, which is a hash of this and the fixture."""
    with world.instance(None, seed=7) as live:
        assert live.state()["seed"] == 7
    with world.instance(None) as live:
        assert live.state()["seed"] is None


def test_the_call_count_is_the_calls_dispatched(instance: Instance) -> None:
    add(instance, "n1")
    add(instance, "n2")

    assert instance.state()["call_count"] == instance.call_count == 2


def test_a_blank_instance_has_a_composition_and_no_fixture(instance: Instance) -> None:
    """What tells a blank instance from no instance at all: a clock and a composition."""
    document = instance.state()

    assert document["fixture"] is None
    assert document["composition"] == {
        "main": {
            "world": "testworld",
            "world_version": "1.0.0",
            "scope": None,
            "aliases": [],
            "schema_hash": instance.world.schema_hash,
            "frozen_world_version": None,
        }
    }


def test_the_composition_is_every_node_keyed_by_path_root_first(tmp_path: Path) -> None:
    live_world = emporium_world(tmp_path)
    with live_world.instance(None, now=INSTANT_ISO) as live:
        composition = live.state()["composition"]

    assert list(composition) == ["main", "payments", "payments_eu", "shop"]
    assert composition["payments_eu"] == {
        "world": "payments",
        "world_version": "1.4.0",
        "scope": "eu",
        "aliases": [],
        "schema_hash": payments.world.schema_hash,
        "frozen_world_version": None,
    }
    assert composition["payments"]["aliases"] == ["shop/payments"]


def test_a_fixture_instance_reports_the_fixtures_files_by_path(tmp_path: Path) -> None:
    """`fixture.nodes` is the sidecar's hashes, the root included, keyed as `composition` is."""
    live_world = emporium_world(tmp_path)
    with live_world.instance(None, now=INSTANT_ISO) as live:
        live.call("pay_create_charge", amount=100)
        fixture = live.freeze("charged", "one charge on the company account")

    with live_world.instance("charged") as live:
        reported = live.state()["fixture"]

    assert reported["id"] == "charged"
    assert list(reported["nodes"]) == ["main", "payments", "payments_eu", "shop"]
    assert reported["nodes"]["main"] == {"file_sha256": fixture.meta.file_sha256}
    assert reported["nodes"]["payments"] == {
        "file_sha256": next(
            node.file_sha256 for node in fixture.meta.nodes if node.path == "payments"
        )
    }


def test_the_startup_keywords_are_reported_as_the_hooks_received_them(tmp_path: Path) -> None:
    world = build_world(tmp_path)

    @world.instance_startup
    def remember(ctx: Ctx, *, tier: str = "free", limits: dict[str, Any] | None = None) -> None:
        """Take two keywords, one of them nested."""
        ctx.state["tier"] = tier

    with world.instance(None, tier="paid", limits={"calls": 10}) as live:
        assert live.state()["startup"] == {"tier": "paid", "limits": {"calls": 10}}
    with world.instance(None) as live:
        assert live.state()["startup"] == {}


def test_a_startup_keyword_a_document_could_not_carry_is_refused_at_creation(
    tmp_path: Path,
) -> None:
    """Named, and at creation: a keyword that cannot be reported is not a late failure."""
    world = build_world(tmp_path)

    @world.instance_startup
    def remember(ctx: Ctx, *, ledger: object = None) -> None:
        """Take anything at all, which in process includes what JSON cannot carry."""

    with pytest.raises(WorldBug, match="startup keyword 'ledger' must be JSON-able"):
        world.instance(None, ledger=threading.Lock())


def test_two_identical_episodes_produce_the_same_document(tmp_path: Path) -> None:
    """Everything but the episode id is a function of the world, the fixture and the calls."""
    world = build_world(tmp_path)

    def episode() -> str:
        with world.instance(None, seed=3, now=INSTANT_ISO) as live:
            add(live, "n1", "hello", 1)
            live.call("execute", sql="UPDATE notes SET n = 2 WHERE id = 'n1'")
            document = live.state()
        return json.dumps(
            {k: v for k, v in document.items() if k != PER_INSTANCE}, ensure_ascii=False
        )

    assert episode() == episode()


# ---------------------------------------------------------------- the built-in formats


def test_state_v1_carries_the_whole_log(instance: Instance) -> None:
    add(instance, "n1")
    add(instance, "n2")

    document = instance.state()

    assert [(record["i"], record["key"]["id"]) for record in log_of(document)] == [
        (0, "n1"),
        (1, "n2"),
    ]
    assert log_of(document) == [record.to_dict() for record in instance.change_log()]


def test_last_step_carries_the_records_of_the_last_call_alone(world: World) -> None:
    with world.instance(None, state_format=SEAHAVEN_STATE_LAST_STEP_V1) as live:
        add(live, "n1")
        add(live, "n2")

        document = live.state()

    assert document["format"] == SEAHAVEN_STATE_LAST_STEP_V1
    assert [record["key"]["id"] for record in log_of(document)] == ["n2"]


def test_last_step_is_idempotent_and_empty_before_any_call(world: World) -> None:
    """Scoped by the call counter, not by when `state()` was last read."""
    with world.instance(None, state_format=SEAHAVEN_STATE_LAST_STEP_V1) as live:
        assert log_of(live.state()) == []

        add(live, "n1")

        assert live.state() == live.state()


def test_the_last_step_documents_of_an_episode_concatenate_into_the_whole_log(
    world: World,
) -> None:
    with world.instance(None, state_format=SEAHAVEN_STATE_LAST_STEP_V1) as live:
        per_step = []
        for note in ("n1", "n2", "n3"):
            add(live, note)
            per_step.extend(log_of(live.state()))

        assert per_step == log_of(live.state(format=SEAHAVEN_STATE_V1))


def test_a_bulk_write_is_never_a_last_step_record(world: World) -> None:
    """A record with no ordinal belongs to no call, so no call's step holds it."""
    with world.instance(None, state_format=SEAHAVEN_STATE_LAST_STEP_V1) as live:
        with live.bulk() as ctx:
            ctx.db.execute("INSERT INTO notes VALUES ('n1', 'authored', 0)")
        add(live, "n2")

        assert [record["key"]["id"] for record in log_of(live.state())] == ["n2"]


def test_calls_carries_the_call_log_indexed_by_the_records_ordinal(world: World) -> None:
    with world.instance(None, state_format=SEAHAVEN_STATE_CALLS_V1) as live:
        add(live, "n1")
        with pytest.raises(Boom):
            live.call("write_then_fail", sql="INSERT INTO notes VALUES ('n2', 'never', 0)")

        document = live.state()

    calls = document["state"]["calls"]
    assert [call["tool"] for call in calls] == ["execute", "write_then_fail"]
    assert calls[0]["error"] is None
    assert calls[1]["error"] == "it did not work out"
    assert calls[log_of(document)[0]["i"]]["arguments"] == {
        "sql": "INSERT INTO notes VALUES ('n1', 'a body', 0)"
    }


def test_the_calls_document_publishes_only_what_a_world_wrote_for_the_agent(
    tmp_path: Path,
) -> None:
    """The document is a wire boundary, and three classes of error arrive at it.

    OpenEnv's `StepEnvSessionAdapter` puts the whole document into every trace
    entry, so a harness that renders a trace back into a model's context would
    otherwise read a `WorldBug`'s wording, or a Python exception's, to the agent.
    A `ToolError` is published as written; the other two become the generic error.
    """
    world = build_world(tmp_path)

    @world.tool
    def misuse(ctx: Ctx) -> None:
        """Fail the way a broken world fails."""
        raise WorldBug("the fixture names a table this world does not have")

    with world.instance(None, state_format=SEAHAVEN_STATE_CALLS_V1) as live:
        with pytest.raises(Boom):
            live.call("write_then_fail", sql="INSERT INTO notes VALUES ('n1', 'never', 0)")
        with pytest.raises(WorldBug):
            live.call("misuse")
        with pytest.raises(ValueError):
            live.call("crash")

        published = [entry["error"] for entry in live.state()["state"]["calls"]]
        # In process nothing is hidden: the author's own message is still there.
        assert [record.error for record in live.call_log()] == [
            "it did not work out",
            "the fixture names a table this world does not have",
            "a bug in world code",
        ]

    assert published == ["it did not work out", "internal error", "internal error"]


def test_an_argument_that_cannot_be_deep_copied_still_reaches_the_calls_document(
    tmp_path: Path,
) -> None:
    """The capture tolerates it, so the document does too; JSON is then the caller's problem.

    A document is JSON-able exactly when its calls' arguments were
    (`functional_spec.md` §4.2), and a call the framework let run must not turn
    `state()` into a failure.
    """
    world = build_world(tmp_path)
    lock = threading.Lock()

    @world.tool
    def hold(ctx: Ctx, thing: object) -> str:
        """Take a value no copy can be made of."""
        return type(thing).__name__

    with world.instance(None, state_format=SEAHAVEN_STATE_CALLS_V1) as live:
        live.call("hold", thing=lock)

        assert live.state()["state"]["calls"] == [
            {"tool": "hold", "arguments": {"thing": lock}, "error": None}
        ]


def test_every_built_in_answers_with_no_instance(world: World) -> None:
    """The environment before its first `reset`: the world is known, the episode is not."""
    from seahaven.state import document

    for name in (SEAHAVEN_STATE_V1, SEAHAVEN_STATE_LAST_STEP_V1, SEAHAVEN_STATE_CALLS_V1):
        answered = document(world, None, name, world.resolve_state_format(name))

        assert answered["world"] == {"name": "testworld", "version": "1.0.0"}
        assert answered["composition"] is None
        assert answered["fixture"] is None
        assert (answered["episode_id"], answered["seed"], answered["now"]) == (None, None, None)
        assert answered["startup"] is None
        assert answered["call_count"] == 0
        assert log_of(answered) == []
        if name == SEAHAVEN_STATE_CALLS_V1:
            assert answered["state"]["calls"] == []


def test_a_built_in_document_validates_against_the_published_schema(tmp_path: Path) -> None:
    live_world = emporium_world(tmp_path)
    with live_world.instance(None, now=INSTANT_ISO) as live:
        live.call("settle_order", total=250)
        live.call("pay_create_charge", amount=100)

        for name in (SEAHAVEN_STATE_V1, SEAHAVEN_STATE_LAST_STEP_V1, SEAHAVEN_STATE_CALLS_V1):
            jsonschema.validate(live.state(format=name), SCHEMA)


def test_the_published_schema_refuses_a_field_that_does_not_belong(instance: Instance) -> None:
    """The negative case: the schema is only worth reading if it fails on drift."""
    document = instance.state()
    document["summary"] = {"rows": 0}

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(document, SCHEMA)


# ------------------------------------------------------------------- custom formats


def test_a_custom_format_is_registered_and_selected_by_the_instance_keyword(
    tmp_path: Path,
) -> None:
    world = build_world(tmp_path)

    @world.state_format("acme.state/1")
    def acme(world: World, instance: Instance | None) -> dict[str, Any]:
        """Count the rows the episode changed, which no built-in does."""
        return {"rows": len(instance.change_log()) if instance is not None else 0}

    with world.instance(None, now=INSTANT_ISO, state_format="acme.state/1") as live:
        add(live, "n1")

        document = live.state()

    assert document["format"] == "acme.state/1"
    assert document["state"] == {"rows": 1}


def test_the_envelope_is_identical_under_a_custom_format(tmp_path: Path) -> None:
    """The framework writes the envelope, so a format can neither omit nor misspell provenance."""
    world = build_world(tmp_path)

    @world.state_format("acme.state/1")
    def acme(world: World, instance: Instance | None) -> dict[str, Any]:
        """Answer something no built-in would."""
        return {"rows": 0}

    with world.instance(None, seed=7, now=INSTANT_ISO) as live:
        add(live, "n1")
        built_in = live.state(format=SEAHAVEN_STATE_V1)
        custom = live.state(format="acme.state/1")

    assert {k: v for k, v in custom.items() if k not in ("format", "state")} == {
        k: v for k, v in built_in.items() if k not in ("format", "state")
    }


def test_a_custom_formatter_may_read_a_built_in_and_edit_what_it_answered(
    tmp_path: Path,
) -> None:
    """The recipe `functional_spec.md` §6 gives: read a built-in's `state`, change it."""
    world = build_world(tmp_path)

    @world.state_format("acme.trimmed/1")
    def trimmed(world: World, instance: Instance | None) -> dict[str, Any]:
        """`seahaven.state/1` with the tables named and the rows dropped."""
        assert instance is not None
        body = instance.state(format=SEAHAVEN_STATE_V1)["state"]
        body["db"]["tables"] = sorted({record["table"] for record in body["db"].pop("log")})
        return body

    with world.instance(None, state_format="acme.trimmed/1") as live:
        add(live, "n1")

        assert live.state()["state"] == {"db": {"tables": ["notes"]}}
        # The inner read restored the guard rather than clearing it.
        assert live.change_log()[0].to_dict()["table"] == "notes"


def test_a_formatter_that_returns_something_that_is_not_a_dict_is_a_world_bug(
    tmp_path: Path,
) -> None:
    world = build_world(tmp_path)

    @world.state_format("acme.state/1")
    def acme(world: World, instance: Instance | None) -> Any:
        """Answer the log itself, forgetting that a formatter answers `state`."""
        return []

    with (
        world.instance(None, state_format="acme.state/1") as live,
        pytest.raises(WorldBug, match="returned list"),
    ):
        live.state()


@pytest.mark.parametrize(
    "name", ["acme.state", "acme.state/0", "acme.state/v1", "acme state/1", "acme/state/1", ""]
)
def test_a_name_that_is_not_a_format_name_is_refused(tmp_path: Path, name: str) -> None:
    world = build_world(tmp_path)

    with pytest.raises(WorldBug, match="not a state format name"):
        world.state_format(name)


def test_a_name_under_the_frameworks_prefix_is_refused(tmp_path: Path) -> None:
    """Reserved, so a world cannot take a name a later Seahaven release publishes."""
    world = build_world(tmp_path)

    with pytest.raises(WorldBug, match="reserved for Seahaven's own formats"):
        world.state_format("seahaven.state/2")


def test_a_format_registered_twice_is_refused(tmp_path: Path) -> None:
    world = build_world(tmp_path)

    @world.state_format("acme.state/1")
    def first(world: World, instance: Instance | None) -> dict[str, Any]:
        """The one that got there."""
        return {}

    with pytest.raises(WorldBug, match="registered twice"):
        world.state_format("acme.state/1")(first)


def test_a_formatter_that_is_not_a_function_is_refused(tmp_path: Path) -> None:
    world = build_world(tmp_path)

    with pytest.raises(WorldBug, match="a state formatter is a function"):
        world.state_format("acme.state/1")("not a function")  # ty: ignore[invalid-argument-type]


# ------------------------------------------------------------------ choosing a format


def test_a_world_must_pin_a_state_format(tmp_path: Path) -> None:
    """A missing pin is a refusal that names the built-ins, not a `TypeError`."""
    with pytest.raises(WorldBug) as raised:
        World("unpinned", "1.0.0", "CREATE TABLE t (id TEXT PRIMARY KEY) STRICT;")

    message = str(raised.value)
    assert "must pin a state format" in message
    assert SEAHAVEN_STATE_V1 in message and SEAHAVEN_STATE_CALLS_V1 in message


def test_a_seahaven_name_that_is_not_built_in_is_refused_at_the_world(tmp_path: Path) -> None:
    with pytest.raises(WorldBug, match="which Seahaven does not publish"):
        build_world(tmp_path, state_format="seahaven.state/99")


@pytest.mark.parametrize("name", ["acme.state", "acme.state/0", "acme state/1", ""])
def test_a_pin_that_is_not_a_format_name_is_refused_at_the_world(tmp_path: Path, name: str) -> None:
    """Syntax is checked at the `World(...)` line; only resolution waits for the first instance."""
    with pytest.raises(WorldBug, match="not a state format name"):
        build_world(tmp_path, state_format=name)


def test_an_unregistered_custom_pin_is_refused_at_the_first_instance(tmp_path: Path) -> None:
    """It cannot be refused at `World(...)`: a world registers its formats after that line."""
    world = build_world(tmp_path, state_format="acme.state/1")

    with pytest.raises(WorldBug, match=re.escape("has no state format 'acme.state/1'")):
        world.instance(None)


def test_a_custom_pin_registered_after_the_world_line_works(tmp_path: Path) -> None:
    world = build_world(tmp_path, state_format="acme.state/1")

    @world.state_format("acme.state/1")
    def acme(world: World, instance: Instance | None) -> dict[str, Any]:
        """Registered later, which is the only time a world can register one."""
        return {"acme": True}

    with world.instance(None) as live:
        assert live.state()["state"] == {"acme": True}


def test_a_format_nothing_registered_leaves_no_instance_behind(tmp_path: Path) -> None:
    """Refused before a directory exists, like every other creation failure."""
    world = build_world(tmp_path)
    work_dir = tmp_path / "work"

    with pytest.raises(WorldBug, match="has no state format"):
        world.instance(None, state_format="acme.state/1")

    assert not work_dir.exists() or list(work_dir.rglob("*.sqlite")) == []


def test_the_roots_pin_governs_a_tree_and_a_leafs_is_ignored(tmp_path: Path) -> None:
    """`payments` pins `seahaven.state+calls/1`; an instance of `emporium` is the root's."""
    assert payments.world.pinned_state_format == SEAHAVEN_STATE_CALLS_V1
    live_world = emporium_world(tmp_path)

    with live_world.instance(None, now=INSTANT_ISO) as live:
        document = live.state()

    assert document["format"] == SEAHAVEN_STATE_V1
    assert "calls" not in document["state"]


def test_a_format_registered_on_a_leaf_is_not_resolvable_from_the_root(tmp_path: Path) -> None:
    """A formatter reads every node's rows, so one written for a leaf alone cannot serve a tree."""
    live_world = emporium_world(tmp_path)

    with (
        live_world.instance(None, now=INSTANT_ISO) as live,
        pytest.raises(WorldBug, match="world 'emporium' has no state format"),
    ):
        live.state(format="payments.state/1")


def test_a_leaf_used_as_a_root_uses_its_own_pin_and_its_own_formats(tmp_path: Path) -> None:
    live_world = copy.copy(payments.world)
    live_world.work_dir = tmp_path / "work"

    with live_world.instance(None, now=INSTANT_ISO) as live:
        live.call("create_charge", amount=250)

        assert live.state()["format"] == SEAHAVEN_STATE_CALLS_V1
        assert live.state()["state"]["calls"] == [
            {"tool": "create_charge", "arguments": {"amount": 250}, "error": None}
        ]
        assert live.state(format="payments.state/1")["state"] == {"charged": 250}


def test_a_startup_hook_may_not_be_given_the_reset_keyword(tmp_path: Path) -> None:
    """`state_format` is `reset`'s, as `fixture`, `seed` and `now` are."""
    world = build_world(tmp_path)

    with pytest.raises(WorldBug, match="state_format"):

        @world.instance_startup
        def hook(ctx: Ctx, *, state_format: str = "acme.state/1") -> None:
            """Name a keyword the framework has already spent."""


# ------------------------------------------------------- the document is the caller's


def test_editing_a_document_edits_nothing_the_instance_holds(tmp_path: Path) -> None:
    world = build_world(tmp_path)

    @world.instance_startup
    def remember(ctx: Ctx, *, limits: dict[str, Any] | None = None) -> None:
        """Take a nested keyword, which is what a shallow copy would share."""

    with world.instance(None, limits={"calls": 10}) as live:
        add(live, "n1")
        document = live.state()

        document["startup"]["limits"]["calls"] = 99
        document["startup"]["extra"] = True
        log_of(document)[0]["key"]["id"] = "rewritten"
        log_of(document)[0]["after"]["body"] = "rewritten"

        again = live.state()

        assert again["startup"] == {"limits": {"calls": 10}}
        assert log_of(again)[0]["key"] == {"id": "n1"}
        assert log_of(again)[0]["after"]["body"] == "a body"


# ------------------------------------------------------------------------- the guards


def test_a_formatter_that_calls_a_tool_is_refused(tmp_path: Path) -> None:
    """A formatter holds the instance lock, and the lock is re-entrant; this is what stops it."""
    world = build_world(tmp_path)
    held: dict[str, Instance] = {}

    @world.state_format("acme.writing/1")
    def writing(world: World, instance: Instance | None) -> dict[str, Any]:
        """Write from inside a read."""
        held["instance"].call("execute", sql="INSERT INTO notes VALUES ('n9', 'x', 0)")
        return {}

    with world.instance(None, state_format="acme.writing/1") as live:
        held["instance"] = live

        with pytest.raises(WorldBug, match="never writes to it"):
            live.state()

        assert live.change_log() == []


def test_a_formatter_that_opens_a_bulk_block_is_refused(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    held: dict[str, Instance] = {}

    @world.state_format("acme.writing/1")
    def writing(world: World, instance: Instance | None) -> dict[str, Any]:
        """The other way in to a write."""
        with held["instance"].bulk():
            pass
        return {}

    with world.instance(None, state_format="acme.writing/1") as live:
        held["instance"] = live

        with pytest.raises(WorldBug, match="never writes to it"):
            live.state()


def test_an_inner_read_leaves_the_outer_formatter_guarded(tmp_path: Path) -> None:
    """Saved and restored, not set and cleared: reading a built-in must not disarm the guard."""
    world = build_world(tmp_path)
    held: dict[str, Instance] = {}

    @world.state_format("acme.after/1")
    def after(world: World, instance: Instance | None) -> dict[str, Any]:
        """Read a built-in first, then try to write."""
        assert instance is not None
        instance.state(format=SEAHAVEN_STATE_V1)
        held["instance"].call("execute", sql="INSERT INTO notes VALUES ('n9', 'x', 0)")
        return {}

    with world.instance(None, state_format="acme.after/1") as live:
        held["instance"] = live

        with pytest.raises(WorldBug, match="never writes to it"):
            live.state()

        assert live.change_log() == []


def test_a_call_from_another_thread_waits_while_a_formatter_runs(tmp_path: Path) -> None:
    """Only the formatting thread is refused; anyone else queues for the lock, as always."""
    world = build_world(tmp_path)
    formatting = threading.Event()
    release = threading.Event()

    @world.state_format("acme.slow/1")
    def slow(world: World, instance: Instance | None) -> dict[str, Any]:
        """Hold the instance lock until the test lets go."""
        formatting.set()
        assert release.wait(WAIT)
        return {}

    with world.instance(None, state_format="acme.slow/1") as live:
        reader = Caller(live.state)
        reader.start()
        assert formatting.wait(WAIT)

        writer = Caller(lambda: add(live, "n1"))
        writer.start()
        writer.join(0.1)
        assert writer.is_alive(), "the call should be waiting for the lock, not refused"

        release.set()
        reader.finish()
        writer.finish()

        assert [record.key["id"] for record in live.change_log()] == ["n1"]


def test_state_inside_a_bulk_block_is_refused(instance: Instance) -> None:
    """The rows are not committed, so no document could describe them."""
    with instance.bulk() as ctx:
        ctx.db.execute("INSERT INTO notes VALUES ('n1', 'authored', 0)")

        with pytest.raises(WorldBug, match="cannot run inside a transaction"):
            instance.state()


def test_state_inside_a_tool_call_is_refused(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    held: dict[str, Instance] = {}

    @world.tool
    def peek(ctx: Ctx) -> dict[str, Any]:
        """Read the document from inside the call's own transaction."""
        return held["instance"].state()

    with world.instance(None) as live:
        held["instance"] = live

        with pytest.raises(WorldBug, match="cannot run inside a transaction"):
            live.call("peek")


def test_a_call_on_an_added_node_refuses_too(tmp_path: Path) -> None:
    """The transaction is the child's alone, and any node in one is enough to refuse."""
    host = rooted("host", tmp_path)
    child = composable_world("child")
    held: dict[str, Instance] = {}

    @child.tool(name="child_peek")
    def peek(ctx: Ctx) -> dict[str, Any]:
        """Read the document from inside a transaction the root knows nothing about."""
        return held["instance"].state()

    host.add_world(child, name="child")

    with host.instance(None) as live:
        held["instance"] = live

        with pytest.raises(WorldBug, match="cannot run inside a transaction"):
            live.call("child_peek")


def test_a_destroyed_instance_has_no_state(instance: Instance) -> None:
    instance.destroy()

    with pytest.raises(WorldBug, match="has been destroyed"):
        instance.state()


def test_reading_the_document_does_no_database_work(tmp_path: Path) -> None:
    """The log was rendered as each call committed; the document is serialisation alone."""
    live_world = emporium_world(tmp_path)
    with live_world.instance(None, now=INSTANT_ISO) as live:
        for _ in range(20):
            live.call("settle_order", total=250)
        connections = [runtime.db.conn for runtime in live._runtime.values()]

        with counting_statements(*connections) as watched:
            assert live.db.conn.execute("SELECT 1").get == 1
        assert watched  # the counter sees a statement when there is one

        with counting_statements(*connections) as statements:
            assert log_of(live.state())

        assert statements == []
