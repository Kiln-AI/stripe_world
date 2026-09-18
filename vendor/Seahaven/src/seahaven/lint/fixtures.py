"""The fixture rules: SH401 to SH406.

A fixture is a binary artifact with a text sidecar, and every way the two can
drift apart is a way an eval silently starts from state nobody meant. The
framework already refuses a fixture it cannot verify at instance creation; these
rules find the same problems before a commit rather than in a run, and say which
fixture and which field.

The sidecar is checked first and alone: the other rules read fields a sidecar
that does not validate does not have, so a fixture that fails SH401 is reported
once and left. That holds for the whole of SH401, the node-list half included --
a list that names one path or one file twice makes the rules below report the
wrong store, and is left to the one fix that covers all of them.

**A composite fixture is checked node by node.** A version-2 sidecar describes one
file per node (`fixtures.NodeMeta`), and SH402, SH403 and SH405 each run over
every one of them with the node's path in the message: without that, a composite
fixture validated and then had only its root looked at, which is exactly the
silence these rules exist to break. SH404 stays whole-sidecar because there is
one clock per instance and a node has no `now` of its own. SH406 is the one rule
that is new rather than repeated: the sidecar's *shape* -- which nodes there are,
which scope each resolved into, which alias edges reach them -- against the tree
the world resolves to now, in the words `check_composition` would refuse it with
at create.

**SH405 is the journal companions, and not the file mode**, which is how
`components/cli_and_check.md` §3 now gives the rule. It read "state file not
read-only or has `-wal`/`-shm` companions" when this module was written, and the
mode half was dropped from the specification rather than implemented here: git
records only the executable bit, so every committed fixture -- the whole point of
a fixture -- comes back from a clone at `0644`, and the reference world's own
`empty` failed the rule on a fresh checkout. A lint that fires on every fixture
of every world after every clone is one authors learn to ignore, which costs more
than the rule is worth. What the seal was guarding against is the file changing,
and that is SH402, over a hash version control does preserve. `freeze` still
seals the file at `0444`, and that is what stops a live instance writing a
fixture in place; the mode is simply not something `check` can ask about.
"""

import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pydantic
import yaml

from seahaven.clock import Clock
from seahaven.composition import ROOT_PATH, Composition
from seahaven.errors import WorldBug
from seahaven.fixtures import SIDECAR_NAME, STATE_NAME, FixtureMeta, composition_mismatch
from seahaven.lint import Finding, Target
from seahaven.world import World

__all__ = ["run"]

# The journal files a live database has beside it. A fixture is checkpointed and
# vacuumed before it is sealed, so either of these means the file was opened for
# writing after it was frozen -- and whatever the fixture's hash covers, it does
# not cover what is in them.
_COMPANIONS = ("-wal", "-shm")

_REGENERATE = "regenerate it with `seahaven fixture freeze` or `seahaven fixture fork`"


def run(target: Target) -> list[Finding]:
    """SH401 to SH406 over every fixture directory of the world, node by node."""
    findings: list[Finding] = []
    composition = target.composition
    for directory in _fixture_directories(target.world.fixtures_dir):
        sidecar = directory / SIDECAR_NAME
        meta = _read(sidecar)
        if isinstance(meta, Finding):
            findings.append(meta)
            continue
        naming = _node_naming_findings(meta, sidecar)
        if naming:
            # Reported once and left, exactly as a sidecar that does not validate
            # is. A repeated `file` makes SH402 lie outright -- the node's hash
            # is checked against the root's bytes, so a file nothing touched is
            # reported as modified -- and a repeated `path` makes SH403 report
            # one node twice and silences SH406, whose node set the repeat
            # collapses. SH404 and the root's own rules read nothing out of
            # `nodes` and are given up with them: the fix is to regenerate the
            # sidecar, which is the fix for all of them.
            findings += naming
            continue
        findings += _sidecar_findings(meta, target, composition, sidecar)
        findings += _composition_findings(meta, composition, sidecar)
        findings += _state_findings(_subject(meta), meta.file_sha256, directory / STATE_NAME)
        for node in meta.nodes:
            findings += _state_findings(
                _subject(meta, node.path), node.file_sha256, directory / node.file
            )
    return findings


