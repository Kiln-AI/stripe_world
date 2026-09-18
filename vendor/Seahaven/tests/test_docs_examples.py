"""The bundled docs' examples, checked by running them rather than by reading them.

An example that does not work is worse than no example, because an authoring
agent copies it. Nothing on a page is checked by eye, so every fenced block of
every page is checked here, and the fence's language says which check it gets:

* ` ```python ` -- **executed**, in a temporary working directory, and it has to
  exit 0. A block that defines a `test_` function is executed by *pytest*,
  against the reference world; anything else is executed as a script. This is the
  tier for anything a reader could paste into a file and run, and it is the
  default: write a new example this way unless it cannot be one.
* ` ```py ` -- **parsed**, and nothing more. The fragment tier: a signature, a
  decorator on a function whose `World` is three files away, the inside of a
  module. Both spellings highlight as Python everywhere; the difference is
  entirely this file's.
* ` ```sh ` -- every `seahaven ...` command line in it is **parsed with the real
  argument parser**, so a subcommand or an option that does not exist fails.
* Anything else (`sql`, `yaml`, `text`, `json`) is not checked here.

Both Python tiers get one more check, which is the one that catches the mistake
these docs exist to prevent: every `seahaven.a.b` name a block mentions must
resolve on the real package.

Two more checks cover what an example cannot. Every `inst.x`, `ctx.y`, `world.z`
and the rest is resolved against a **live** object of that type, anywhere on a
page, prose and tables included — a member table is where a reference page
hallucinates. And every signature written as a stub (`def one(self, sql: str,
*params: SqlValue) -> ...: ...`) is compared parameter by parameter, kinds and
order included, against the real callable.

An executed example must not import `seahaven.openenv`: the `serve` extra is
optional, and its import costs seconds. Server-side examples are fragments, or
shell.
"""

import annotationlib
import ast
import functools
import importlib
import inspect
import re
import shlex
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import seahaven
from seahaven import cli
from seahaven.cli.docs import docs_path

DOCS = Path(docs_path())

# The repository's own Markdown is checked with the same harness: a command line
# in the README or in the contribution guide is copied as readily as a docs
# page's example, and the guide's whole claim is that its commands are the real
# ones. These are absent when these tests run against an installed wheel rather
# than a checkout, which is not a failure.
REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_PAGES = (REPO_ROOT / "README.md", REPO_ROOT / "CONTRIBUTING.md")

# The fence, with its language, and everything up to the closing fence of the
# same length. Indented fences are not used on these pages and are not matched:
# a four-space-indented block inside a list is a code block to Markdown and a
# quoted fragment to a reader, and running one is not what it is there for.
_FENCE = re.compile(r"^```([A-Za-z0-9_+-]*)[^\n]*\n(.*?)^```$", re.MULTILINE | re.DOTALL)

# A dotted name rooted in `seahaven`. The word boundary and the required dot keep
# `seahaven_xmlrpc` and a bare `import seahaven` out of it.
_SEAHAVEN_NAME = re.compile(r"\bseahaven((?:\.[A-Za-z_][A-Za-z0-9_]*)+)")

# What a command line may carry in front of `seahaven` and still be a documented
# invocation of it: the runner this repository uses, and its own options.
_RUNNER_WORDS = frozenset({"uv", "run", "--no-sync", "--locked", "--frozen"})
_RUNNER_OPTIONS_WITH_VALUES = frozenset({"--project", "--directory", "--python"})


@dataclass(frozen=True)
class Block:
    """One fenced block: where it is, what language it claims, and what is in it."""

    page: str
    line: int
    language: str
    source: str

    def __str__(self) -> str:
        return f"{self.page}:{self.line} ({self.language})"


@functools.cache
def pages() -> tuple[Path, ...]:
    found = sorted(DOCS.rglob("*.md"))
    return (*found, *(page for page in REPO_PAGES if page.is_file()))


@functools.cache
def text_of(page: Path) -> str:
    """Every page is read once, by everything here that reads pages."""
    return page.read_text(encoding="utf-8")


def name_of(page: Path) -> str:
    """`reference/api.md`, not `api.md`: one spelling per page, ids included."""
    try:
        return page.relative_to(DOCS).as_posix()
    except ValueError:
        return page.name


