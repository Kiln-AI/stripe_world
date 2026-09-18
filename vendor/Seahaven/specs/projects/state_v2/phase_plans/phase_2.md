---
status: complete
---

# Phase 2: Formats and the world pin

## Overview

Phase 1 captured the change log and the call log. This phase is the surface over them: the state
document, the formats that fill its `state` key, and the pin that decides which format a world's
instances answer in. `inst.state()` answers the whole `functional_spec.md` §3.1 document in
process -- a framework-owned envelope carrying the composition and the fixture's files keyed by
node path, plus `state` from a formatter -- and every `World(...)` in the repository names the
format its instances save in.

Nothing over OpenEnv moves here; that is phase 3, and it reads `state.document` for the
no-instance case this phase makes work.

## Steps

1. `src/seahaven/state.py` (new)
   - `type Formatter = Callable[["World", "Instance | None"], dict[str, Any]]`; the three names
     `SEAHAVEN_STATE_V1`, `SEAHAVEN_STATE_LAST_STEP_V1`, `SEAHAVEN_STATE_CALLS_V1`;
     `BUILTIN_PREFIX = "seahaven."`; `_NAME = re.compile(r"^[A-Za-z0-9_.+-]+/[1-9][0-9]*$")`.
   - `check_format_name(name) -> None`: a `WorldBug` naming the rule.
   - `envelope(world, instance, format) -> dict[str, Any]`: FS §3.1's ten fields in order, with
     `composition` from `_composition(instance)` (one entry per `NodeReport`, keyed by path, root
     first), `fixture` as `{"id": ..., "nodes": {path: {"file_sha256": ...}}}`, `startup`
     deep-copied, and the instance-dependent fields `None` (`call_count` 0) with no instance.
   - `document(world, instance, format, formatter) -> dict[str, Any]`: runs the formatter,
     refuses anything but a dict with a `WorldBug`, and joins the envelope to `state`.
   - `state_v1`, `state_last_step_v1` (records whose `i` is `call_count - 1`), `state_calls_v1`
     (`state_v1` plus `calls`); `BUILTIN_FORMATS` as a `MappingProxyType` over the three.

2. `src/seahaven/world.py`
   - `World.__init__(..., *, state_format: str | None = None, ...)` -> `self.pinned_state_format`,
     through `_checked_state_format(name, state_format)`: `None` is a `WorldBug` naming the
     built-ins, the name is checked, and a `seahaven.` name that is not built in is refused.
   - `world.state_format(name)`: the registration verb, a decorator factory. Checks the name,
     refuses the `seahaven.` prefix, and on application refuses a non-callable and a duplicate.
     No `bump()`, with a comment beside the other verbs' `bump()` calls saying why.
   - `world.resolve_state_format(name) -> Formatter`: built-ins, then `self._state_formats`,
     else a `WorldBug` naming both sets.
   - `_state_formats` is the fifth registry; `__copy__` copies it. `RESET_ARGUMENTS` gains
     `state_format`. `world.instance(..., state_format: str | None = None, ...)` passes it on.

3. `src/seahaven/instances.py`
   - `Instance.__init__` gains `state_format: str`, `formatter: Formatter`, `episode_id: str`,
     `caller_seed: int | None`, `fixture_files: dict[str, str] | None`, `startup: dict[str, Any]`,
     and `self._formatting: int | None`.
   - `Instance.state(format: str | None = None) -> dict[str, Any]`: under `_held()`, refuses a
     transaction on any node, resolves the format (the instance's, or another of the root's),
     saves and restores `_formatting` around `state.document(...)`.
   - `_refuse_if_formatting()`, called at the top of `call()` (ahead of the gate) and in `_bulk`.
   - `InstanceManager.create(..., state_format=None, episode_id=None)`: after
     `_check_startup_kwargs`, serialise each startup keyword (a `WorldBug` naming the keyword),
     then resolve the formatter on the root, then the fixture checks. `fixture_files` from the
     sidecar: `{ROOT_PATH: meta.file_sha256} | {node.path: node.file_sha256 for node in
     meta.nodes}`, `None` for a blank instance.

4. `src/seahaven/changes.py`: `_copied_arguments(arguments)` -- the deep copy with the shallow
   fallback that phase 1 put in `instances._recorded_arguments` -- is shared by the capture and by
   `CallRecord.to_dict()`, so that a call the capture deliberately tolerated cannot make
   `inst.state(format="seahaven.state+calls/1")` raise a bare `TypeError` out of `copy`. The
   document is then not JSON-able, which is exactly what FS §4.2 says of such a call.

