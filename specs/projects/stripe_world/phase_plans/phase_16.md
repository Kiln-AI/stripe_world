---
status: complete
---

# Phase 16: Subscription Schedules

## Overview

Wire the subscription schedules resource (6 routes): a five-status state machine (not_started, active, completed, canceled, released) that manages subscription lifecycle through predefined phases. The phases array is scoped down per functional_spec section 3.2 -- Connect fields, automatic_tax, add_invoice_items, invoice_settings at the phase level, and item-level discounts/price_data/billing_thresholds are omitted.

## Steps

1. Add `subscription_schedules` table DDL to `schema/003_billing.sql` with indexes and FK to subscriptions; add REFERENCES clause to `subscriptions.schedule` (circular FK pair)
2. Create `resources/subscription_schedules.py`: serializer (FieldMap + derived fields for billing_mode, default_settings, phases), 6 ParamSpecs, handlers for create/update/cancel/release, engine-served list/retrieve
3. Replace 6 stub routes in `dispatch/routes.py` with wired handlers
4. Regenerate spec artifacts (`prune_spec`) to include subscription_schedule schemas in `spec3.min.json`
5. Regenerate empty fixture for new schema hash
6. Update `test_empty_fixture.py` and `test_tools.py` for new table and route surface

## Tests

- test_the_created_body_constants: object shape, id prefix, livemode, default_settings constants
- test_create_with_phases_activates_immediately: status=active, subscription created, current_phase set
- test_create_with_iterations: iterations parameter computes end_date with multi-interval-count strides
- test_create_multi_phase: two-phase creation chains start/end dates
- test_create_with_future_start_stays_not_started: future start_date keeps not_started
- test_create_without_phases_stays_not_started: no phases -> not_started
- test_create_from_subscription: from_subscription links and goes active
- test_from_subscription_refuses_already_scheduled: double-scheduling rejected
- test_metadata_rides_create_and_update: merge/delete semantics
- test_list_returns_newest_first: list ordering and customer filter
- test_retrieve_by_id: engine-served retrieve
- test_retrieve_unknown_id_is_404: missing id -> 404
- test_update_end_behavior: end_behavior mutation
- test_update_default_settings: default_settings mutation
- test_update_refuses_terminal_statuses: canceled schedule rejects update
- test_cancel_not_started: not_started -> canceled
- test_cancel_active_also_cancels_subscription: active -> canceled, subscription canceled and unlinked
- test_cancel_active_with_prorate_and_invoice_now: prorate/invoice_now flags pass through
- test_cancel_refuses_already_canceled: double-cancel rejected
- test_release_active_schedule: active -> released, subscription freed
- test_release_with_preserve_cancel_date: keeps subscription cancel_at_period_end
- test_release_without_preserve_cancel_date_clears_it: clears pending cancellation
- test_release_refuses_not_started: non-active schedule rejects release
- test_events_are_emitted: all four event types emitted
- test_unknown_parameter_refused: parameter_unknown on bogus param
- test_the_idempotent_create_replays: keyed POST replays