@functools.cache
def blocks_of(page: Path) -> tuple[Block, ...]:
    text = text_of(page)
    return tuple(
        Block(
            page=name_of(page),
            line=text.count("\n", 0, found.start()) + 1,
            language=found.group(1).lower(),
            source=found.group(2),
        )
        for found in _FENCE.finditer(text)
    )


def blocks(*languages: str) -> list[Block]:
    return [block for page in pages() for block in blocks_of(page) if block.language in languages]


def ids(found: list[Block]) -> list[str]:
    return [str(block) for block in found]


PAGE_IDS = [name_of(page) for page in pages()]


RUNNABLE = blocks("python")
FRAGMENTS = blocks("py")
SHELL = blocks("sh")


# Both spellings of the import an executed example must not carry:
# `import seahaven.openenv` and `from seahaven import openenv`.
_IMPORTS_SERVE_EXTRA = re.compile(r"seahaven\.openenv|from\s+seahaven\s+import\s+[^\n]*\bopenenv\b")

# A block that defines tests is a test module, and running it as a script would
# define two functions and exit 0 without having run either.
_DEFINES_TESTS = re.compile(r"^(async )?def test_", re.MULTILINE)

# The world a documented test module is run against. A test that uses the plugin
# needs a world, and the docs' test examples are written against the reference
# world, which is a dev dependency of this repository and is installed.
_REFERENCE_WORLD = "projecttracker:world"

# pytest's own summary line, which is the only thing that distinguishes "every
# test passed" from "nothing ran" -- both of which exit 0.
_PYTEST_PASSED = re.compile(r"\b\d+ passed\b")
_PYTEST_DID_NOT_PASS = re.compile(r"\b\d+ (?:skipped|failed|error|errors|deselected|xfailed)\b")


@pytest.mark.parametrize("block", RUNNABLE, ids=ids(RUNNABLE))
def test_every_python_example_runs(block: Block, tmp_path: Path) -> None:
    """A ` ```python ` block is something a reader can paste and run, so it is run."""
    script = tmp_path / "test_example.py"
    script.write_text(block.source, encoding="utf-8")
    if _DEFINES_TESTS.search(block.source):
        command = [
            sys.executable,
            "-m",
            "pytest",
            script.name,
            "-q",
            "-p",
            "no:cacheprovider",
            "--seahaven-world",
            _REFERENCE_WORLD,
        ]
    else:
        command = [sys.executable, script.name]
    finished = subprocess.run(
        command,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
    )
    report = f"--- stdout ---\n{finished.stdout}\n--- stderr ---\n{finished.stderr}"
    assert finished.returncode == 0, (
        f"the example at {block} exited {finished.returncode}\n{report}"
    )
    if _DEFINES_TESTS.search(block.source):
        # A run that collected nothing, or skipped everything, also exits 0. A
        # documented test module is only documented if it ran and passed.
        assert _PYTEST_PASSED.search(finished.stdout), (
            f"the test module at {block} reported no passing tests\n{report}"
        )
        assert not _PYTEST_DID_NOT_PASS.search(finished.stdout), (
            f"the test module at {block} skipped, errored or failed something\n{report}"
        )


@pytest.mark.parametrize("block", FRAGMENTS, ids=ids(FRAGMENTS))
def test_every_python_fragment_parses(block: Block) -> None:
    """A fragment cannot be run, but a fragment that does not parse is a typo."""
    try:
        ast.parse(block.source)
    except SyntaxError as error:
        pytest.fail(f"the fragment at {block} does not parse: {error}", pytrace=False)


@pytest.mark.parametrize("page", pages(), ids=PAGE_IDS)
def test_every_seahaven_name_a_page_uses_exists(page: Path) -> None:
    """The anti-hallucination check: a name these docs spell must be a real one.

    Over the whole page, not only its fences. A fragment is never executed and a
    sentence is never anything, so this is what stands between a plausible
    `seahaven.helpers.run_sql.showing_sqlite_text` and an authoring agent that
    believes it -- and prose is where a reference page reaches for a name it did
    not have to write out in code.
    """
    text = text_of(page)
    for chain in sorted({found.group(1) for found in _SEAHAVEN_NAME.finditer(text)}):
        parts = chain.lstrip(".").split(".")
        found = _resolve(parts)
        if found is _UNAVAILABLE:
            continue
        assert found is not _MISSING, f"{name_of(page)} names seahaven{chain}, which does not exist"