def _subject(meta: FixtureMeta, path: str = ROOT_PATH) -> str:
    """What a finding calls the thing it is about: the fixture, and which node of it."""
    if path == ROOT_PATH:
        return f"fixture {meta.id!r}"
    return f"fixture {meta.id!r} node {path!r}"


def _fixture_directories(fixtures_dir: Path) -> list[Path]:
    """Every fixture directory, in id order.

    Dot-directories are the framework's own -- `.pending-<id>` is a freeze in
    flight -- and a world with no fixtures directory at all simply has no
    fixtures, which is not a finding: a world may only ever make blank instances.
    """
    try:
        children = sorted(fixtures_dir.iterdir())
    except FileNotFoundError, NotADirectoryError:
        return []
    return [child for child in children if child.is_dir() and not child.name.startswith(".")]


def _read(sidecar: Path) -> FixtureMeta | Finding:
    """The sidecar, or the one SH401 that says why it is not one."""
    try:
        data: Any = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    except OSError as error:
        return _sh401(sidecar, f"cannot be read: {error}")
    except yaml.YAMLError as error:
        return _sh401(sidecar, f"is not valid YAML: {error}")
    if not isinstance(data, dict):
        return _sh401(sidecar, "is not a mapping")
    try:
        return FixtureMeta.model_validate(data)
    except pydantic.ValidationError as error:
        return _sh401(sidecar, "; ".join(_violations(error)))


def _violations(error: pydantic.ValidationError) -> list[str]:
    """Pydantic's errors, one per line of the message, field first.

    Every one of them, not the first: a sidecar hand-edited into the wrong shape
    usually has several, and a `format_version` this Seahaven does not write
    shows up here as the field it is -- which is the whole of what a reader needs
    to know that the fixture came from another version of Seahaven.
    """
    return [
        f"{'.'.join(str(part) for part in violation['loc']) or 'sidecar'}: {violation['msg']}"
        for violation in error.errors()
    ]


def _node_naming_findings(meta: FixtureMeta, sidecar: Path) -> list[Finding]:
    """SH401, for the two things a node list can say that pydantic cannot refuse.

    Each `NodeMeta` is valid on its own and the list is still not: two entries at
    one path describe one store twice, and two entries naming one file describe
    two stores with one. Both reach `Fixture.file_of` and `verify` as a fixture
    that loads something other than what it says it does.

    A finding here stops the fixture, which is what `run` does with it: every rule
    below reads this list as a set of distinct nodes.
    """
    repeated = [
        f"more than one node is listed at path {path!r}"
        for path in _repeated(node.path for node in meta.nodes)
    ] + [
        f"more than one node's state is the file {file!r}"
        for file in _repeated([STATE_NAME, *(node.file for node in meta.nodes)])
    ]
    return [
        Finding(code="SH401", severity="error", path=sidecar, message=message, fix=_REGENERATE)
        for message in repeated
    ]


def _repeated(values: Iterable[str]) -> list[str]:
    """Every value that appears more than once, in first-seen order."""
    seen: dict[str, int] = {}
    for value in values:
        seen[value] = seen.get(value, 0) + 1
    return [value for value, count in seen.items() if count > 1]


def _sidecar_findings(
    meta: FixtureMeta, target: Target, composition: Composition | None, sidecar: Path
) -> list[Finding]:
    findings: list[Finding] = []
    if not _is_canonical(meta.now):
        findings.append(
            Finding(
                code="SH404",
                severity="error",
                path=sidecar,
                message=(
                    f"fixture {meta.id!r} has now={meta.now!r}, which is not a canonical timestamp"
                ),
                fix="canonical is 2026-06-01T09:00:00.000Z: UTC, milliseconds, trailing Z",
            )
        )
    for path, world, frozen in _declared_schemas(meta, target, composition):
        if frozen != world.schema_hash:
            findings.append(
                Finding(
                    code="SH403",
                    severity="error",
                    path=sidecar,
                    message=(
                        f"{_subject(meta, path)} was frozen from a different schema than world "
                        f"{world.name!r} declares"
                    ),
                    fix=_REGENERATE,
                )
            )
    return findings


