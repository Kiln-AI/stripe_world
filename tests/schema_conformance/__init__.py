"""The schema-conformance package: test-only, by design.

Nothing in ``src/stripeapi/`` imports this; the shipped package needs neither
``stripe-python`` nor a Stripe key to run (components/conformance.md,
Dependencies). Two modules: ``validate`` answers the cheap constant question —
does every object this world returns match the shape the pinned spec gives it?
— and ``capture`` is the pytest hook that feeds it every response body any
test produces.
"""
