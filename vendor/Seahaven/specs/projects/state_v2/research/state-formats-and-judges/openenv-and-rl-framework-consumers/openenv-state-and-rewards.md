# OpenEnv: the `State` type, the `/state` surface, and where rewards live

**Sources read (this session):** the `meta-pytorch/OpenEnv` repository at
`da5929566e99c8eb376a47042b316cb13c0aae29` (2026-09-10, `version = "0.4.3.dev0"` in
`pyproject.toml`), cloned from <https://github.com/meta-pytorch/OpenEnv>. All quotes below
are verbatim from that tree unless marked otherwise. `huggingface.co` (where the rendered
docs and the environment Spaces live) is blocked by this session's egress proxy, so the
equivalent files were read from the repo's `docs/source/` tree, which is what those pages
are built from.

**Release timeline (from git tags on that clone):**

| tag | date | what arrived (by file presence in the tag) |
|---|---|---|
| v0.2.1 | 2026-02-04 | MCP support; no rubrics |
| v0.2.2 | 2026-03-20 | `src/openenv/core/rubrics/` (RFC 004) |
| v0.2.3 | 2026-03-28 | |
| v0.3.0 | 2026-05-11 | `src/openenv/core/harness/` (RFC 005, `collect.py`, `EpisodeRecord`) |
| v0.3.1 | 2026-06-02 | |
| v0.4.0 | 2026-07-02 | `src/openenv/core/evals/` (`EvalHarness`, Inspect-style harness) |
| v0.4.1 | 2026-07-03 | `TaskProvider` protocol + Task API HTTP routes |
| v0.4.2 | 2026-09-09 | Task API guide doc; `pelican_svg_env`, `pi_env` |

(Note: a WebFetch of the rendered GitHub releases page returned 2024 dates for these tags.
That was a misread by the fetch summarizer; the tag dates above come from `git log` on the
tags themselves and are authoritative.)

---

## 1. The `State` type is almost empty, and deliberately open

`src/openenv/core/env_server/types.py`:

```python
class State(BaseModel):
    """Base class for environment state.

    Represents internal environment state, separate from observations.
    """

    model_config = ConfigDict(
        extra="allow",  # Allow extra fields for flexibility
        validate_assignment=True,
        arbitrary_types_allowed=True,
    )

    episode_id: Optional[str] = Field(
        default=None, description="Unique identifier for the current episode"
    )
    step_count: int = Field(
        default=0,
        ge=0,  # Greater than or equal to 0
        description="Number of steps taken in the current episode",
    )
```

Two base fields, `episode_id` and `step_count`. Everything else is environment-defined.
Note `extra="allow"` — unlike `Action` and `Observation`, which are both `extra="forbid"`.
So `State` is the one core type that tolerates unknown fields on the wire in both
directions. That is the mechanism by which a richer state survives a round trip through a
client that only knows the base class.

`Observation`, for contrast, is where `reward` and `done` live:

```python
class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", ...)
    done: bool = Field(default=False, ...)
    reward: bool | int | float | None = Field(default=None, ...)
    metadata: Dict[str, Any] = Field(default_factory=dict, ...)
```

`docs/source/guides/concepts.md` states this explicitly: "Reward and termination are
carried on the returned observation — they are **not** a tuple return value."

The environment ABC is generic over all three (`src/openenv/core/env_server/interfaces.py`):

```python
class Environment(ABC, Generic[ActT, ObsT, StateT]):
    ...
    @property
    @abstractmethod
    def state(self) -> StateT:
        """Get the current environment state."""
```

## 2. What environments actually put in `State` — a census

I parsed every `class *State(State)` in `envs/*/models.py` in the repo (28 environments).
Field counts, excluding the two inherited base fields:

| env | state class | # fields | representative fields |
|---|---|---|---|
| carla_env | CarlaState | 16 | scenario_name, town, weather, total_distance, total_reward, total_tool_calls, tool_call_counts |
| sumo_rl_env | SumoState | 15 | net_file, route_file, delta_time, sim_time, total_vehicles |
| wildfire_env | WildfireState | 13 | total_burned, wind_dir, remaining_water, **grid** |
| coding_tools_env | CodingToolsState | 9 | sandbox_id, setup_results, **verify_commands, verify_results**, todos, tool_history, submitted, last_reward |
| textarena_env | TextArenaState | 9 | env_id, turn, last_reward, last_info, **raw_state** |
| jupyter_env | JupyterState | 8 | cells, sandbox_id, verify_results, verify_commands, submitted_answer, last_reward |
| terminus_env | TerminusState | 8 | sandbox_id, setup_results, verify_commands, verify_results, commands, submitted_answer, last_reward |
| tbench2_env | Tbench2State | 7 | task_id, task_path, session_id, terminal_ready, last_command, last_output |
| browsergym_env | BrowserGymState | 7 | benchmark, task_name, task_id, goal, current_url, cum_reward |
| chess_env | ChessState | 5 | **fen**, current_player, move_history |
| connect4_env | Connect4State | 3 | **board**, next_player |
| git_env | GitState | 3 | gitea_ready, workspace_path |
| coding_env | CodeState | 1 | last_exit_code |

