# Releasing Stripe World

This is the maintainer's checklist for publishing Stripe World to its Hugging Face Space,
[scosman/stripe_world](https://huggingface.co/spaces/scosman/stripe_world). A release is a push of
`main` to the Space. Hugging Face builds the root `Dockerfile` and runs it.

1. [Check `main`](#1-check-main)
2. [Push to the Space](#2-push-to-the-space)
3. [Set the Space card](#3-set-the-space-card)
4. [Test the Space](#4-test-the-space)

## 1. Check `main`

Push from a fresh clone. `openenv push` uploads the working tree, not git, so a working checkout
would also upload git-ignored files such as `research/stripe-openapi/spec3.json`.

```sh
git clone https://github.com/Kiln-AI/stripe_world.git stripe_world-release
cd stripe_world-release
uv sync --extra serve      # Python 3.14 final; provides the openenv and hf commands
uv run openenv validate .  # the only failure is "openenv.yaml has no `validation:` block"
```

A Seahaven world has no reward to declare, so the missing `validation:` block is expected.

## 2. Push to the Space

```sh
uv run hf auth login       # once per machine; openenv push also asks if you are not logged in
uv run openenv push --repo-id scosman/stripe_world
```

Keep the web interface on, which is the default, and do not pass `--base-image`.

## 3. Set the Space card

Run this after every push. Hugging Face reads a Space's settings, such as its title, card
description and start page, from YAML front matter at the top of the Space's `README.md`. GitHub
shows that front matter as a table at the top of the repository page, so this repository's README
has none. Without front matter, `openenv push` writes its own defaults, which set the start page to
`/web` and replace the title and card. This command replaces them on the Space only:

```sh
uv run python -c '
from huggingface_hub import metadata_update
metadata_update("scosman/stripe_world", {
    "title": "Stripe World", "emoji": "💳", "colorFrom": "indigo", "colorTo": "purple",
    "short_description": "Stripe API + MCP for agent evals and RL. Built with Seahaven",
    "base_path": "/console", "tags": ["openenv", "seahaven"],
}, repo_type="space", overwrite=True)'
```

`short_description` must be 60 characters or fewer, or Hugging Face refuses it. The change commits
to the Space and restarts it.

## 4. Test the Space

Wait for the build in the Space's logs, then open
https://huggingface.co/spaces/scosman/stripe_world. It opens on the web console. Then drive it from
any Python with `openenv` installed:

```py
from openenv import AutoEnv

with AutoEnv.from_hub("scosman/stripe_world", skip_install=True) as env:
    env.reset(seed=7)
    print(env.step({"type": "list_tools"}).observation)
```

It prints the world's tools.