@pytest.mark.parametrize("block", SHELL, ids=ids(SHELL))
def test_every_documented_command_line_parses(block: Block) -> None:
    """Every `seahaven ...` line in the docs, against the parser the CLI builds."""
    parser = cli.build_parser()
    for command in _seahaven_commands(block.source):
        try:
            parser.parse_args(command)
        except SystemExit:
            pytest.fail(
                f"{block} documents `seahaven {' '.join(command)}`, which the CLI does not accept",
                pytrace=False,
            )


# The receivers the docs write examples and member tables against, and the type
# each one is. `tracker` is `projecttracker.md`'s name for an instance, and
# `call_record` is the reference's name for a `CallRecord`, which `record` cannot
# also be: the two record classes share no field.
_RECEIVER_TYPES = {
    "world": "world",
    "inst": "instance",
    "instance": "instance",
    "tracker": "instance",
    "ctx": "ctx",
    "db": "db",
    "clock": "clock",
    "ids": "ids",
    "call": "call",
    "tool": "tool",
    "record": "record",
    "call_record": "call_record",
    "fixture": "fixture",
}

# A dotted chain rooted in one of those names, anywhere on a page: inside a
# fence, inside backticks, or in a sentence.
# The lookbehind keeps two shapes out: a longer dotted name whose tail happens to
# start with a receiver (`seahaven.world.Handler`), and a TOML table
# (`[tool.seahaven]`).
_MEMBER_CHAIN = re.compile(
    r"(?<![\w.\[])(" + "|".join(sorted(_RECEIVER_TYPES)) + r")((?:\.[A-Za-z_][A-Za-z0-9_]*)+)"
)

# `world.py`, `fixture.yaml`, `errors.py`: a filename is not a member access, and
# the docs name plenty of files after the objects in them.
_FILE_SUFFIXES = (".py", ".md", ".sql", ".yaml", ".yml", ".toml", ".json", ".sqlite", ".txt")


@pytest.fixture(scope="session")
def receivers(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, object]]:
    """One live object per receiver the docs write about.

    Live, and not the classes: `Ids.random` and every other attribute bound in an
    `__init__` is absent from its class, so a `hasattr` on the type would report
    a member the docs get right as missing.
    """
    directory = tmp_path_factory.mktemp("docs_receivers")
    world = seahaven.World(
        name="docs_receivers",
        version="1.0.0",
        schema="CREATE TABLE notes (id TEXT PRIMARY KEY, body TEXT NOT NULL) STRICT;",
        fixtures_dir=directory / "fixtures",
        state_format="seahaven.state/1",
    )

    @world.tool
    def add_note(ctx: seahaven.Ctx, body: str) -> dict[str, object]:
        """Write a note down."""
        note = {"id": ctx.ids.uuid(), "body": body}
        ctx.db.execute("INSERT INTO notes (id, body) VALUES (?, ?)", note["id"], note["body"])
        return note

    with world.instance(now="2026-06-01T09:00:00.000Z") as inst:
        inst.call("add_note", body="a note")
        tool = next(iter(world.tools.values()))
        fixture = inst.freeze("sample", "One note, for the docs' own tests.")
        # `bulk()` is the one public way to a `Ctx` outside a tool call.
        with inst.bulk() as ctx:
            yield {
                "world": world,
                "instance": inst,
                "ctx": ctx,
                "db": ctx.db,
                "clock": ctx.clock,
                "ids": ctx.ids,
                "call": seahaven.Call(name=tool.name, arguments={}, tool=tool),
                "tool": tool,
                "record": inst.change_log()[0],
                "call_record": inst.call_log()[0],
                "fixture": fixture,
            }