Median field count across the 28: **6**.

The pattern is unambiguous: OpenEnv `State` in practice is *episode bookkeeping and
configuration*, not a dump of the world. The only environments that put the world's data in
`State` are the ones whose world is a handful of bytes (`fen`, `board`, a fire `grid`).
Environments with a real backend — `git_env` (a Gitea server), `tbench2_env` (a
Terminal-Bench 2 container), `coding_tools_env`/`terminus_env`/`jupyter_env` (sandboxes),
`browsergym_env` — put an **identifier for the live backend** in state (`sandbox_id`,
`session_id`, `workspace_path`, `gitea_ready`) and nothing about its contents.

`openapp_env` is the closest analogue to a Seahaven-style multi-app world. It has **no
`State` subclass at all**; it puts `app_state: Dict[str, Any] = Field(default_factory=dict,
description="State of all apps")` on the **Observation** instead
(`envs/openapp_env/models.py`), alongside `task_info`. Grading is a per-task
`validate(page, chat_messages) -> Tuple[float, bool, str, Dict[str, Any]]` method that
drives a live Playwright page (`envs/openapp_env/server/openapp_environment.py`).

A useful side note for the trajectory subtopic: `carla_env` keeps `total_tool_calls` and
`tool_call_counts` (a per-tool counter dict) in `State`. That is the only example in the
repo of aggregate tool-call counters carried on state.

## 3. `/state` is a simulation-mode-only route, and the HTTP one is not session-scoped

Two independently important facts from `src/openenv/core/env_server/http_server.py`.

**(a) The route only exists in simulation mode.**

```python
        # Only register /state endpoint in simulation mode
        if mode == ServerMode.SIMULATION:
            get_endpoints.insert(
                0,
                GetEndpointConfig(
                    path="/state",
                    handler=get_state_handler,
                    response_model=State,
                    ...
```

`docs/source/guides/simulation-vs-production.md` frames the two modes:

> - **Simulation mode** is for training, evaluation, and any workflow where the
>   orchestrator controls episode boundaries.
> - **Production mode** is for exposing tools directly to clients over MCP without the
>   training loop controlling `reset()`, `step()`, or `state()`.
> ...
> In practice, simulation mode models **trajectory time** and production mode models
> **service time**.

Simulation-mode routes are `/ws`, `/mcp`, `/reset`, `/step`, `/state`; production mode
removes the simulation control routes. This is directly relevant to Seahaven's plan to
replace `controller_run_sql` / `controller_changes` with `state()`: OpenEnv's own
convention is that *evaluator-only affordances are gated by server mode*, and `state()` is
already on the simulation side of that line. `MCPEnvironment` also supports
`@self.tool(mode="production")` / `@self.tool(mode="simulation")` decorators, so a tool can
be registered in one mode only (`src/openenv/core/env_server/mcp_environment.py`).

**(b) The HTTP `GET /state` handler builds a throwaway environment.**

```python
        def get_state_handler() -> State:
            _env = self._env_factory()
            try:
                return _env.state
            finally:
                _env.close()
```

It constructs a *new* environment from the factory, reads `.state`, and closes it. It does
not reach into any live WebSocket session. The session-scoped path is the WebSocket
message `{"type": "state"}` (`WSStateMessage` / `WSStateResponse` in `types.py`), which is
what the Python client uses (`src/openenv/core/env_client.py`):

```python
    async def _state_async(self) -> StateT:
        message = {"type": "state"}
        response = await self._send_and_receive(message)
        return self._parse_state(response.get("data", {}))
```

So: **the meaningful `state()` in OpenEnv is a WebSocket call on a live session.** If you
call `GET /state` expecting the episode you just ran, you get a fresh env's state. I found
no doc page that warns about this; I am reporting it as a reading of the source.

**(c) The `/schema` endpoint advertises the *base* `State` schema, not the environment's.**

