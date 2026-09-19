"""This world's middleware. Importing the package registers every module in it.

`seahaven check` (SH301) fails if a module in this directory is not imported
here, so a middleware that exists but was never registered cannot go
unnoticed. Registration order is chain order, outermost first: the error
handler wraps everything, and the Stripe envelope sits inside it so a bug in
the boundary reaches the agent as this world's `INTERNAL` rather than as a
raw traceback. The idempotency layer registers inside the envelope in a later
phase (`components/cross_cutting.md` §3.1.7).
"""

from stripeapi.middleware import error_handler, stripe_envelope

__all__ = ["error_handler", "stripe_envelope"]
