"""The composition rules: a tree that does not seal, a declaration, a shared node.

Every rule here reads a `World` object and the tree it resolves to, so every test
runs over a committed world under `tests/worlds/` rather than over source written
into `tmp_path`: `add_world` takes the object a package exports, and there is no
honest way to write one of these trees without writing the packages.

`bazaar` and `unsealed` are the positive fixtures and `emporium` and `shop` the
negative ones -- `tests/worlds/README.md` says which is which and why.
"""

import copy

import pytest

from seahaven.cli import discover
from seahaven.lint import Finding, Target
from seahaven.lint import world as world_lint
from tests.conftest import WORLDS

pytestmark = pytest.mark.usefixtures("isolated_imports")


def target(name: str) -> Target:
    """A lint target over one of the committed worlds, found the way the CLI finds it."""
    found = discover(None, WORLDS / name)
    return Target(world=found.world, package=found.package, imported=found.imported)


def findings(name: str, code: str) -> list[Finding]:
    return [finding for finding in world_lint.run(target(name)) if finding.code == code]


# --- SH504: the seal ---------------------------------------------------------


def test_a_tree_that_does_not_seal_is_sh504() -> None:
    """The refusal's own words, which name the add_world and what it should have said."""
    (finding,) = findings("unsealed", "SH504")
    assert finding.severity == "error"
    assert "add_world(ledger, name='ledger')" in finding.message
    assert "'post_entrie'" in finding.message
    assert finding.path.name == "world.py"
    assert "add_world" in finding.fix


def test_a_world_that_does_not_seal_still_gets_the_rules_that_need_no_tree() -> None:
    """`check` is a report, not a stop: the tree failing must not cost every other rule.

    `unsealed` carries one undescribed tool for exactly this, so the assertion is
    that both findings arrive rather than that nothing raised.
    """
    from seahaven import lint

    codes = {finding.code for finding in lint.run_all(target("unsealed"))}
    assert codes == {"SH504", "SH205"}


def test_a_world_that_does_not_seal_reports_nothing_that_needs_the_tree() -> None:
    """One defect, one code: a consequence of the failure SH504 named is not a second finding."""
    assert findings("unsealed", "SH207") == []
    assert target("unsealed").composition is None


def unsealable_bazaar() -> Target:
    """`bazaar`, declaration and all, with one edge that stops it resolving.

    A copy of the `World` and not the module's own object. `isolated_imports`
    happens to purge `bazaar` today -- nothing imports it at collection time, so
    every test discovers it afresh -- but node identity is object identity, and
    the moment a test module imports it directly, as the `emporium` tests import
    theirs, an edge added to the real one would outlive this test. The copy is
    what keeps that from being a trap.

    The new edge is named for nothing `BazaarWorlds` annotates, which is why
    SH502 says exactly what it says about the sealed world and SH503 says it
    once more, for `stalls`.
    """
    found = target("bazaar")
    broken = copy.copy(found.world)
    broken.add_world(broken.added_worlds[0].world, name="stalls", tool_allow_list=["nope"])
    return Target(world=broken, package=found.package, imported=found.imported)


def test_exactly_four_rules_ask_for_a_tree_and_the_rest_report_without_one() -> None:
    """The sentence `architecture.md` section 14 and SH504's reference page both make.

    It has been written wrong twice, in both directions, because nothing held it:
    `unsealed` declares no `Worlds` subclass and has no fixtures, so the codes it
    reports are silent about whether SH502 and SH503 need a tree. `bazaar`
    declares one and is wrong in four other ways, so an unsealable copy of it
    answers the whole question at once -- SH502 and SH503 report, the code rules
    that read no tree report, and SH206 and SH207 are the ones that go quiet.

    The other two of the four are asserted where their own fixtures are:
    `test_lint_fixtures.py`'s
    `test_a_world_that_does_not_seal_reports_neither_rule_that_reads_the_tree`
    covers SH406 and the per-node half of SH403.
    """
    from seahaven import lint

    unsealable = unsealable_bazaar()
    assert unsealable.composition is None
    assert sorted(finding.code for finding in lint.run_all(unsealable)) == [
        "SH208",
        "SH209",
        "SH502",
        "SH503",
        "SH503",
        "SH504",
    ]