```python
        async def get_schemas() -> SchemaResponse:
            """Return all schemas in one response."""
            return SchemaResponse(
                action=self.action_cls.model_json_schema(),
                observation=self.observation_cls.model_json_schema(),
                state=State.model_json_schema(),
            )
```

`create_app()` takes `action_cls` and `observation_cls` but **no `state_cls`** (see the
`HTTPEnvServer.__init__` signature and `create_app` at the bottom of `http_server.py`). So
the machine-readable schema an external consumer can fetch for state is always
`{episode_id, step_count}` plus `additionalProperties`, no matter how rich the real state
is. A consumer that wants the real shape has to import the environment's Python package.
This is a genuine gap in OpenEnv's state contract as of 0.4.3.dev0, and it is the single
most relevant OpenEnv fact for a project that wants an *external* judge to read state.

## 4. Rewards live inside the environment, and rubrics see `(action, observation)` — not `state`

`docs/source/guides/rewards.md`, first line of the first section:

> The OpenEnv contract is that reward computation stays on the server side, inside
> `Environment.step`.

`docs/source/guides/concepts.md`:

> Rewards are computed **inside the environment**, not by external code.

The mechanism is the `Rubric`, added in v0.2.2 and specified in `rfcs/004-rubrics.md`:

```python
class Rubric:
    def forward(self, action, observation) -> float:
        """Implement this. Return 0.0-1.0."""
        raise NotImplementedError
```

RFC 004, section "Rubrics Live Inside Environments":

> Rubrics are **server-side only**. Each environment defines its rubric in `__init__`, and
> the rubric executes during `step()`.

and:

> **Important**: The `Environment` base class requires a `rubric` attribute. All
> environments must define `self.rubric` in their constructor.

