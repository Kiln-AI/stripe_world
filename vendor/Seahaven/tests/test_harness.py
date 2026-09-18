"""A stock OpenEnv harness against a served world, and what `serving_and_openenv.md` promises it.

The docs make four claims about upstream code this repository does not own.
§"Why there are no rewards" closes on three of them: that `_resolve_env_reward`
raises one exact sentence against a Seahaven rollout, that passing
`verify_builder=` is the whole fix, and that the state document reaches
`verify()` with no Seahaven-specific code in the harness. §"Grading a run" and
`state.md` make the fourth: that `reset` is where a per-step reader chooses the
cheaper state format. The docs state each in a clause; each is checked in full
here, which is why these tests are longer than the prose they guard. Every one is
a fact about `openenv`, so every one can go stale on a version bump with nothing
in this repository noticing. That is what these tests are for: a bump that
changes any of the four fails here, and whoever bumps it fixes the docs.

Driven through a real server on a real port, and through OpenEnv's own
`MCPHarnessAdapter`, because the claims are about what a consumer sees rather
than about any part Seahaven wrote.
"""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from seahaven.world import World
from tests.conftest import INSTANT_ISO

# The subpackage and not `openenv`: what this module imports is
# `seahaven.openenv`, so that is what has to import for the tests below to mean
# anything.
pytest.importorskip(
    "seahaven.openenv", exc_type=ImportError, reason="the serve extra does not import here"
)

from openenv.core.env_server.mcp_types import CallToolAction, Tool
from openenv.core.harness import (
    HarnessRolloutResult,
    HarnessRunLimits,
    MCPHarnessAdapter,
    ModelStepResult,
    ResourceSessionFactory,
    StepEnvSessionAdapter,
    VerifyResult,
    # Private upstream, imported deliberately: the page names
    # `_resolve_env_reward` to its reader, so a rename is a documentation defect
    # and this import is part of what catches it. The collection error that a
    # removal produces here is the intended failure and not an accident.
    _resolve_env_reward,
)
from openenv.core.harness.collect import (
    CollectRunner,
    RolloutSerializer,
    push_to_hf_hub,
)
from openenv.core.llm_client import LLMResponse, ToolCall

from seahaven.openenv import SeahavenClient
from tests.serving import serving

# The sentence `serving_and_openenv.md` quotes. Spelled out here rather than
# imported, because the page quotes the text and not the name:
# a reworded exception upstream keeps every import in this file working and
# makes the page wrong.
DOCUMENTED_FAILURE = "rollout did not produce an environment reward"

# Two writes, so that the change log has something to grow by between steps.
WRITES = (
    "INSERT INTO notes (id, body, n) VALUES ('n1', 'one', 1)",
    "INSERT INTO notes (id, body, n) VALUES ('n2', 'two', 2)",
)

TOOL_SPECS = [
    Tool(
        name="execute",
        description="Run one statement for its effect.",
        input_schema={"type": "object", "properties": {"sql": {"type": "string"}}},
    )
]


def model_step(
    messages: list[dict[str, Any]], tools: list[Tool], sampling: dict[str, Any]
) -> ModelStepResult:
    """A scripted model: one `execute` per turn, then a turn with no tool call.

    Which write comes next is read out of the transcript rather than held in a
    closure, because `CollectRunner` drives every episode of a run with the one
    `model_step` it was given. A counter would script the first episode and leave
    the rest silent.

    Ending on silence rather than on a limit is deliberate: it is how
    `MCPHarnessAdapter` stops when nothing sets `done`, which is the only way a
    Seahaven rollout ever ends.
    """
    done_so_far = sum(1 for message in messages if message.get("role") == "tool")
    if done_so_far >= len(WRITES):
        return ModelStepResult(response=LLMResponse(content="finished", tool_calls=[]))
    return ModelStepResult(
        response=LLMResponse(
            content="",
            tool_calls=[
                ToolCall(
                    id=f"call-{done_so_far}", name="execute", args={"sql": WRITES[done_so_far]}
                )
            ],
        )
    )


