"""This world's Stripe HTTP API, served by `serve_http.py` through `seahaven.http`.

`handle` is the `seahaven.http` handler; `RESET_OPTIONS` is what each instance
the server makes starts from. Nothing here imports the server stack, so the
handler is importable, and testable, without the `http` extra.
"""

from seahaven_stripe_world.http_api.handler import RESET_OPTIONS, handle

__all__ = ["RESET_OPTIONS", "handle"]
