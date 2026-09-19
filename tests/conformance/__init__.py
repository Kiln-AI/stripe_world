"""Cassette conformance: recorded real-API scenarios, replayed against this
world, diffed through a reviewed allow-list
(`specs/projects/stripe_world/components/conformance.md`).

Nothing in this package imports `stripe` — that package is the recorder's
transport and lives behind `tools_dev/` only, so replay and every hygiene
test run with no network and no recording dependency installed.
"""