def _session(url: str, **options: Any) -> StepEnvSessionAdapter:
    """The adapter as a consumer following the page builds it, without the `verify_builder`.

    The `verify_builder` is the one part a caller here supplies for itself,
    because most of these tests are about the rollout that has no reward.
    `now=` is the tests' own addition, so that a document compares equal run to
    run.
    """
    reset_kwargs = {"now": INSTANT_ISO, **options.pop("reset_kwargs", {})}
    return StepEnvSessionAdapter(
        SeahavenClient(base_url=url),
        tool_specs=TOOL_SPECS,
        action_builder=lambda name, arguments: CallToolAction(
            tool_name=name, arguments=dict(arguments)
        ),
        initial_messages_builder=lambda reset_result, task: [
            {"role": "user", "content": "write two notes"}
        ],
        reset_kwargs=reset_kwargs,
        **options,
    )


@pytest.fixture
def rollout(world: World) -> Iterator[tuple[StepEnvSessionAdapter, HarnessRolloutResult]]:
    """One finished rollout of the default format, and the session that produced it."""
    with serving(world) as url:
        session = _session(url)
        try:
            yield (
                session,
                MCPHarnessAdapter().run_white_box(
                    model_step=model_step, session=session, limits=HarnessRunLimits(max_turns=5)
                ),
            )
        finally:
            session.close()


def test_a_rollout_carries_no_reward_and_no_done_anywhere(
    rollout: tuple[StepEnvSessionAdapter, HarnessRolloutResult],
) -> None:
    """Both halves of "`reward` is always `null` and `done` is always `false`", on the wire."""
    session, result = rollout
    assert len(result.tool_trace) == len(WRITES)
    for entry in result.tool_trace:
        assert entry.result.metadata["reward"] is None
        assert entry.result.data["reward"] is None
        assert entry.result.done is False
    # The rollout still ended, because the scripted model stopped asking. Nothing
    # in the environment ended it: `HarnessRolloutResult.done` is set by the
    # adapter's own "no more tool calls" exit, and `verify()` answers `False`.
    assert result.done is True
    assert session.verify(transcript=result.messages).done is False


def test_resolving_the_reward_raises_the_sentence_the_page_quotes(
    rollout: tuple[StepEnvSessionAdapter, HarnessRolloutResult],
) -> None:
    """The failure a stock consumer hits, with the exact text the page prints."""
    session, result = rollout
    with pytest.raises(ValueError, match=DOCUMENTED_FAILURE):
        _resolve_env_reward(result, session.verify(transcript=result.messages))


def test_an_env_reward_resolves_where_a_missing_one_raises(
    rollout: tuple[StepEnvSessionAdapter, HarnessRolloutResult],
) -> None:
    """Upstream's resolver on its own: a score reaches the caller unchanged.

    Unchanged matters: `_resolve_env_reward` prefers a reward found in the tool
    trace and raises when the two disagree. Neither branch can fire for a
    Seahaven rollout, because no entry of the trace carries a reward at all.

    This is the resolver and not the wiring the page tells a consumer to write.
    The test below is that wiring.
    """
    _, result = rollout
    assert _resolve_env_reward(result, VerifyResult(env_reward=0.75)) == 0.75


def test_a_verify_builder_is_the_keyword_the_page_tells_a_consumer_to_pass(
    world: World,
) -> None:
    """The documented wiring end to end: the keyword, through `verify()`, to the reward.

    `verify_builder` is the one name the page hands a reader, so the keyword,
    the builder's four arguments in order, and the route from `verify()` to
    `_resolve_env_reward` are all pinned here. The builder grades the document
    rather than ignoring its arguments, because an argument that moved would
    otherwise still answer 0.75.
    """
    seen: dict[str, Any] = {}

    def verify_builder(
        transcript: list[dict[str, Any]], final_state: Any, last_result: Any, state: Any
    ) -> VerifyResult:
        seen["document"] = state.model_dump(exclude={"step_count"})
        notes = len(seen["document"]["state"]["db"]["log"])
        return VerifyResult(env_reward=notes / len(WRITES), done=True)

    with serving(world) as url:
        session = _session(url, verify_builder=verify_builder)
        try:
            result = MCPHarnessAdapter().run_white_box(
                model_step=model_step, session=session, limits=HarnessRunLimits(max_turns=5)
            )
            verified = session.verify(transcript=result.messages)
            assert _resolve_env_reward(result, verified) == 1.0
        finally:
            session.close()

    # The fourth argument really was the state document, and not some other
    # object that happened to answer `model_dump`.
    assert seen["document"]["format"] == "seahaven.state/1"
    assert seen["document"]["world"]["name"] == "testworld"