(In the shipped code the attribute is optional: `Environment.__init__(self, transform=None,
rubric: Optional["Rubric"] = None)` and `_apply_rubric` returns `0.0` when it is `None`. So
the RFC's "required" is stronger than the implementation.)

**The signature is the load-bearing fact for Seahaven.** A rubric receives the action just
taken and the observation it produced. It does *not* receive `State`, and nothing in the
base class passes state to it. An env author who wants to grade on world state has to reach
`self._state` from inside a rubric bound to that env instance — which works, but is outside
the abstraction, and means the rubric is not portable and not inspectable by a trainer as a
function of state.

Composition is `nn.Module`-shaped: child rubrics auto-register on attribute assignment;
`named_rubrics()` walks the tree; `last_score` is cached on each node; `register_forward_hook`
lets a trainer harvest every component score. Containers shipped:

| Container | Behavior (verbatim from RFC 004's API table) |
|-----------|------|
| `Sequential(*rubrics)` | Run in order; stop and return 0 if any returns 0 |
| `Gate(rubric, threshold=1.0)` | Return 0 if score < threshold |
| `WeightedSum(rubrics, weights)` | Weighted combination (parallel via `asyncio.gather()` for async) |
| `RubricList(rubrics)` | Container for dynamic lists (no aggregation) |
| `RubricDict(rubrics: Dict[str, Rubric])` | Container for named rubrics with keyed access |
| `LLMJudge(prompt_template, endpoint)` | Call LLM via MCP for evaluation |

### The "many graded tasks in one environment" pattern OpenEnv actually proposes

RFC 004 answers this with `RubricDict` keyed off a field on the **observation**:

```python
  class AtariRubric(Rubric):
      def __init__(self):
          super().__init__()
          self.games = RubricDict({
              "pong": PongRubric(),
              "breakout": BreakoutRubric(),
              "space_invaders": SpaceInvadersRubric(),
          })

      def forward(self, action, obs) -> float:
          return self.games[obs.game_id](action, obs)
```

That is the whole of OpenEnv's story for one environment carrying many graders: a Python
dict of Python rubric objects, dispatched on an observation field, all inside the env
process. There is no declarative grader format, and RFC 004 rejects one explicitly under
"Alternatives Considered":

> **Declarative YAML/JSON schema**
>
> A declarative format would enable non-programmers to define rubrics and allow static
> validation. However, interesting rubrics involve LLM calls, sandboxed execution, database
> queries, and complex branching logic. These don't fit declarative formats well. Python
> code is more expressive and debuggable. The ecosystem (TRL, OpenRLHF, veRL) has converged
> on code-based rewards.

That is a direct, sourced argument *against* Seahaven's declarative-judge direction, worth
answering in the spec rather than ignoring. (The counter-evidence is verifiers' `Criterion`
files and Inspect's packaged scorers — see the sibling docs.)

### Delayed rewards: `TrajectoryRubric`

RFC 004's "Delayed Rewards" section adds `TrajectoryRubric`, shipped at
`src/openenv/core/rubrics/trajectory.py`. It accumulates `(action, observation)` pairs
internally, returns `intermediate_reward` until `observation.done`, then calls
`score_trajectory(trajectory)`. `compute_step_rewards()` does credit assignment;
`ExponentialDiscountingTrajectoryRubric` implements `r_t = gamma^(T-1-t) * R_final`.

Its stated memory model is a constraint worth noting for any "final state in memory" design:

> **Constraint**: Trajectories must not consume GPU memory.
> **Design**: `TrajectoryRubric` stores observations in CPU memory only.
> **Future extension**: If CPU memory becomes problematic at scale, a reference-based
> approach could store `(episode_id, step_index)` tuples referencing external replay buffers.

Still nothing about state. The trajectory a `TrajectoryRubric` accumulates is
`List[Tuple[Any, Observation]]`.

## 5. How a stateful OpenEnv environment actually grades today: run the verifier in the box

`envs/tbench2_env/` (Terminal-Bench 2) is the clearest example of a real stateful backend
in the repo. It does not expose the final state to the trainer at all. Instead
(`envs/tbench2_env/server/tbench2_env_environment.py`):

- The task's canonical `test.sh` runs **inside the container** and its verifier writes a
  verdict to `/logs/verifier/reward.txt`; the environment echoes it out with a marker and
  parses it (`_parse_canonical_reward`, `_REWARD_MARKER`).
- A missing verdict is an *error*, not a zero: `_require_canonical_verdict` raises
  `"canonical harness produced no verdict (reward.txt missing after ...)"` and the
  observation carries `error` with `reward=None`.
- `_withhold_verifier_assets(task_dir)` moves the tests and the answer out of the agent's
  reach before it acts, with the comment: "the literal answer — for RL training both are
  reward-hacking bait."

So the reference pattern for a stateful OpenEnv environment in 2026 is: **grade server-side
against the live backend, and treat an unobtainable verdict as a failure rather than a
score.** Both of those are directly transferable to Seahaven.

`coding_tools_env`, `jupyter_env`, and `terminus_env` do a lighter version: they put
`verify_commands` and `verify_results` (and `last_reward`) *into* `State`, so the
verification output — not the world — is the state a client can read back.

## 6. Environment packaging and versioning

`openenv.yaml` (from `docs/source/guides/concepts.md`):

```yaml
name: my_env
version: 0.1.0
description: My custom environment

client:
  class_name: MyEnvClient
  module: my_env.client

action:
  class_name: MyAction
  module: my_env.models

observation:
  class_name: MyObservation
  module: my_env.models

default_image: my-env:latest
spec_version: 1
```

The manifest declares `action` and `observation` classes but **no `state` class**, and
carries a `spec_version: 1` (read by `src/openenv/auto/_discovery.py` and surfaced via
`AutoEnv`). The manifest's `version` is the environment's own version. There is no
versioning anywhere on a *state payload* — `spec_version` is about the manifest format,
and `version` about the env package.

## 7. Environments on the Hugging Face hub

I could not verify the hub side directly: `huggingface.co` is blocked by this session's
egress proxy (both WebFetch and curl return a 403 from the proxy). What I can say from the
repo and from TRL's docs (read locally from the `huggingface/trl` clone,
`docs/source/openenv.md`):

- Hub environments are HF Spaces that are also pip-installable git repos, e.g.
  `pip install "openenv-echo-env @ git+https://huggingface.co/spaces/openenv/echo_env"`,
  and what you install is "the **environment client** (e.g., `EchoEnv`) that communicates
  with the remote environment server via WebSocket, along with the action/observation models".
  Action and observation models are named; state models are not.
- The Spaces are built from the same `envs/<name>/` trees I censused in §2, so the field
  census above is the best available evidence for what hub environments expose as state.
- TRL's own OpenEnv guide never calls `state()` in any example. Neither does SkyRL's OpenEnv
  integration (`examples/train_integrations/openenv/env.py` in the SkyRL clone literally has
  it commented out: `# Look at the state of the environment` / `# self.env.state()`).

**Bottom line for this section: as of 0.4.3.dev0 I found no OpenEnv environment, in-repo or
documented, that uses `state()` as the input to grading.**
