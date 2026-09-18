"""The bundled docs: the layout exists, and the lint reference is not stale.

The pages are hand-written and nothing generates them, so there is no drift test
to write for their prose -- `components/pytest_and_docs.md` §2 says so. What can
go wrong silently is checked here: a page named by the layout (and by `index.md`,
and by a scaffolded world's `AGENTS.md`) that is not there; the lint reference
falling behind the rules, which is the one page whose content is a list of things
that exist elsewhere in the code; and a page still naming either of the two
surfaces the state-format project removed from the docs, which no fence check
catches in prose.
"""

import re
from pathlib import Path

import pytest

import seahaven
from seahaven.cli.docs import docs_path

# The layout, as `index.md` gives it. `components/pytest_and_docs.md` §2 wrote
# down an earlier version of this list; the pages were since rewritten, with
# `fixtures.md` renamed and `serving.md` and `openenv.md` merged.
PAGES = (
    "index.md",
    "concepts.md",
    "authoring.md",
    "composition.md",
    "db_schema_and_fixtures.md",
    "testing.md",
    "state.md",
    "serving_and_openenv.md",
    "extensions.md",
    "projecttracker.md",
    "reference/api.md",
    "reference/lints.md",
    "reference/cli.md",
)

LINTS_PAGE = "reference/lints.md"

# A code as a rule module spells it when it builds the finding, and as `cli/`
# spells it on the `CliError` that `check` renders as one. The constraint this
# puts on the rules is worth knowing: a code must appear as a double-quoted
# literal right after `code=`, and one assembled from a constant or handed in by
# a helper would be outside this check entirely.
_REGISTERED = re.compile(r'code="(SH\d+)"')

# A code as *documented*: the leading cell of a table row, and never a mention in
# prose. Both tables below say in their own text that a retired code is never
# reused, and the sentence naming a code as the example of that would otherwise
# be enough to keep the code "documented" after its row was deleted -- which is
# exactly the staleness these tests exist to catch.
_DOCUMENTED = re.compile(r"^\|\s*(SH\d+)\s*\|", re.MULTILINE)

# Every markdown link target except an `http`/`https` URL, which is a link *out*
# of the docs: the test below is about which pages exist, and matching one would
# fail the layout test over a working link. Nothing else is excluded -- a
# `mailto:`, a protocol-relative `//host/x` or an in-page `#anchor` would be read
# as a page and fail. None exists in the docs, and this is a layout test rather
# than a link checker, so the pattern is left as narrow as what it is asked.
_PAGE_LINK = re.compile(r"\]\((?!https?://)([^)]+)\)")

DOCS = Path(docs_path())
SOURCE = Path(seahaven.__file__).resolve().parent


def registered_codes() -> set[str]:
    """Every `SHnnn` a finding can be built with, read from the code that builds them.

    The rules are functions and not a registry -- there is no table to import --
    so the source is what there is to ask. Both directories, because `cli/`
    carries the two codes for a world that never got as far as being a `World`.
    """
    found = {
        code
        for directory in ("lint", "cli")
        for path in (SOURCE / directory).rglob("*.py")
        for code in _REGISTERED.findall(path.read_text(encoding="utf-8"))
    }
    assert found, "no lint codes were found in the source; this test is asking the wrong question"
    return found


def documented_codes() -> set[str]:
    return set(_DOCUMENTED.findall((DOCS / LINTS_PAGE).read_text(encoding="utf-8")))


@pytest.mark.parametrize("page", PAGES)
def test_every_page_of_the_layout_exists(page: str) -> None:
    assert (DOCS / page).is_file()


@pytest.mark.parametrize("page", PAGES)
def test_every_page_has_a_heading(page: str) -> None:
    """A stub is a page with a heading on it, never an empty file."""
    assert (DOCS / page).read_text(encoding="utf-8").startswith("# ")


def test_the_docs_are_where_seahaven_docs_says_they_are() -> None:
    """`seahaven docs` prints a directory read out of the installed package.

    The whole tree and not the top level: `reference/` is part of the layout, and
    a page added there without being added to the layout is one nothing links to
    and nobody maintains.
    """
    assert DOCS.is_dir()
    found = sorted(path.relative_to(DOCS).as_posix() for path in DOCS.rglob("*.md"))
    assert found == sorted(PAGES)


def test_every_page_index_md_links_to_is_a_page_of_the_layout() -> None:
    """`index.md` is the reading order, and it ships: a dead link in it ships too."""
    index = (DOCS / "index.md").read_text(encoding="utf-8")
    linked = set(_PAGE_LINK.findall(index))
    assert linked <= set(PAGES), f"{sorted(linked - set(PAGES))} is linked from index.md"


def test_a_link_out_of_the_docs_is_not_read_as_a_page_of_the_layout() -> None:
    """`index.md` has no external link today, and the first one must not fail the test above.

    Asserted on a sample rather than on the page, because the page that has no
    such link is exactly the page that cannot show this.
    """
    sample = "[the OpenEnv spec](https://example.invalid/spec) and [concepts](concepts.md)\n"
    assert set(_PAGE_LINK.findall(sample)) == {"concepts.md"}


def test_every_registered_lint_code_is_documented() -> None:
    """The one thing in the docs that goes stale without anyone noticing."""
    missing = registered_codes() - documented_codes()
    assert not missing, (
        f"{sorted(missing)} can be reported by `seahaven check` and is not in {LINTS_PAGE}"
    )


def test_the_lint_reference_documents_no_code_that_does_not_exist() -> None:
    """The other direction: a retired rule left in the reference is a lie too."""
    extra = documented_codes() - registered_codes()
    assert not extra, f"{sorted(extra)} is documented in {LINTS_PAGE} and no rule reports it"


def test_the_lint_packages_own_table_agrees_with_the_reference() -> None:
    """`seahaven/lint/__init__.py`'s docstring is the third copy of the list.

    It is what a reader of the code sees, `reference/lints.md` is what a reader
    of the docs sees, and the rules themselves are the truth. Pinning the two
    copies to the truth is what keeps a new rule from being documented in one
    place only.
    """
    from seahaven import lint

    assert set(_DOCUMENTED.findall(lint.__doc__ or "")) == registered_codes()


# `functional_spec.md` §11: `controller_run_sql` is deprecated and leaves the docs
# but for the one line of `reference/cli.md` that documents the flag it is behind.
# The prefix is what to search for, because it is how every control tool is
# spelled, and the exception is one line rather than a whole page, so that the
# deprecation line cannot quietly grow a worked example.
_CONTROL_PREFIX = "controller_"
_CONTROL_PAGE = "reference/cli.md"

# `Instance.changes()` was removed with the change log. A page that still calls it
# is an example that raises `AttributeError` for whoever pastes it, and no fence
# check catches a mention in prose.
_REMOVED_CALL = "changes()"


@pytest.mark.parametrize("page", PAGES)
def test_no_page_names_a_control_tool_but_the_cli_reference(page: str) -> None:
    text = (DOCS / page).read_text(encoding="utf-8")
    naming = [line for line in text.splitlines() if _CONTROL_PREFIX in line]
    if page != _CONTROL_PAGE:
        assert not naming, f"{page} names a control tool: {naming}"
    else:
        assert len(naming) == 1, f"{page} names a control tool on {len(naming)} lines, not one"


@pytest.mark.parametrize("page", PAGES)
def test_no_page_calls_the_removed_changes_method(page: str) -> None:
    text = (DOCS / page).read_text(encoding="utf-8")
    naming = [line for line in text.splitlines() if _REMOVED_CALL in line]
    assert not naming, f"{page} still calls a method the framework removed: {naming}"
