"""Serve StripeAPI over HTTP: `uv run serve_http.py`.

Point a Stripe SDK's API base at `http://127.0.0.1:8000/worlds/<id>`, with any
API key; each `<id>` is its own sandbox, made on first use from the `empty`
fixture. Command-line options come with `seahaven.http.main`.
"""

import seahaven.http

from seahaven_stripe_world import world
from seahaven_stripe_world.http_api import RESET_OPTIONS, handle

if __name__ == "__main__":
    seahaven.http.serve(world, handle, host="127.0.0.1", port=8000, reset_options=RESET_OPTIONS)