@pytest.mark.parametrize("page", pages(), ids=PAGE_IDS)
def test_every_documented_member_exists(page: Path, receivers: dict[str, object]) -> None:
    """`inst.freeze`, `ctx.ids.uuid`, `change.after`: resolved on a real object.

    Over the whole page and not only its code, because a member table is exactly
    where a reference page invents a name nobody notices.
    """
    text = text_of(page)
    for found in _MEMBER_CHAIN.finditer(text):
        chain = found.group(0)
        if chain.endswith(_FILE_SUFFIXES):
            continue
        receiver = receivers[_RECEIVER_TYPES[found.group(1)]]
        for attribute in found.group(2).lstrip(".").split("."):
            if not hasattr(receiver, attribute):
                pytest.fail(
                    f"{name_of(page)} names `{chain}`, and {attribute!r} is not a member of "
                    f"{type(receiver).__name__}",
                    pytrace=False,
                )
            receiver = getattr(receiver, attribute)
            if callable(receiver) and not isinstance(receiver, type):
                # A method, not a container: the rest of the chain is whatever it
                # returns, which this cannot follow without calling it.
                break
            if isinstance(receiver, seahaven.Worlds):
                # `ctx.worlds.payments`: the rest of the chain is a child name the
                # page's own world author chose, and no live object here can
                # answer it -- this world adds nothing. The container also raises
                # `WorldBug` rather than `AttributeError` for a name no
                # `add_world` registered, so a `hasattr` walk would not merely
                # report it missing, it would error the test. `SH209` is what
                # checks a child name, against the world that declares it.
                break


# Where a stub signature in the reference is looked up, in order.
_SIGNATURE_NAMESPACES = (
    "seahaven",
    "seahaven.helpers",
    "seahaven.instances",
    "seahaven.sandbox",
    "seahaven.world",
)

# The page whose `def`s and `class`es are claims about the framework rather than
# example code. Everywhere else a `def` is a world author's own function, and
# looking it up would be asking the wrong question.
_STUB_PAGE = "reference/api.md"

# The two names on that page that live in the `serve` extra. They are resolved
# like any other -- against `seahaven.openenv`, imported here and nowhere else in
# this file -- and skipped only when that import fails, which is what an optional
# extra makes possible. Naming them is not a licence to be wrong about them:
# `test_the_serve_extra_allowlist_is_exactly_what_the_page_uses` keeps the set
# honest in both directions.
_SERVE_EXTRA_NAMES = frozenset({"SeahavenClient", "app"})


def test_every_documented_signature_matches_the_code(receivers: dict[str, object]) -> None:
    """A stub signature in the reference, parameter by parameter, against the real one.

    Names, order and kinds -- so a `/` or a `*` written in the wrong place is a
    failure too. Defaults are not compared: the reference shortens a long default
    to the constant it is, which is a reader's improvement and not a drift.

    A name that does not resolve **fails**: a stub for something that does not
    exist is the hallucination this check is for, and skipping it quietly would
    make the check report success for the one case it was written to catch.
    """
    # A live object per documented type, for the members that exist only on one:
    # `Ids.random` is bound in `__init__`, so the class does not have it and
    # `ctx.ids` does.
    live = {type(receiver): receiver for receiver in receivers.values()}
    checked = 0
    for block in (found for found in FRAGMENTS if found.page == _STUB_PAGE):
        for node in ast.parse(block.source).body:
            if isinstance(node, ast.ClassDef):
                found = _resolve_stub(block, node.name)
                if found is _UNAVAILABLE:
                    continue
                for member in node.body:
                    if isinstance(member, ast.FunctionDef):
                        name = f"{node.name}.{member.name}"
                        target = getattr(found, member.name, None)
                        assert target is not None, f"{block} documents {name}, which does not exist"
                        _compare(block, name, member, target)
                        checked += 1
                    elif isinstance(member, ast.AnnAssign):
                        checked += _check_annotated_member(block, found, node.name, member, live)
            elif isinstance(node, ast.FunctionDef):
                found = _resolve_stub(block, node.name)
                if found is not _UNAVAILABLE:
                    _compare(block, node.name, node, found)
                    checked += 1
            elif isinstance(node, ast.AnnAssign):
                # A module-level constant of whichever module the block is about:
                # `MAX_VALUE_BYTES`, `REFUSALS`. Nothing says which module, so the
                # namespaces are searched exactly as a name is.
                checked += _check_annotated_member(block, None, None, node, live)
    assert checked >= 15, f"only {checked} documented signatures were checked against the code"