def test_the_same_world_reports_both_tree_rules_once_it_seals() -> None:
    """Not vacuous: SH206 and SH207 are exactly what the unsealable copy loses."""
    from seahaven import lint

    sealed = target("bazaar")
    assert sealed.composition is not None
    assert sorted({finding.code for finding in lint.run_all(sealed)}) == [
        "SH206",
        "SH207",
        "SH208",
        "SH209",
        "SH502",
        "SH503",
    ]


def test_the_committed_tree_seals() -> None:
    assert findings("emporium", "SH504") == []


# --- SH502, SH503: the `Worlds` declaration ----------------------------------


def test_an_annotated_name_no_add_world_registered_is_sh502() -> None:
    (finding,) = findings("bazaar", "SH502")
    assert finding.severity == "error"
    assert "'market'" in finding.message
    assert finding.path.name == "worlds.py"
    assert "ledger" in finding.fix and "books" in finding.fix


def test_sh502_points_at_the_annotation_and_not_at_the_class() -> None:
    """The edit is one line, and a reader sent to the class has to find it again."""
    (finding,) = findings("bazaar", "SH502")
    assert finding.line is not None
    line = finding.path.read_text(encoding="utf-8").splitlines()[finding.line - 1]
    assert line.strip().startswith("market:")


def test_a_registered_name_the_declaration_forgets_is_sh503() -> None:
    (finding,) = findings("bazaar", "SH503")
    assert finding.severity == "warning"
    assert "'books'" in finding.message
    assert "books: seahaven.WorldHandle" in finding.fix


def test_a_private_or_dunder_annotation_is_not_a_declared_child() -> None:
    """No child can be spelled with a leading underscore, so none of these claims to be one.

    `BazaarWorlds` carries `__slots__` and a private field of its own, which reach
    ordinary attribute lookup rather than `Worlds.__getattr__` -- which is why
    that method lets them. `market` is the one wrong annotation, and an error
    apiece for the other two would make a subclass unable to have a field.
    """
    reported = [finding.message for finding in findings("bazaar", "SH502")]
    assert reported == [
        "BazaarWorlds annotates 'market', which world 'bazaar' does not add",
    ]


def test_a_complete_declaration_is_neither() -> None:
    """`shop` annotates the one world it adds, and nothing else."""
    assert findings("shop", "SH502") == []
    assert findings("shop", "SH503") == []


def test_a_world_that_declares_no_class_is_never_sh503() -> None:
    """Declaring one is optional, so its absence cannot be a finding."""
    assert findings("emporium", "SH502") == []
    assert findings("emporium", "SH503") == []


def test_a_worlds_subclass_outside_the_package_is_not_this_worlds_declaration() -> None:
    """`tests/typed_calls_fixture.py` declares `EmporiumWorlds` and is nobody's world."""
    import tests.typed_calls_fixture  # noqa: F401

    assert findings("emporium", "SH502") == []


# --- SH207: one tool, two names ----------------------------------------------


def test_one_tool_of_a_shared_node_contributed_twice_is_sh207() -> None:
    """Two routes to one store, each with its own prefix: two names, one account."""
    found = findings("bazaar", "SH207")
    assert {finding.severity for finding in found} == {"warning"}
    assert len(found) == 2
    message = " ".join(finding.message for finding in found)
    assert "'post_entry'" in message and "'list_entries'" in message
    assert "books_post_entry, ledger_post_entry" in message


def test_two_nodes_of_one_world_are_not_sh207() -> None:
    """`emporium`'s two payments accounts are two stores, so two names is the truth."""
    assert findings("emporium", "SH207") == []