def _declared_schemas(
    meta: FixtureMeta, target: Target, composition: Composition | None
) -> list[tuple[str, World, str]]:
    """Each node the sidecar describes, the world running it, and the hash it recorded.

    The root is asked of the world itself, so a leaf world's fixtures are checked
    exactly as they always were and with no tree at all. An added node is asked of
    the composition, and one the composition does not have is left out: that is a
    node set that disagrees, which is SH406's to report and not a schema drift.
    """
    declared: list[tuple[str, World, str]] = [(ROOT_PATH, target.world, meta.schema_hash)]
    if composition is None:
        return declared
    live = {node.path: node for node in composition.nodes}
    declared += [
        (node.path, live[node.path].world, node.schema_hash)
        for node in meta.nodes
        if node.path in live
    ]
    return declared


def _composition_findings(
    meta: FixtureMeta, composition: Composition | None, sidecar: Path
) -> list[Finding]:
    """SH406: the sidecar's shape against the tree the world resolves to now."""
    if composition is None:
        return []
    mismatch = composition_mismatch(meta, composition)
    if mismatch is None:
        return []
    return [
        Finding(
            code="SH406",
            severity="error",
            path=sidecar,
            message=mismatch,
            fix=_REGENERATE,
        )
    ]


def _state_findings(subject: str, expected: str, state: Path) -> list[Finding]:
    findings: list[Finding] = []
    # Judged before `is_file`, which follows a link, and reported instead of the
    # hash: `_sha256` would open through the link and agree with a sidecar whose
    # `file_sha256` is the target's, so a fixture the runtime refuses
    # (`fixtures._verify_file`) would pass here. Same rule, same code -- a link
    # is a state file that is not the file the sidecar describes.
    if state.is_symlink():
        return [
            Finding(
                code="SH402",
                severity="error",
                path=state,
                message=(
                    f"{subject} has a {state.name} that is a symbolic link, and a fixture's "
                    f"files are the files in its own directory"
                ),
                fix=_REGENERATE,
            )
        ]
    if not state.is_file():
        return [
            Finding(
                code="SH402",
                severity="error",
                path=state,
                message=f"{subject} has no {state.name}",
                fix=_REGENERATE,
            )
        ]
    if _sha256(state) != expected:
        findings.append(
            Finding(
                code="SH402",
                severity="error",
                path=state,
                message=(
                    f"{subject} does not match its sidecar's file_sha256; it has been "
                    f"modified since it was frozen"
                ),
                fix="fixtures are immutable: fork it, change the fork, and freeze that",
            )
        )
    for suffix in _COMPANIONS:
        companion = state.with_name(state.name + suffix)
        if companion.exists():
            findings.append(
                Finding(
                    code="SH405",
                    severity="error",
                    path=companion,
                    message=(
                        f"{subject} has a {suffix} file beside its state, so it was "
                        f"opened for writing after it was frozen"
                    ),
                    fix=_REGENERATE,
                )
            )
    return findings


def _is_canonical(now: str) -> bool:
    """Whether a timestamp is the one text every door of a world writes.

    Asked by round-tripping it through the clock rather than by a pattern: the
    clock is what produces canonical text everywhere else, so what it renders is
    the definition and a second one here could disagree with it.
    """
    try:
        return Clock.from_iso(now).iso() == now
    except WorldBug:
        return False


def _sh401(sidecar: Path, why: str) -> Finding:
    return Finding(
        code="SH401",
        severity="error",
        path=sidecar,
        message=f"{SIDECAR_NAME} {why}",
        fix=_REGENERATE,
    )


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()