def _resolve_stub(block: Block, name: str) -> Any:
    """The real object a stub in the reference stands for. Fails if there is none.

    `_UNAVAILABLE` only for a `serve`-extra name in an environment where the extra
    does not import; everywhere else a name that does not resolve is the
    hallucination this check exists for.
    """
    for module_name in _SIGNATURE_NAMESPACES:
        module = importlib.import_module(module_name)
        if hasattr(module, name):
            return getattr(module, name)
    if name in _SERVE_EXTRA_NAMES:
        try:
            return getattr(importlib.import_module("seahaven.openenv"), name)
        except ImportError:
            return _UNAVAILABLE
        except AttributeError:
            pytest.fail(f"{block} documents {name}, which seahaven.openenv does not have")
    pytest.fail(
        f"{block} documents {name}, which is not in {', '.join(_SIGNATURE_NAMESPACES)}",
        pytrace=False,
    )


def _check_annotated_member(
    block: Block,
    owner: Any,
    owner_name: str | None,
    node: ast.AnnAssign,
    live: dict[type, object],
) -> int:
    """A member the reference writes as a bare annotation (`row_count: int`).

    Its type is not compared -- the reference simplifies one where a reader is
    better off for it -- but the name has to exist, which is the half that can be
    invented. Returns how many were checked, so the caller's floor counts them.
    """
    if not isinstance(node.target, ast.Name):
        return 0
    name = node.target.id
    if owner is None:
        # A module-level constant: `_resolve_stub` fails if there is no such name,
        # and answers `_UNAVAILABLE` only for a `serve`-extra name this
        # environment cannot import. Either way there is nothing left to assert.
        _resolve_stub(block, name)
        return 1
    instance = live.get(owner)
    found = hasattr(owner, name) or (instance is not None and hasattr(instance, name))
    assert found, f"{block} documents {owner_name}.{name}, which is not a member of {owner_name}"
    return 1


def test_the_serve_extra_allowlist_is_exactly_what_the_page_uses() -> None:
    """A name allowlisted but not documented is a skip nobody needs any more."""
    documented = {
        node.name
        for block in FRAGMENTS
        if block.page == _STUB_PAGE
        for node in ast.parse(block.source).body
        if isinstance(node, ast.ClassDef | ast.FunctionDef)
    }
    assert documented >= _SERVE_EXTRA_NAMES, (
        f"{sorted(_SERVE_EXTRA_NAMES - documented)} is allowlisted and not documented"
    )


def _compare(block: Block, name: str, node: ast.FunctionDef, target: Any) -> None:
    """Fail unless the stub's parameters are the real callable's, in order."""
    decorators = {
        decorator.id for decorator in node.decorator_list if isinstance(decorator, ast.Name)
    }
    documented = [
        *[(argument.arg, "positional-only") for argument in node.args.posonlyargs],
        *[(argument.arg, "positional") for argument in node.args.args],
        *([(node.args.vararg.arg, "*args")] if node.args.vararg else []),
        *[(argument.arg, "keyword-only") for argument in node.args.kwonlyargs],
        *([(node.args.kwarg.arg, "**kwargs")] if node.args.kwarg else []),
    ]
    if "classmethod" in decorators or "staticmethod" in decorators:
        # `inspect.signature` of a bound classmethod has already eaten `cls`.
        documented = documented[1:]
    kinds = {
        inspect.Parameter.POSITIONAL_ONLY: "positional-only",
        inspect.Parameter.POSITIONAL_OR_KEYWORD: "positional",
        inspect.Parameter.VAR_POSITIONAL: "*args",
        inspect.Parameter.KEYWORD_ONLY: "keyword-only",
        inspect.Parameter.VAR_KEYWORD: "**kwargs",
    }
    # `Format.STRING` leaves annotations unevaluated. Only names and kinds are
    # compared below, and evaluating would fail on any documented callable
    # annotated with a `TYPE_CHECKING`-only import -- `fixtures.freeze` takes an
    # `Instance`, which `fixtures.py` cannot import at runtime because
    # `instances.py` imports it.
    signature = inspect.signature(target, annotation_format=annotationlib.Format.STRING)
    real = [(parameter.name, kinds[parameter.kind]) for parameter in signature.parameters.values()]
    if isinstance(target, type):
        # A class's signature is its `__init__`'s without `self`, which the stub
        # writes out. Documenting `__init__` is what the reference does, so the
        # stub keeps `self` and the comparison puts it back.
        real = [("self", "positional"), *real]
    assert documented == real, f"{block} documents {name}{tuple(documented)}, not {tuple(real)}"


