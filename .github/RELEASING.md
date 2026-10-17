# Releasing Stripe World

This is the maintainer's checklist for releasing Stripe World. A release is one tagged commit on
`main`, published twice: to PyPI as
[seahaven-stripe-world](https://pypi.org/project/seahaven-stripe-world/), and to the Hugging Face
Space [scosman/stripe_world](https://huggingface.co/spaces/scosman/stripe_world), where Hugging
Face builds the root `Dockerfile` and runs it. `X.Y.Z` stands for the new version throughout.

1. [Bump the version](#1-bump-the-version)
2. [Tag the release](#2-tag-the-release)
3. [Check the release](#3-check-the-release)
4. [Publish to PyPI](#4-publish-to-pypi)
5. [Push to the Space](#5-push-to-the-space)
6. [Set the Space card](#6-set-the-space-card)
7. [Test the Space](#7-test-the-space)

The Space's `Dockerfile` installs the world from the files `openenv push` uploads (`COPY . /app`,
then `uv sync`), not from PyPI, so neither publish waits on the other. PyPI goes first because it
is the one that cannot be redone: if PyPI refuses the upload, the release stops before the Space
runs a version PyPI does not have.

## 1. Bump the version

The version is written in four places, and all four must agree. Seahaven treats the world's
`version` as its contract: it appears in every state document, in the OpenEnv metadata and in
every fixture frozen from then on.

| File | Field | Written by |
|---|---|---|
| `pyproject.toml` | `[project] version`, the version PyPI publishes | `uv version` |
| `uv.lock` | `version` of the `seahaven-stripe-world` package | `uv version`, never by hand |
| `src/seahaven_stripe_world/world.py` | `version=` in `seahaven.World(...)` | hand |
| `openenv.yaml` | `version:` | hand |

On a branch off `main`:

```sh
uv version X.Y.Z           # rewrites pyproject.toml and re-locks uv.lock
```

`uv version --bump patch`, `minor` or `major` works out the next version instead of taking one.
`uv.lock` is never edited by hand: uv writes it, with `uv version` here and with `uv lock` or
`uv sync` after any other change to `pyproject.toml`. Then set the same version in `world.py` and
`openenv.yaml` by hand. `git diff --stat` now lists exactly those four files. Run the check list in
`AGENTS.md` ("Commands"), where `tests/test_package.py` fails unless all four agree, commit, and
merge the bump into `main` through a pull request once CI is green. CI syncs with
`uv sync --locked`, so a `pyproject.toml` bumped without its `uv.lock` fails there.

Leave the `world_version` in each `fixtures/*/fixture.yaml` alone. It records the version that
fixture was frozen from, and fixtures are never edited. Seahaven loads a fixture frozen at an
earlier version as long as its `schema_hash` matches, and reports the difference as
`frozen_world_version`. A schema change cannot reach `main` with stale fixtures, because
`seahaven check` fails on the hash (SH403). The earlier versions named in `specs/` are history.

## 2. Tag the release

Once the pull request has merged, tag the commit on `main` that carries the bump and push the tag:

```sh
git switch main
git pull --ff-only
uv version --short         # prints X.Y.Z
grep -n -E '^version: |version="' openenv.yaml src/seahaven_stripe_world/world.py   # both X.Y.Z
git tag vX.Y.Z
git push origin vX.Y.Z
```

Every step below publishes that tag. A tag is never moved after it is published: PyPI keeps what
it was given, so a fix after a release is a new version.

## 3. Check the release

Release from a fresh clone of the tag. `openenv push` uploads the working tree, not git, so a
working checkout would also upload git-ignored files such as `research/stripe-openapi/spec3.json`.
It skips only dot-files, such as `.git` and `.venv`, and bytecode, so the build below writes beside
the clone rather than into it.

```sh
git clone --branch vX.Y.Z https://github.com/Kiln-AI/stripe_world.git stripe_world-vX.Y.Z
cd stripe_world-vX.Y.Z
uv run --extra serve openenv validate .  # only failure: "openenv.yaml has no `validation:` block"
uv build --out-dir ../stripe_world-vX.Y.Z-dist
```

A Seahaven world has no reward to declare, so the missing `validation:` block is expected.

`openenv`, `hf` and `huggingface_hub` come only from the `serve` extra, so every command in this
document that runs one of them passes `--extra serve`, and `uv run` installs the extra before it
runs the command. Without the flag, the command works only if the environment was last synced with
the extra. A plain `uv sync` is exact and removes it, and `uv run` then stops with
`` Failed to spawn: `openenv` `` or `ModuleNotFoundError: No module named 'huggingface_hub'`. The
first of these commands also creates the clone's environment, on Python 3.14 final: the extra's
`beartype` does not import on a 3.14 release candidate.

List what will be uploaded to PyPI:

```sh
unzip -l ../stripe_world-vX.Y.Z-dist/seahaven_stripe_world-X.Y.Z-py3-none-any.whl
tar tzf ../stripe_world-vX.Y.Z-dist/seahaven_stripe_world-X.Y.Z.tar.gz
```

Both carry the whole `seahaven_stripe_world` package, its `schema/*.sql` and `spec/` files
included, with `LICENSE` and `THIRD_PARTY_LICENSES.md`; the sdist adds `pyproject.toml` and
`README.md`. Neither carries `fixtures/`, `tests/`, `research/` or `specs/`. As the comment at the
top of `pyproject.toml` says, a fixture is a checkout's file, so an install from PyPI has none.

Then install the wheel in a throwaway environment and run it:

```sh
uv run --isolated --no-project --with ../stripe_world-vX.Y.Z-dist/*.whl python -I -c '
from importlib.metadata import version
from seahaven_stripe_world import world
assert world.version == version("seahaven-stripe-world"), world.version
with world.instance() as inst:
    print(world.version, len(inst.tools()), inst.call("get_stripe_account_info")["object"])'
```

It prints `X.Y.Z 8 account`. An `AssertionError` that names an older version means the bump missed
`world.py`. `-I` keeps the clone off the import path, so the code that runs is the wheel's. The
environment resolves dependencies the way a user's install does, without `uv.lock` or the
`pydantic` override in `pyproject.toml`, so a dependency release that breaks the world fails here.

## 4. Publish to PyPI

`uv publish` authenticates with a PyPI API token, which it reads from `UV_PUBLISH_TOKEN`. Create a
token scoped to the `seahaven-stripe-world` project under API tokens in pypi.org's account
settings. Never commit it, and keep it out of shell history:

```sh
read -rs UV_PUBLISH_TOKEN && export UV_PUBLISH_TOKEN   # paste the token; it is not echoed
uv publish ../stripe_world-vX.Y.Z-dist/*
unset UV_PUBLISH_TOKEN
```

PyPI can scope a token to a project only once the project exists, so the first upload, 0.3.0,
needed an account-wide token; every later release uses a project-scoped one.

PyPI never accepts the same file name twice, so a version cannot be uploaded again, not even after
deleting it, and a mistake found after the upload needs a new version. Yanking the broken one on
pypi.org stops installs that do not pin it from choosing it. If the upload stops partway, run it
again with `--check-url https://pypi.org/simple/`, which skips the files PyPI already has.

Open https://pypi.org/project/seahaven-stripe-world/X.Y.Z/, which shows the README, then install
the published package the way a user would:

```sh
uv run --isolated --no-project --refresh-package seahaven-stripe-world \
    --with seahaven-stripe-world==X.Y.Z python -I -c '
from importlib.metadata import version
from seahaven_stripe_world import world
assert world.version == version("seahaven-stripe-world"), world.version
with world.instance() as inst:
    print(world.version, len(inst.tools()), inst.call("get_stripe_account_info")["object"])'
```

`--refresh-package` makes uv ask PyPI again rather than answer from its cache. It prints
`X.Y.Z 8 account`.

## 5. Push to the Space

From the release clone:

```sh
uv run --extra serve hf auth login  # once per machine; openenv push also asks when logged out
uv run --extra serve openenv push --repo-id scosman/stripe_world
```

Keep the web interface on, which is the default, and do not pass `--base-image`.

## 6. Set the Space card

Run this after every push. Hugging Face reads a Space's settings, such as its title, card
description and start page, from YAML front matter at the top of the Space's `README.md`. GitHub
shows that front matter as a table at the top of the repository page, so this repository's README
has none. Without front matter, `openenv push` writes its own defaults, which set the start page to
`/web` and replace the title and card. This command replaces them on the Space only:

```sh
uv run --extra serve python -c '
from huggingface_hub import metadata_update
metadata_update("scosman/stripe_world", {
    "title": "Stripe World", "emoji": "💳", "colorFrom": "indigo", "colorTo": "purple",
    "short_description": "Stripe API + MCP for agent evals and RL. Built with Seahaven",
    "base_path": "/console", "tags": ["openenv", "seahaven"],
}, repo_type="space", overwrite=True)'
```

`short_description` must be 60 characters or fewer, or Hugging Face refuses it. The change commits
to the Space and restarts it.

## 7. Test the Space

Wait for the build in the Space's logs, then open
https://huggingface.co/spaces/scosman/stripe_world. It opens on the web console. Then drive it from
any Python with `openenv` installed, such as `uv run --extra serve python` in the release clone:

```py
from openenv import AutoEnv

with AutoEnv.from_hub("scosman/stripe_world", skip_install=True) as env:
    env.reset(seed=7)
    print(env.step({"type": "list_tools"}).observation)
```

It prints the world's tools.
