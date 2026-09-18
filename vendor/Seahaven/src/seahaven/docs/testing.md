# Testing a world

Installing `seahaven` activates its pytest plugin. There is nothing to add to a `conftest.py`,
nothing to import, and nothing to subclass. The plugin adds two fixtures, one marker and one option.

```python
import pytest

import seahaven

pytestmark = pytest.mark.seahaven(fixture="small_startup")


def test_an_issue_can_be_read_by_its_key(instance: seahaven.Instance) -> None:
    issue = instance.call("get_issue", key="ENG-12")
    assert issue["key"] == "ENG-12"


def test_a_missing_issue_is_this_worlds_not_found(instance: seahaven.Instance) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("get_issue", key="ENG-9999")
    assert raised.value.code == "NOT_FOUND"
```

That example, and the others on this page, run against ProjectTracker — see
[projecttracker.md](projecttracker.md). In your own world the tool names are yours.

## The two fixtures

**`world`** is the `World` object, found the way every other Seahaven command finds it: the nearest
`pyproject.toml`, the package its `[project] name` normalises to, and the attribute `world` on it.
It is **session-scoped**, so one import gives one object for the whole run. A `World` is a
declaration, and no test mutates it.

**`instance`** is a fresh instance per test, made from the fixture the marker names, and destroyed
on teardown whether the test passed or failed. Two tests with the same marker share nothing: each
gets its own copy of the fixture's file.

## The marker

```py
@pytest.mark.seahaven(fixture="agency", seed=7)
def test_a_report_is_the_same_every_time(instance: seahaven.Instance) -> None: ...
```

| Written | What it makes |
|---|---|
| `fixture="small_startup"` | an instance of that fixture on disk |
| `fixture=None` | a blank instance, built from the schema |
| `fixture=None, now="2026-01-01T00:00:00.000Z"` | a blank instance with its clock set |
| `fixture="agency", seed=7` | a fixed seed, so ids repeat |
| `fixture="agency", user_id="u_12"` | a startup keyword of this world's, passed to its hooks |

Everything but `fixture` is passed straight through to `world.instance(...)`, so `seed`, `now` and
any startup keyword your world accepts work exactly as they do in process. `fixture` may also be
given positionally: `@pytest.mark.seahaven("small_startup")`.

**`fixture=None` is a blank instance, not a missing value.** What fails is the marker's *absence*:

```
the `instance` fixture needs a marker: @pytest.mark.seahaven(fixture="empty") for a fixture on
disk, or @pytest.mark.seahaven(fixture=None) for a blank instance
```

A module-level `pytestmark` is the usual spelling, with a marker on one test where that test needs a
different fixture. A marker on a test **replaces** the module's rather than adding to it, so every
marker names its own fixture, including one written only to add `seed=`. The plugin says so in the
failure message, because it is the commonest way to get here.

Two `seahaven` markers in **one place** — on one test, on one class, or in one module's `pytestmark`
list — are refused. Nothing chooses between them on purpose, so the plugin names the node that
carries both and asks you to keep one.

## Running them

```sh
uv run pytest
uv run pytest --seahaven-world mypackage:world
```

`--seahaven-world module:attr` is the plugin's namespace-qualified version of the CLI's `--world`,
for a layout the convention misses. Everything else — `-k`, `-x`, markers of your own — is ordinary
pytest. The plugin adds two fixtures, one marker and that one option, and does nothing at all to a
run that does not use them.

## What is worth testing in a world

**Every tool, through a real call.** Write `instance.call("create_issue", ...)`, never
`create_issue(ctx, ...)`. What you are testing is the tool: its argument model, its JSON schema, the
transaction it runs in, and the error handler above it. All of that is Seahaven's work on the
signature, and only a real call exercises it.

**The errors, by code.** Assert `raised.value.code == "NOT_FOUND"`, not the message text. The code
is the contract; the wording is presentation.

**The argument model, where it matters.** A tool that takes a `Literal` should have a test that the
sixth value is refused, and a tool with a patterned timestamp one that a bare date is.

```python
import pytest

import seahaven


@pytest.mark.seahaven(fixture="small_startup")
def test_an_unknown_status_is_refused_before_the_tool_runs(instance: seahaven.Instance) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("list_issues", status="nearly_done")
    # ProjectTracker's error handler restates the framework's ArgumentError in
    # this product's vocabulary, which is what an agent sees.
    assert raised.value.code == "INVALID_INPUT"
```

**The state, not just the answer.** `inst.inspect()` is a read-only handle with `one` and `rows`,
over every table, on a second connection. Use it to check what a tool wrote, including in tables the
tool does not return.

```python
import pytest

import seahaven


@pytest.mark.seahaven(fixture="small_startup")
def test_transitioning_an_issue_records_it_in_the_trail(instance: seahaven.Instance) -> None:
    issue = instance.call("get_issue", key="ENG-3")
    instance.call("transition_issue", issue_id=issue["id"], status="done")

    stored = instance.inspect().one("SELECT status FROM issues WHERE id = ?", issue["id"])
    assert stored == {"status": "done"}
    # The fixture's own history is in this table too. What the episode added is
    # everything stamped with the instance's one instant, in insertion order --
    # here, the status change and the assignee this product drops when an issue
    # closes.
    episode = instance.inspect().rows(
        "SELECT kind FROM issue_events WHERE issue_id = ? AND created_at = ? ORDER BY rowid",
        issue["id"],
        instance.clock.iso(),
    )
    assert [event["kind"] for event in episode] == ["status", "assignee"]
```

**The change log, for anything an eval will grade.** If an eval is going to score "the issue was
closed", test that closing it produces the change you expect. [state.md](state.md) is the page on
the log and the document an eval reads it from.

```python
import pytest

import seahaven


@pytest.mark.seahaven(fixture="small_startup")
def test_closing_an_issue_is_one_update_of_that_row(instance: seahaven.Instance) -> None:
    issue = instance.call("get_issue", key="ENG-4")
    instance.call("transition_issue", issue_id=issue["id"], status="canceled")

    issue_records = [record for record in instance.change_log() if record.table == "issues"]
    assert [record.op for record in issue_records] == ["update"]
    assert issue_records[0].key == {"id": issue["id"]}
    assert issue_records[0].after["status"] == "canceled"
```

**Determinism, once.** One test that the same seed gives the same ids is worth having, because it
fails the day someone reaches for `uuid.uuid4()`. It needs two instances, so it takes the `world`
fixture rather than `instance`:

```python
import seahaven


def test_the_same_seed_mints_the_same_ids(world: seahaven.World) -> None:
    def first_issue_id() -> str:
        with world.instance("small_startup", seed=3) as inst:
            project = inst.inspect().one("SELECT id FROM projects ORDER BY id LIMIT 1")
            created = inst.call("create_issue", project_id=str(project["id"]), title="A")
            return str(created["id"])

    assert first_issue_id() == first_issue_id()
```

**The fixtures' own invariants.** A fixture is data an eval will rely on, so assert the things you
have promised in its description — that `agency` really has twelve people, that no closed issue has
an assignee — and regenerating it cannot then quietly change the deal.

**What not to test: the framework.** That a `Literal` produces a JSON schema, that a rollback rolls
back, that the change log records a write. Those have tests of their own in Seahaven's suite.
