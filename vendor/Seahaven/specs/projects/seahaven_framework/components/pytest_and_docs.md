---
status: complete
---

# Component: the pytest plugin and the bundled docs

Modules: `seahaven/pytest_plugin.py` and `seahaven/docs/` (package data).

## 1. The pytest plugin

Registered through `[project.entry-points.pytest11] seahaven = "seahaven.pytest_plugin"`, so
installing `seahaven` activates it in any project; it does nothing unless a test uses its fixtures.

```python
def pytest_configure(config):
    config.addinivalue_line("markers", "seahaven(fixture, seed=None, **startup_kwargs): the fixture the `instance` fixture is created from")

@pytest.fixture(scope="session")
def world(request) -> World:
    return cli.find_world(request.config.getoption("--seahaven-world"), start=request.config.rootpath)

@pytest.fixture
def instance(request, world) -> Iterator[Instance]:
    marker = request.node.get_closest_marker("seahaven")
    if marker is None:
        pytest.fail("the `instance` fixture needs a marker: @pytest.mark.seahaven(fixture=\"empty\")")
    if "fixture" in marker.kwargs: fixture = marker.kwargs["fixture"]     # None means a blank instance
    elif marker.args: fixture = marker.args[0]
    else: pytest.fail(...)
    kwargs = {k: v for k, v in marker.kwargs.items() if k != "fixture"}
    with world.instance(fixture, **kwargs) as inst:     # seed and startup kwargs pass through
        yield inst
```

- `--seahaven-world module:attr` is added as a pytest option (`pytest_addoption`) for the same
  override the CLI has.
- `fixture=None` is a blank instance, not a missing value: the marker's absence is the failure, so
  the sentinel is "was `fixture` given at all", never a falsy test.
- The `world` fixture is session-scoped: one import, one `World`. Instances are function-scoped and
  destroyed on teardown even when the test fails.
- `pytest.fail` messages show the exact marker line to add.
- Worlds' own tests import nothing from `seahaven.pytest_plugin`; they use the fixtures by name.
- Seahaven's own test suite uses the plugin too, against ProjectTracker and the small worlds under
  `tests/worlds/`, which is the plugin's integration test.

## 2. Bundled docs (later phase)

Layout, fixed now so `seahaven docs` and `AGENTS.md` can point at it:

```
seahaven/docs/
  index.md              # what Seahaven is, the reading order, the commands
  concepts.md           # world, fixture, instance, tool, clock, reproducibility, changesets (what a changeset means)
  authoring.md          # writing tools (the whole docstring is the description; Field(alias="from") for wire
                        #   names Python cannot spell), errors and the error handler, middleware, startup hooks
                        #   (spell the parameters out; **kwargs switches off typo detection), schema rules
  composition.md        # adding other worlds: add_world, ctx.worlds, shared stores, composite fixtures
  fixtures.md           # freezing, forking, generators (every fixture is committed with the script that
                        #   generates it; any schema change means regenerating them all), descriptions for eval authors
  testing.md            # the pytest plugin, what to test in a world
  serving.md            # seahaven serve, the OpenEnv client, control tools, publishing to a hub (seahaven new --hub)
  openenv.md            # OpenEnv compatibility: driving a world from any client, the wire protocol, no rewards
  extensions.md         # the extension contract, the XML-RPC example
  reference/api.md      # the public API, hand-written
  reference/lints.md    # every SHnnn code: rule, why, fix
  reference/cli.md      # every subcommand and option
  projecttracker.md     # the walkthrough of the reference world
```

Every page is hand-written; nothing is generated and there is no drift test. One test asserts that
every registered lint code appears in `reference/lints.md`, which is the only part that goes stale
silently. Docs are shipped as package data -- the build backend packages every file under
`src/seahaven`, not only the `.py` ones -- read with `importlib.resources`, and `seahaven docs`
prints the directory.

*Extended 2026-09-14 — `composition.md` joined the layout with the world-composition project's
phase 6, and the scaffolded `AGENTS.md` in §3 names it for a world that adds worlds. The layout
here is what `tests/test_docs.py` asserts the shipped directory is, so a page that exists and is
not listed here would be a page nothing links to and nobody maintains, which is what that test is
for.*

*Corrected 2026-09-13 — this paragraph named hatchling's `[tool.hatch.build] include` as what
ships the docs. The backend is `uv_build` now (`BACKLOG.md` B24), and neither backend needed an
`include`: both package every file under the module directory. What carries the docs is therefore
where they live, `src/seahaven/docs/`, and not a declaration -- which is why
`tests/test_packaging.py` builds the wheel and reads the pages out of it rather than trusting a
backend's defaults.*

## 3. The scaffolded `AGENTS.md`

`seahaven new` writes it once; nothing rewrites it afterwards, so its content is evergreen and
carries no version (the reading order gained `concepts.md` in Phase 8's code review: §2's own
`index.md` leads with it, and a file written once must not send an authoring agent past it):

```
# Seahaven world

This project is a Seahaven world. Seahaven is not in your training data: read the bundled docs
before writing code. They ship inside the installed `seahaven` package and match the installed
version; `seahaven docs` prints the directory. Start at index.md, then concepts.md, authoring.md,
fixtures.md, testing.md; composition.md if this world adds other worlds.

Commands: `uv run seahaven check` (lint; run before every commit), `uv run pytest`,
`uv run seahaven fixture list`, `uv run seahaven serve`.

Rules: time comes from `ctx.clock` and ids and randomness from `ctx.ids`, so a replay of the same
fixture and seed gives the same result; SQL goes through `ctx.db` (`ctx.db.conn` is the raw APSW
connection when you need it; never close it or change its pragmas). Fixtures are immutable: fork,
never edit.
Every tool module under tools/ and middleware/ must be imported from the package __init__.
A world this one adds is reached with `ctx.worlds.<name>`, never by making an instance of it; prefer
that world's own tools over direct SQL on its store.
Errors are ToolError subclasses in errors.py; the error handler is the only place engine errors are mapped.

## About this world
```

## 4. Test plan

- `test_pytest_plugin.py` using `pytester`: a test with the marker gets a fresh instance and it is
  destroyed after; `fixture=None` gives a blank instance; two tests with the same marker do not
  share state; missing marker fails with the hint; `seed=`, `now=` and startup kwargs pass through;
  `--seahaven-world` override; `world` is session-scoped (same object across two tests).
- `test_docs.py`: every file in the layout exists (the later phase fills content; until then, stubs
  with a heading so the layout is stable); every registered lint code appears in
  `reference/lints.md`.
