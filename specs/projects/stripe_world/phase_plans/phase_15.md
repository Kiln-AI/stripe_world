---
status: complete
---

# Phase 15: Credit Notes and Customer Balance Transactions

## Overview

Wire the credit notes resource (8 routes) and customer balance transactions resource (4 routes). Credit notes implement Stripe's three-channel settlement model: refund, customer balance, and out-of-band. Customer balance transactions provide the scoped adjustment API under `/v1/customers/{customer}/balance_transactions`.

## Steps

1. Add `credit_notes` table DDL to `schema/003_billing.sql` with CHECK constraints and indexes
2. Create `resources/credit_notes.py`: serializer (FieldMap + presence_sets), 8 ParamSpecs, handlers for create/update/void/list_lines/preview/preview_lines
3. Create `resources/customer_balance_transactions.py`: scoped serializer, 4 ParamSpecs, handlers for create/update
4. Replace 12 stub routes in `dispatch/routes.py` with wired handlers
5. Regenerate spec artifacts (`prune_spec`) to include new operations in `spec3.min.json`
6. Regenerate empty fixture for new schema hash
7. Update `test_empty_fixture.py` and `test_tools.py` for new table and route surface

## Tests

- test_create_credit_note_on_paid_invoice: refund channel default settlement
- test_create_credit_note_on_open_invoice: pre-payment credit note
- test_credit_amount_channel: customer balance settlement writes cbt row
- test_out_of_band_channel: out-of-band amount recorded
- test_credit_note_lines: line items structure
- test_amount_exceeds_limit: rejection when amount > invoice amount_remaining
- test_draft_invoice_refused: draft invoices cannot have credit notes
- test_credit_note_numbering: CN-1/CN-2/CN-3, void does not renumber
- test_update_credit_note_memo: memo update
- test_update_credit_note_metadata: metadata merge semantics
- test_void_credit_note: void lifecycle transition
- test_void_reversal: void reverses customer balance adjustment
- test_list_credit_notes: list with pagination
- test_retrieve_credit_note: retrieve by id
- test_retrieve_credit_note_404: missing id error
- test_credit_note_lines_pagination: lines sub-resource pagination
- test_preview: preview without persistence
- test_preview_lines: preview lines pagination
- test_cbt_create: adjustment balance transaction
- test_cbt_update: description and metadata update
- test_cbt_list_and_retrieve: list and retrieve scoped to customer
- test_cbt_balance_invariant: ending_balance matches sum of amounts
- test_cbt_wrong_customer_404: scope enforcement
- test_credit_note_cbt_integration: credit note + cbt round-trip
- test_credit_note_events: event emission
- test_credit_note_on_paid_invoice_with_metadata: metadata on create
- test_void_already_voided: double-void error
- test_cbt_retrieve_404: missing transaction error
