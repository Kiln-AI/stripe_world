---
status: complete
---

# Backlog: World Composition

Items found while building composition that are out of scope for the phase that found them. Each is
closed or dismissed through the standard phase flow.

## Open

_None. Every item was decided in phase 8._

## Closed

- **`AliasPath` makes the tool list disagree with validation.** — *dismissed.*
  `Field(validation_alias=AliasPath("p", 0))` publishes the *parameter's own* name in the tool
  schema but validates only the nested shape, so the name the tool list advertises is one
  validation rejects — by reference, by name, and for an agent's own call alike. That is exactly
  what `tool.py`'s module docstring says cannot happen ("the tool list an agent reads and the
  validation a call passes cannot drift apart").

  Pre-existing, and not introduced by composition: `docs/authoring.md` documents only
  `Field(alias=...)`, and phase 3's `Tool.arguments` reads the published schema, so the typed call
  path is exactly as wrong as the by-name path and no more. Closing it means refusing `AliasPath`
  in `tool._field`, which is a change to the registration surface that no phase of this project
  names.

  **Dismissed.** Reaching this needs an obscure pydantic feature the docs never mention, and it
  predates composition — genuinely rare on both counts. The cost of refusing it at registration is
  not worth spending now. `AliasPath` stays unsupported and undocumented.

- **Both fixture readers follow symlinks.** — *closed.*
  Planting a node's state file in a fixture directory as a symlink to a database outside it, with
  `file_sha256` set to the *target's* digest, makes `verify()` hash the target and `_copy_fixture`
  copy it into the new instance: the instance comes up on the outside file's contents, and every
  check passes.

  Pre-existing, and not introduced by composition: `fixture.state_path` has had this property since
  `format_version: 1`, so closing it -- a `path.is_symlink()` refusal in `fixtures._verify_file` --
  changes pre-phase-4 behaviour for every world and not only composite ones. Phase 4's
  `NodeMeta.file` validator closes the *naming* half of the same threat (a `file` that spells its
  way out of the directory) and deliberately not this half.

  What makes it worth deciding rather than leaving: `instances._open_child` hardens the
  working-directory side against exactly this attack -- `O_NOFOLLOW`, an owner check, and
  `mkdirat` on a descriptor rather than a path -- while the fixtures side does not, so the two
  sides of one framework disagree about one threat.

  **Closed** by `9c1c0a8`, "Refuse a symlink where a fixture's state file should be". Option A
  (refuse a symlink outright) was chosen over option B (resolve it and require containment inside
  the fixture directory): a fixture that points outside itself is a fixture plus an invisible
  dependency. B's use case — sharing one large database between two fixtures on the same disk — is
  real but unasked for, and can be added if anyone asks for it. `seahaven check` had the same hole
  and now reports it under the existing SH402, so the linter and the runtime agree.

- **`world._check_name` and `fixtures.check_id` accept a Windows drive-relative name.** — *closed.*
  `C:x` passes both on Linux: `_check_name` refuses both platforms' separators but not a drive
  letter, and `check_id` refuses only the running platform's (`name != Path(name).name`). A world
  or a fixture id authored on Linux under such a name is refused the moment the same artifact is
  read on Windows, which is the failure a portable artifact format exists to prevent.

  Pre-existing and in framework code (`world.py`, `fixtures.py`), not composition's. Found while
  validating `NodeMeta.file`, whose new check is strictly stricter than either of these -- it
  refuses a name that is not `PurePosixPath(value).name` *and* not `PureWindowsPath(value).name` --
  so the three name rules the framework applies to durable artifacts now disagree with each other.

  **Closed** by `75e5d97`, "Lock down the name rules for worlds and fixture ids", which went
  considerably further than the item asked. Rather than close the drive-letter gap alone, the whole
  charset was locked down now, on the reasoning that a cross-platform name problem is far worse to
  fix once names are already in other people's fixtures. The three rules now read from one.

- **SH206 is blind to a tool a block list hides.** — *dismissed.*
  `lint/code._renamed_by_node` collects only the entries whose contributed name differs from the
  tool's own, so a world added with no `tool_prefix` and a `tool_block_list` is skipped entirely. A
  description reading "Call `beta` first" earns no finding even though `beta` is reachable under no
  name at all — arguably worse than the renamed case the rule does catch, because there is no name
  the agent could have been told instead.

  Architecture §14 scopes the rule to prefixes ("a *prefixed* world's tool descriptions"), so this is
  a gap in the rule rather than a deviation from it, and widening it is a change to what SH206 means.
  Whether the wider rule is wanted is the decision: the block-list case has no fix but "accept it or
  unhide the tool", which is a different sentence from SH206's own.

  **Dismissed.** §14 scopes SH206 to prefixed worlds, and widening it changes what the rule means.
  The gap is not worth that, given the block-list case has no fix but "accept it or unhide the
  tool" — a different sentence from the one SH206 exists to say.

- **`seahaven check` has no home for what an author wants to know and is not a finding.** —
  *dismissed.*
  The node count architecture §15 originally promised is the case that raised it, and a composite
  world has others: which nodes there are, which scopes they resolved into, which routes alias which.
  §15.1 records why `check` itself is the wrong place — it prints findings and nothing else, so a
  clean world prints nothing, and neither an informational line nor a warning on every correct
  composite world is worth what it costs.

  A command whose job is to describe a world rather than to find fault with one — `seahaven world
  info`, say — would be that home, and nothing in this project proposes one. `len(world.composition()
  .nodes)` and `Instance.composition()` mean nothing here is unknowable in process; what has no home
  is volunteering it.

  **Dismissed** from this backlog as not a defect. It is a feature request that landed here because a
  spec sentence promised something the code did not do, and that sentence has been corrected
  (§15.1). If a `seahaven world info`-shaped command is wanted, it deserves its own spec rather than
  a slot in a cleanup phase.

- **Two member tables in the docs are verified by nothing.** — *dismissed.*
  `tests/test_docs_examples.py`'s `_RECEIVER_TYPES` has no `handle` or `report` entry, so the two
  largest tables phase 6 added -- `handle.call`/`db`/`state`/`worlds` (`reference/api.md:178-182`)
  and `report.path`/`world`/`world_version`/`scope`/`aliases`/`schema_hash`/`frozen_world_version`
  (`:302-307`) -- resolve against no live object. All thirteen were checked by hand and are correct
  today, so this is drift risk rather than a present defect: the check that catches an invented
  member everywhere else on that page is not asked here.

  `report` is the cheap half to close, since every documented mention is a genuine `NodeReport`
  member. `handle` is not: `composition.md` writes `ctx.worlds.payments.db.execute(...)`, and a
  class-level walk trips over `db` being a `property` object, so closing that half needs a live
  composite in the `receivers` fixture plus a break after the first handle member.

  **Dismissed.** All thirteen members were verified by hand and are correct. This is drift insurance
  on documentation rather than a defect, and nothing is worth spending here.
