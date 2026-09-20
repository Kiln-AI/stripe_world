"""This world's middleware. Importing the package registers every module in it.

`seahaven check` (SH301) fails if a module in this directory is not imported
here, so a middleware that exists but was never registered cannot go
unnoticed. Registration order is chain order, outermost first: the error
handler wraps everything, the Stripe envelope sits inside it so a bug in the
boundary reaches the agent as this world's `INTERNAL` rather than as a raw
traceback, and the idempotency layer is innermost — inside the envelope so it
sees `ApiResponse` returns and `StripeApiError` raises rather than rendered
bodies (`components/cross_cutting.md` §3.1.7).
"""

from stripeapi.middleware import error_handler, stripe_envelope  # noqa: I001 — order is chain order
from stripeapi.middleware import idempotency

# Import order is registration order is chain order (outermost first):
# error_handler, then stripe_envelope, then idempotency innermost. The isort
# above would alphabetize `idempotency` before `stripe_envelope`, which would
# put the idempotency layer *outside* the envelope — precisely the placement
# cross_cutting.md §3.1.7 forbids, because outside the envelope a 400 success
# and a 402 decline arrive as indistinguishable rendered dicts and the
# `pre_execution` flag that decides caching is already gone. Hence the noqa.
__all__ = ["error_handler", "idempotency", "stripe_envelope"]
