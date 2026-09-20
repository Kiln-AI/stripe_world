# CLI reference

The whole command tree, as a synopsis. Every command is spelled out below it.

```text
seahaven new <name> [--dir <path>] [--hub]
seahaven check [--world module:attr]
seahaven docs
seahaven fixture list [--world module:attr]
seahaven fixture freeze <id> --run module:function --description <text> [--now <ts>] [--world module:attr]
seahaven fixture fork <parent> <id> --run module:function --description <text> [--world module:attr]
seahaven serve [--host HOST] [--port PORT] [--max_concurrent_envs N] [--concurrency N]
               [--session-timeout SECONDS] [--include-control-tools] [--no-console]
               [--world module:attr]
```

Every command exits `0` on success and `1` on failure, and a failure is one line on stderr with the
fix in it, never a traceback. `seahaven check` also exits 1 when it found an error-severity finding,
which is what makes it usable in CI.

| Command | What it does |
|---|---|
| [`seahaven new`](#seahaven-new-name) | scaffold a world |
| [`seahaven check`](#seahaven-check) | run every lint |
| [`seahaven docs`](#seahaven-docs) | print the bundled docs directory |
| [`seahaven fixture list`](#seahaven-fixture-list) | every fixture of this world |
| [`seahaven fixture freeze`](#seahaven-fixture-freeze-id) | fill a blank instance and freeze it |
| [`seahaven fixture fork`](#seahaven-fixture-fork-parent-id) | fill an instance of a fixture and freeze the result |
| [`seahaven serve`](#seahaven-serve) | run this world's OpenEnv server |

## Finding the world

Every command except `new` and `docs` acts on a world, and finds it the same way: the nearest
`pyproject.toml` from the working directory, the package its `[project] name` normalises to, and the
attribute `world` on that package. A package that is not importable, or whose `world` is missing or
is not a `seahaven.World`, is an error naming the package and the fix.

`--world module:attr` overrides the convention for a layout it misses. `--world mypkg.world:world`
names the module the `World` is built in, and the lints climb out of it to the package around it,
which is where `schema/`, `tools/` and `middleware/` are. The pytest plugin has the same override,
spelled `--seahaven-world`, because pytest's option namespace is shared with every other plugin.

Paths in output are printed relative to the *project*, not to wherever the shell happened to be. So
`seahaven check` run from `src/mypkg/middleware/` and from the project root describe the same file
the same way.

## `seahaven new <name>`

Renders a complete world and stops. One table, two tools, an error handler, `errors.py`, `world.py`,
`openenv_app.py`, one passing test, an empty `fixtures/`, `README.md`, `AGENTS.md`, `.gitignore` and
the generator script. No fixture is built and nothing is imported.

| Option | What it does |
|---|---|
| `--dir <path>` | the directory to create the world in; the default is here |
| `--hub` | also write the five files `openenv push` requires: `openenv.yaml`, a root `Dockerfile`, a root `__init__.py`, `client.py` and `models.py` |

The package name is the world's name, normalised. `AGENTS.md` is written once and never touched
again. It is an ordinary file the world owns, and it points an authoring agent at these docs.

```sh
seahaven new notes
seahaven new notes --dir ~/projects --hub
```

The command refuses an existing directory rather than merging into one. It also refuses a name that
does not make a Python package name, and a name `World(name=...)` would not accept — the name
reaches the scaffolded `world.py` verbatim, so a world it cannot import is refused before the
directory is written.

The command finishes by printing what to do next. While Seahaven is unpublished, the `uv sync` in
that list is a trap: the scaffold's `seahaven~=0.0` resolves to a placeholder release that contains
none of the framework, so the sync succeeds and the world fails at import with `ModuleNotFoundError:
No module named 'seahaven.world'`. `seahaven check` itself cannot start either, because the
placeholder ships no console script. Install the framework from a checkout until publication. The
same applies to `--hub`: the `Dockerfile` it writes runs `uv sync --extra serve`, which today builds
an image whose container cannot start.

## `seahaven check`

Runs every lint over the world and prints each finding, sorted by code, then path, then line.

```
SH101 error src/badworld/schema/001_items.sql:8  table 'items' is not STRICT  fix: append STRICT to the CREATE TABLE items
SH201 warning src/badworld/tools/items.py:26  datetime.datetime.now() reads the wall clock, not the instance's  fix: take the instance's time from ctx.clock.iso() or ctx.clock.now()
SH301 error src/badworld/tools/orphan.py  module 'badworld.tools.orphan' is never imported, so it registers nothing  fix: import it from badworld/__init__.py, or from badworld.tools
```

It exits 1 if any finding is an `error`. Warnings alone exit 0. Run it before every commit. Every
rule is in [lints.md](lints.md).

## `seahaven docs`

Prints the directory holding the bundled docs for the installed version, and nothing else, so it
composes:

```sh
seahaven docs
```

```
cat "$(seahaven docs)/index.md"
```

The docs ship inside the installed package, so what this prints always matches the version
installed. That is why a scaffolded world's `AGENTS.md` says to run this rather than to search the
web for a framework that is not in an agent's training data.

## `seahaven fixture list`

Every fixture of the world, one per line, tab-separated: id, parent (`-` when there is none), `now`,
and the description.

```
agency	-	2026-06-01T09:00:00.000Z	A twelve-person agency: three teams, nine projects ...
empty	-	2026-06-01T09:00:00.000Z	The tracker's schema with no rows. Start here to write a history ...
small_startup	-	2026-06-01T09:00:00.000Z	A three-person startup's tracker: one engineering team ...
```

A world with no fixtures prints nothing and exits 0.

## `seahaven fixture freeze <id>`

Creates a blank instance from the world's schema, runs the generator against it, and freezes the
result to `fixtures/<id>/`.

| Option | What it does |
|---|---|
| `--run module:function` | **required.** The generator: a function taking the live instance and filling it |
| `--description <text>` | **required.** What the fixture holds and what scenarios it supports, for eval authors |
| `--now <ts>` | the instant to freeze the clock at; the default is the wall clock at creation |

```sh
seahaven fixture freeze empty \
    --now 2026-06-01T09:00:00.000Z \
    --run fixtures_src.generate:empty \
    --description "The schema with no rows. Start here to write a history."
```

**Pass `--now`, and pass the same value every time.** Without it the fixture is dated from the day
it was built, and nothing will tell you, because a wall-clock instant is a perfectly valid
timestamp. See [../db_schema_and_fixtures.md](../db_schema_and_fixtures.md).

The command refuses an id whose directory already exists.

## `seahaven fixture fork <parent> <id>`

Creates an instance of `parent`, runs the generator against it, and freezes the result with
`parent_id` set. It takes the same `--run` and `--description`, and no `--now`: a fork inherits its
parent's clock, which is the point of forking.

```sh
seahaven fixture fork empty small_startup \
    --run fixtures_src.generate:small_startup \
    --description "A three-person startup's tracker: one team, two projects, forty issues."
```

## `seahaven serve`

Runs the OpenEnv server for this world: one world, many sessions, one worker process. It needs the
`serve` extra; without it the command says so and exits 1.

| Option | Default | What it does |
|---|---|---|
| `--host HOST` | `0.0.0.0` | the address to bind |
| `--port PORT` | `8000` | the port to bind |
| `--max_concurrent_envs N` | `500` | how many sessions may be open at once |
| `--concurrency N` | `min(cpus, 16)` | how many tool calls run at once; `0` for no gate |
| `--session-timeout SECONDS` | `3600` | seconds of idleness before a session is reaped; `0` disables the reaper |
| `--include-control-tools` | off | make `controller_run_sql` callable over the wire; it is never listed, and it is deprecated in favour of the state document -- run Python with `-W default::DeprecationWarning` to see the warning |
| `--no-console` | off | do not serve the web console at `/console`; the address is otherwise printed when the server starts |

```sh
seahaven serve
seahaven serve --host 127.0.0.1 --port 9000
seahaven serve --include-control-tools --concurrency 0
```

`--max_concurrent_envs` is spelled with underscores because it is OpenEnv's own option name, and a
second spelling here would be one more thing to translate.

Read [../serving_and_openenv.md](../serving_and_openenv.md) before choosing `--concurrency`. The
gate is unfair whenever it binds, and the default is not exempt.

## Using it from Python

Nothing in the CLI is a hidden entry point. `seahaven.cli.main(argv)` is the whole command,
`seahaven.cli.find_world(explicit, start=...)` is the discovery, and
`seahaven.openenv.serve.serve(world, ...)` is the three lines `serve` runs, for a harness that wants
a server inside its own process without going through `argv`.
