---
status: complete
---

# No Tells

Make our fake Stripe MCP indistinguishable from the real thing.

## Context

This project sits on top of [`specs/projects/stripe_world`](../stripe_world/), which specifies and
builds the world itself. That project is still being implemented and will be complete before we
code this one. Nothing in this project should make definitive statements about "what is
implemented" in `stripe_world` — that spec is still moving.

## The goal

An agent working inside our world should not be able to tell it is not talking to Stripe. Anything
that reveals the difference is a **tell**, and the job is to find the tells and remove them.

## Known cases

- **Unimplemented features.** Behave as if the API key does not have access to them — a permission
  error — not as an "unimplemented" error.

## Unknown cases

- Look for other cases where a real MCP and our fake one can be distinguished.

## In general

- **Probe the real MCP** to see how it behaves.
- **Add tests aligned to the real probes.**
- **Make the tests pass.**

## Process

This project needs a **research phase before the functional spec**: hit the real MCP, find the
places where we differ, and let what actually comes back form the plan. The functional spec is
written against probe results, not against our reading of the docs.
