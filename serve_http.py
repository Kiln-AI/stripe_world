"""Serve StripeAPI over HTTP: `uv run serve_http.py [--port N] [--fixture NAME] ...`.

Point a Stripe SDK's API base at `http://127.0.0.1:8000/worlds/<id>`, with any
API key; each `<id>` is its own account, made on first use. With no options it
is a blank instance (the same rows as the `empty` fixture) in live mode, the
world's default; `--reset-options '{"startup": {"livemode": false}}'` (or
`SEAHAVEN_RESET_OPTIONS`) makes every instance a test-mode account. `--help`
lists every option; `seahaven.http.main` defines them.
"""

import seahaven.http

from seahaven_stripe_world import world
from seahaven_stripe_world.http_api import handle

if __name__ == "__main__":
    seahaven.http.main(world, handle)