def test_the_harness_collects_blocks_of_every_tier() -> None:
    """A collector that quietly stopped collecting would be a green run.

    The numbers are floors and not counts: a page is free to gain an example.
    What they pin is that each tier is still being found at all.
    """
    # The counts at the time of writing, less one apiece: a page is free to gain
    # an example or to lose one, and a collector that stopped collecting loses
    # them by the handful. A loose floor -- 10 against 18 -- would absorb eight
    # examples silently, which is not a guard.
    assert len(RUNNABLE) >= 17, f"only {len(RUNNABLE)} executed examples were collected"
    assert len(FRAGMENTS) >= 27, f"only {len(FRAGMENTS)} Python fragments were collected"
    assert len(SHELL) >= 13, f"only {len(SHELL)} shell blocks were collected"
    assert any(_seahaven_commands(block.source) for block in SHELL)
    assert any(_DEFINES_TESTS.search(block.source) for block in RUNNABLE), (
        "no documented test module was collected, so the pytest tier is not being exercised"
    )


def test_no_executed_example_imports_the_serve_extra() -> None:
    """`seahaven.openenv` costs seconds to import and is optional; keep it out.

    Not a style rule: an example that imports it turns a docs test into a test of
    whether the `serve` extra is installed, which is what the fragment tier and
    the shell tier are for.
    """
    offenders = [str(block) for block in RUNNABLE if _IMPORTS_SERVE_EXTRA.search(block.source)]
    assert not offenders, f"{offenders} import the serve extra inside an executed example"


_MISSING = object()
_UNAVAILABLE = object()


def _resolve(parts: list[str]) -> object:
    """`seahaven.a.b` as an object, or why it could not be reached.

    `_UNAVAILABLE` is a submodule whose own imports are not installed -- the
    `serve` extra, most of all -- which is a fact about this environment and not
    about the docs. `_MISSING` is the finding.
    """
    found: object = seahaven
    name = "seahaven"
    for part in parts:
        name = f"{name}.{part}"
        if hasattr(found, part):
            found = getattr(found, part)
            continue
        if not isinstance(found, ModuleType):
            return _MISSING
        try:
            found = importlib.import_module(name)
        except ModuleNotFoundError as error:
            if error.name is not None and not error.name.startswith("seahaven"):
                return _UNAVAILABLE
            return _MISSING
        except ImportError:
            return _UNAVAILABLE
    return found


def _seahaven_commands(source: str) -> list[list[str]]:
    """Every `seahaven ...` invocation in a shell block, as argument lists.

    A line that merely mentions the command somewhere inside it -- `cat
    "$(seahaven docs)/index.md"` -- is not an invocation this can parse and is
    left alone: the command word has to be `seahaven` itself, after the runner
    words this repository writes in front of it.
    """
    commands: list[list[str]] = []
    for line in _logical_lines(source):
        try:
            tokens = shlex.split(line, comments=True)
        except ValueError:  # an unbalanced quote is not a command line
            continue
        rest = _after_runner(tokens)
        if rest is not None:
            commands.append(rest)
    return commands


def _logical_lines(source: str) -> list[str]:
    """The block's lines, with backslash continuations joined and prompts dropped."""
    joined = source.replace("\\\n", " ")
    return [line.removeprefix("$ ").strip() for line in joined.splitlines() if line.strip()]


def _after_runner(tokens: list[str]) -> list[str] | None:
    """The arguments after the `seahaven` command word, or `None` for another command."""
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in _RUNNER_OPTIONS_WITH_VALUES:
            index += 2
            continue
        if token in _RUNNER_WORDS:
            index += 1
            continue
        if token == "seahaven":
            return tokens[index + 1 :]
        return None
    return None