5. `src/seahaven/cli/templates/base/src/PACKAGE/world.py.tmpl`: `state_format="seahaven.state/1"`
   with the two-line comment ARCH §10 asks for.

6. The sweep: `state_format="seahaven.state/1"` on every `World(...)` in `worlds/`,
   `extensions/`, `tests/` (the committed worlds under `tests/worlds/` included), `README.md` and
   `src/seahaven/docs/`, found by a paren-matching scan; `options.setdefault("state_format",
   "seahaven.state/1")` in `tests/conftest.py`'s `build_world` and `composable_world` and in the
   `make_world`/`rowed` helpers of `tests/test_add_world.py`, `tests/test_composition.py` and
   `tests/test_typed_call.py`. `tests/worlds/payments` pins `seahaven.state+calls/1` and
   registers `payments.state/1`, which is what the root-decides cases on `emporium` are asserted
   against.

7. Documentation, only as far as this phase makes a page wrong: `reference/api.md`'s `World`
   stub and member table, and `authoring.md`'s three statements of the reserved reset keywords.
   The rest is phase 5's.

8. `pyproject.toml`: `jsonschema>=4` joins the `dev` group, which is what asserts a document
   against `tests/state_v1.schema.json`.

## Tests

- `tests/state_v1.schema.json` (new): the published shape of a built-in document,
  `additionalProperties: false` throughout, covering the envelope, a log record and a call entry.
- `tests/test_state.py` (new):
  - *The envelope.* `test_the_envelope_reports_the_world_and_the_format`,
    `test_a_blank_instance_has_no_fixture`, `test_a_fixture_instance_reports_its_files_by_path`,
    `test_the_composition_is_keyed_by_path_root_first` (one node and `emporium`),
    `test_startup_keywords_are_reported_and_are_empty_when_none_were_given`,
    `test_the_episode_id_is_the_instance_id_in_process`, `test_the_seed_is_the_one_the_caller_gave`,
    `test_the_envelope_is_identical_under_a_custom_format`.
  - *The built-ins.* `test_state_v1_carries_the_whole_log`,
    `test_last_step_carries_only_the_last_calls_records`,
    `test_the_last_step_documents_of_an_episode_concatenate_into_the_whole_log`,
    `test_last_step_is_empty_before_any_call`, `test_a_bulk_write_is_never_a_last_step_record`,
    `test_calls_carries_the_call_log_indexed_by_the_records_ordinal`,
    `test_each_built_in_answers_without_an_instance`,
    `test_a_document_validates_against_the_published_schema` (with a negative case),
    `test_an_argument_that_cannot_be_deep_copied_still_reaches_the_calls_document`.
  - *Custom formats.* `test_a_custom_format_is_selected_by_the_instance_keyword`,
    `test_a_custom_formatter_may_read_a_built_in_and_edit_it`,
    `test_a_seahaven_name_is_refused`, `test_a_duplicate_registration_is_refused`,
    `test_a_formatter_that_is_not_callable_is_refused`, `test_a_bad_format_name_is_refused`,
    `test_a_formatter_that_returns_something_else_is_a_world_bug`.
  - *Choosing.* `test_a_world_must_pin_a_state_format`,
    `test_an_unknown_seahaven_name_is_refused_at_the_world`,
    `test_an_unregistered_custom_pin_is_refused_at_the_first_instance`,
    `test_a_custom_pin_registered_after_the_world_line_works`,
    `test_the_roots_pin_governs_a_tree`, `test_a_leafs_formatter_is_not_resolvable_from_the_root`,
    `test_a_leaf_used_as_a_root_uses_its_own_pin`,
    `test_a_refused_format_leaves_no_instance_behind`.
  - *Ownership and guards.* `test_editing_a_document_does_not_edit_the_instance` (startup, nested
    startup, a record's `key` and `after`), `test_a_formatter_that_calls_a_tool_is_refused`,
    `test_a_formatter_that_writes_in_bulk_is_refused`,
    `test_a_call_from_another_thread_waits_while_a_formatter_runs`,
    `test_state_inside_bulk_is_refused`, `test_state_inside_a_tool_call_is_refused`,
    `test_state_after_destroy_is_refused`, `test_state_does_no_database_work`.
- `tests/test_world.py`: `test_a_startup_hook_may_not_name_state_format`,
  `test_a_copy_carries_the_state_formats`, `test_registering_a_state_format_does_not_reseal`.
- `tests/test_changes.py`: `test_a_call_record_copies_an_argument_it_cannot_deep_copy`.
- `tests/test_cli_new.py`: the generated `world.py` carries the pin (added to the existing
  scaffold assertions).
- `tests/test_pytest_plugin.py`: `test_the_marker_passes_a_state_format_to_the_instance`.
