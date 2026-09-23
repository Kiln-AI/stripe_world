---
status: complete
---

# Phase 10: Documentation and Closeout

## Overview

Final phase: audit every register row for a disposition, update the declared-residue
list (functional spec §13), apply the SH206 correction from architecture §1.1,
update the README and AGENTS.md to reflect the rebuilt 8-tool surface with
composition requirement, and correct TS-02's rationale for the key-auth MCP surface.

## Steps

1. **Register audit.** Walk every row in `tells.md`. Close EC-05/DT-18 (guidance
   suffix — implemented in Phase 5, tested in refusal conformance). Assign
   dispositions to remaining open rows based on current code state: `closed` if
   implemented with a test, `declared` if a known limit documented in §13,
   `not-a-tell` if the premise was wrong.

2. **Reword TS-02.** The rationale says `get_stripe_account_info` "does not exist
   on real MCP". The enumeration used a secret-key MCP surface where it IS present
   (and `list_available_accounts_or_orgs`/`manage_stripe_accounts` are absent).
   Reword to reflect that the OAuth surface lacks it but the key surface has it.
   Disposition stays `declared`.

3. **Functional spec §13 corrections.**
   - Apply SH206 correction: downgrade from "unsafe under a prefixing host" to
     "a prefixing host earns a warning it is documented to accept". Remove the
     `SEAHAVEN_FINDINGS.md` entry reference.
   - Document that `mcp_catalogue.jsonl`'s `{"kind":"tools"}` record reflects
     the key-authenticated surface (9 tools), not the OAuth surface (10 tools).
   - Document the 15 key_restricted operations (Balance, Issuing, Payouts reads).
   - Add remaining declared items from Phases 7-9 findings.

4. **Update README.md.** Reflect the 8-tool registered surface with operation-id
   addressing, `stripe_context`/`livemode` context parameters, and the bare-body
   return shape. Add the composition requirement from functional spec §4.1.2.

5. **Update AGENTS.md.** Reflect the same surface changes and the composition
   requirement.

6. **Surface conformance test hardening.** Add a check that our schema does not
   carry spurious defaults absent from the real schema.

## Tests

- `test_no_spurious_defaults`: for each property in the real schema that has no default, verify our schema also has no default
