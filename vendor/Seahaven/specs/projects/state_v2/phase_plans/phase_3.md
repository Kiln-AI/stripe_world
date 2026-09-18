---
status: complete
---

# Phase 3: OpenEnv

## Overview

Phase 2 made `inst.state()` answer the whole `functional_spec.md` §3.1 document in process. This
phase puts that document on the wire: the OpenEnv `state` message answers it, `reset` chooses the
format and hands the episode id over before the instance exists, and `SeahavenState` types every
envelope field so a typed client reads the document rather than a three-field placeholder.

**This phase is the project's gate.** Subclass fields travel over the WebSocket state message
while the HTTP `GET /state` route strips them (huggingface/OpenEnv#1155). Everything above the
document depends on that holding for a document that now carries nested models, a composition
keyed by node path, and an arbitrarily deep `state`. So it is driven over a real socket, to the
typed client and to the stock `GenericEnvClient`, rather than assumed.

## Steps

1. `src/seahaven/openenv/env.py` -- the models. Four `BaseModel`s above `SeahavenState`, each
   field carrying a `description` because the module's existing rule is that every declared field
   publishes one:

   ```python
   class WorldRef(BaseModel):      # name, version
   class NodeRef(BaseModel):       # world, world_version, scope, aliases, schema_hash,
                                   # frozen_world_version
   class FileRef(BaseModel):       # file_sha256
   class FixtureRef(BaseModel):    # id, nodes: dict[str, FileRef]
   ```

2. `src/seahaven/openenv/env.py` -- `SeahavenState`. Every envelope field of FS §3.1 in §3.1's
   order, less `episode_id` which the base carries:

   ```python
   format: str
   seahaven_version: str
   world: WorldRef
   composition: dict[str, NodeRef] | None = None
   fixture: FixtureRef | None = None
   seed: int | None = None
   now: str | None = None
   startup: dict[str, Any] | None = None
   call_count: int
   state: dict[str, Any]
   ```

   `state` stays `dict[str, Any]`: its shape is the format's. `main`'s `world: str`,
   `fixture: str | None` and `composition: list[dict]` are replaced, which is the breaking change
   FS §9 accepts. The base's `extra="allow"` is inherited and kept.

3. `src/seahaven/openenv/env.py` -- `reset`. Gains keyword-only `state_format: str | None = None`,
   named so it can never fall into `**startup_kwargs`. Mints `episode_id or str(uuid.uuid4())`
   before the instance and calls `self.world._instances().create(fixture, seed=..., now=...,
   state_format=..., episode_id=..., startup_kwargs=startup_kwargs)` -- the manager
   `world.instance` itself delegates to, because `episode_id` is deliberately not a parameter of
   `world.instance` (ARCH §6). `self._episode_id` is removed from `__init__` and from `_forget`:
   the instance is the one answer to which episode this is.

4. `src/seahaven/openenv/env.py` -- the `state` property. With an instance,
   `SeahavenState(step_count=self._steps, **instance.state())`. Without one,
   `SeahavenState(step_count=self._steps, **document(self.world, None, pin,
   self.world.resolve_state_format(pin)))` with `pin = self.world.pinned_state_format`. The
   module-level `_composition()` helper and the `dataclasses.asdict` import go; the envelope
   carries the composition now. `step()` and `_listing()` are untouched.

5. `src/seahaven/openenv/__init__.py`: export `FileRef`, `FixtureRef`, `NodeRef` and `WorldRef`
   beside the existing names.

6. `src/seahaven/openenv/client.py`: the class docstring's example reads the document --
   `state.state[...]` and `state.model_dump(exclude={"step_count"})` -- and a paragraph says what
   `state()` now answers and that `reset(state_format=...)` chooses the format.

7. `worlds/projecttracker/tests/test_openenv.py`: the one `state.world`/`state.fixture`
   assertion moves to the nested models and reads the change log the state message carried back.

## Tests

`tests/test_env.py`, the state section rewritten:

- `test_state_before_reset_is_the_worlds_pinned_format_with_no_instance` -- FS §3.5 field by
  field: the envelope answered, `composition`/`fixture`/`episode_id`/`seed`/`now`/`startup` null,
  `call_count` 0, `state` `{"db": {"log": []}}`.
- `test_state_before_reset_runs_a_custom_pinned_formatter_with_no_instance` -- a world pinning
  its own format decides what "no episode yet" looks like; the framework has no default.
- `test_state_after_reset_is_the_instances_document_and_the_step_count` -- `state.model_dump(
  exclude={"step_count"}) == instance.state()`, and `step_count` counts a listing while
  `call_count` does not.
- `test_state_after_reset_carries_the_fixture_and_the_clock` -- `WorldRef`, `FixtureRef` with
  `nodes` keyed by path, `now`, `episode_id`, `seed`, `startup`.
- `test_state_after_reset_carries_the_one_node_a_leaf_world_is` -- `composition` is
  `{"main": NodeRef(...)}`.
- `test_reset_selects_a_state_format` -- `seahaven.state+last_step/1` answers the last call alone.
- `test_an_unknown_state_format_refuses_the_reset_and_leaves_the_session_fresh`.
- `test_the_state_format_never_reaches_a_startup_hook`.
- `test_close_discards_the_log_with_the_instance` and `test_a_second_reset_starts_a_new_log`.
- `test_state_carries_every_node_of_a_composite` and `test_the_composition_in_state_is_json`
  moved to the keyed composition on the composite session.
- `DECLARED_DESCRIPTIONS` gains `SeahavenState`'s new fields and the four new models, so the
  existing "every declared field publishes a description" parametrisation covers them.

`tests/test_client.py`:

- `DOCUMENT_FRAME`, one whole document as a frame, drives `test_parse_state_answers_a_typed_state`
  (nested models, `state` left as the dict the formatter produced) and
  `test_the_document_is_the_state_without_the_step_count`.
- `test_state_answers_none_for_the_fields_a_pre_reset_frame_leaves_null`.
- `test_state_refuses_a_frame_that_does_not_name_a_world`.
- The synchronous end-to-end test reads the nested models and the log.

`tests/test_server.py`, the gate, against a real uvicorn server on a real port:

- `test_the_whole_document_arrives_over_the_websocket` -- one episode with a fixture, a seed, a
  named episode id, a startup keyword and one write, asserted against an `expected_document`
  built field by field from FS §3.1, on the typed client.
- `test_the_stock_client_sees_the_same_document` -- the same episode driven by
  `GenericEnvClient`, with no Seahaven model on the receiving side, answering the identical dict.
- `test_the_document_of_a_composite_arrives_whole` -- the committed `emporium` tree over the
  wire: four nodes in `composition`, and a log record from an added node.
- `test_the_document_over_the_wire_validates_against_the_published_schema` -- the stock client's
  dict against `tests/state_v1.schema.json`.
- `test_reset_selects_a_state_format_over_the_wire` and
  `test_an_unknown_state_format_is_an_error_frame_and_the_session_survives`.
- The two existing `state` assertions in the section 7 flow move to the nested models.
