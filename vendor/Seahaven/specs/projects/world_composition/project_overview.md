---
status: complete
---

# World Composition

Composable worlds for Seahaven. Drafted 2026-09-11 from a working session with the lead; the lead's
words are kept where they were said. This is a follow-on to the Seahaven core specs, which are
complete through planning and heading into the proof of concept.

## The idea

One world implements Slack-like APIs, another an email/Resend-like API, another implements a Stripe
clone. I can then build a world called `my_company_agent` which imports those three (maybe
namespacing), adds a few more APIs and the company DB, and have it be a combined environment.

## Status going in

This was never in the original plan. A search of all 141 spec files and all 50 commits found
nothing: world-level composition was never specified, and nothing was lost. (Update 2026-09-13: the
framework spec in `../seahaven_framework/`, merged after this was written, had recorded "composable
world modules" as a post-V1 P2 item awaiting "DDL and fixture composition rules"; the functional
spec here supplies them and is written against that framework.) What exists nearby is not this —
`functional_areas/endpoint_primitive/spec.md` §5.1 in the planning repository composes *projections* into an exposure (the tool
layer of one world), and HUD's "namespaced mounts" appear only in the column explaining why we are
not building on HUD.

## The shape: real composition

Two alternatives were considered and one more surfaced during the session:

1. **Real composition** (chosen): `@world.add_subworld(stripe_world, namespace='stripe')`. Worlds
   import worlds. The framework owns the composition.
2. **Downstream composition**: the upstream environment host lets you add a set of synthetic worlds to your run config. "Way
   less cool." It also fails the motivating case: no shared clock, no single inspection or changeset
   story, no cross-world calls from world code, and the company's own DB and APIs have no home — they
   would be a fourth world that cannot call the other three.
3. **Author-time composition**: `seahaven new my_company --import stripe,slack` vendors the component
   code into the new world as editable code. "Option 3 is interesting too." Cheaper, no runtime
   machinery, and lets a world customise the component; loses upgrade propagation. Worth keeping on
   the table as an alternative or as a cheap experiment that tests demand before the full build.

## Decisions made in the session

- **Schema conformance is classic composition.** The composed world checks only its own DDL and
  dispatches to sub-worlds, which check their own. The checking logic composes as-is; only the
  fixture sidecar's shape changes.
- **A saved fixture in the composite app includes all the frozen databases and one shared
  `fixture.yaml`** (time, etc). One sidecar for the composite, N database files inside it.
- **Sub-worlds always start empty.** Imported worlds cannot access their own fixtures; they are
  filled through the composed world and frozen into the composed world's fixture store. Letting you
  import a Stripe DB with records from 2021 and a Resend one starting in 2026 is a footgun. The rule
  exists to keep the API from offering it.
- **Expect users to build data cohesively.** Whether composition buys data reuse as well as code
  reuse is open — creation agents can make it work. The framework's job is not to hand them an API
  footgun: no freezing data a decade apart, no attaching two datasets that do not make sense
  together.
- **Cross-world references are not a problem.** "I can't join across Stripe and Slack today, this is
  fine. N DBs is realistic." References between components are soft (an id column resolved by calling
  the other component's endpoint), exactly as in reality.
- **Versioning solves versioning.** Standard dependency pinning. A component bump invalidates that
  namespace's hash and surfaces as "this fixture needs regenerating", which is the behaviour the
  existing rule already produces.
- **No auto-prefixing of tool names.** Namespacing is explicit in code. This keeps the agent-facing
  surface hand-written and faithful, and leaves `namespace=` as internal identity only (which store
  file, which endpoint id, which changeset key, which attached schema name).
- **Inspection uses an asymmetry**: N isolated connections for the agent, one read-only connection
  with the component files ATTACHed for the eval, so state-based grading stays a single SQL query
  across components. ATTACH is right for read-only eval inspection and wrong for the agent-facing
  `run_sql`, which must not see sibling components' tables.
- **Seeding helpers are P3.** A component may ship code that populates a blank instance of itself;
  worlds can use it if they want. Optional, brief mention at most, and not a framework concept.

## Why it might be worth building

The pitch is that a client world becomes mostly its own code plus a few imports. That value is
proportional to how many client agents touch the *same* third-party vendor APIs. The three client
cases recorded in the Seahaven specs (the 175-API client, the PostHog-like, the Linear-like) are all
clients' own surfaces, where a component library buys nothing. This is an open strategic question,
not a technical one, and it should be answered honestly during the functional spec rather than
assumed.

The fidelity caveat that goes with it: a reusable Stripe world is faithful to *Stripe*, which is a
real, fixed, documented API — that holds up well. It holds up badly wherever a client has customised
the product they use. Seahaven's founding rule is that a world must mock a real existing world exactly,
or the optimised agent does not transfer.