def test_the_state_document_reaches_verify_with_no_seahaven_code_in_the_harness(
    rollout: tuple[StepEnvSessionAdapter, HarnessRolloutResult],
) -> None:
    """`artifacts["final_state"]` from the stock `verify()`: the document, plus `step_count`."""
    session, result = rollout
    verified = session.verify(transcript=result.messages)
    document = verified.artifacts["final_state"]
    assert document["format"] == "seahaven.state/1"
    assert document["world"]["name"] == "testworld"
    assert [record["after"]["id"] for record in document["state"]["db"]["log"]] == ["n1", "n2"]
    # The artifact is `model_dump()` with nothing excluded, so it is the document
    # *and* OpenEnv's `step_count`. The page says so, because a grader that
    # compares the artifact against `inst.state()` in process would not match.
    assert "step_count" in document


def test_the_default_format_repeats_the_whole_log_on_every_step(
    rollout: tuple[StepEnvSessionAdapter, HarnessRolloutResult],
) -> None:
    """Why the docs send a per-step reader elsewhere: each step carries every record so far."""
    _, result = rollout
    per_step = [
        len(entry.result.metadata["state"]["state"]["db"]["log"]) for entry in result.tool_trace
    ]
    assert per_step == [1, 2]


class _SessionFactory(ResourceSessionFactory):
    """One session per episode, which is what `CollectRunner` asks a factory for."""

    def __init__(self, url: str) -> None:
        self._url = url

    def create(
        self, task: Any, seed: int | None = None, episode_id: str | None = None
    ) -> StepEnvSessionAdapter:
        return _session(self._url, seed=seed, episode_id=episode_id)


def test_a_collect_run_of_a_seahaven_world_collects_nothing(world: World, tmp_path: Path) -> None:
    """The headline consequence the page states, run rather than reasoned about.

    Three episodes, every one of them a full rollout that the collector then
    throws away, because `EpisodeRecord.from_rollout` resolves the reward and
    there is none. A consumer pays for the model calls and keeps no episode.
    """
    output_dir = tmp_path / "collected"
    serializer = RolloutSerializer(output_dir)
    with serving(world) as url:
        collected = CollectRunner(
            session_factory=_SessionFactory(url),
            harness_adapter=MCPHarnessAdapter(),
            serializer=serializer,
            limits=HarnessRunLimits(max_turns=5),
        ).run(model_step=model_step, num_episodes=3)

    assert (collected.num_failed, collected.num_collected) == (3, 0)
    assert (collected.avg_reward, collected.success_rate) == (0.0, 0.0)
    assert not serializer.results_path.exists()
    # And the file the run was for is the file the upload then cannot find.
    with pytest.raises(FileNotFoundError, match=re.escape("No results.jsonl found")):
        push_to_hf_hub(output_dir=output_dir, repo_id="nobody/never-pushed")


def test_reset_kwargs_is_where_a_per_step_reader_asks_for_the_cheaper_format(
    world: World,
) -> None:
    """The spelling the docs give, and that it reaches every step and the final document."""
    with serving(world) as url:
        session = _session(url, reset_kwargs={"state_format": "seahaven.state+last_step/1"})
        try:
            result = MCPHarnessAdapter().run_white_box(
                model_step=model_step, session=session, limits=HarnessRunLimits(max_turns=5)
            )
            for entry in result.tool_trace:
                stamped = entry.result.metadata["state"]
                assert stamped["format"] == "seahaven.state+last_step/1"
                assert len(stamped["state"]["db"]["log"]) == 1
            final = session.verify(transcript=result.messages).artifacts["final_state"]
            assert final["format"] == "seahaven.state+last_step/1"
        finally:
            session.close()
