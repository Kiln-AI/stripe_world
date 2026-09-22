---
status: complete
---

# Phase 17: Events

## Overview

Wire the two events routes (`GET /v1/events` and `GET /v1/events/{id}`) with a hand-written serializer and handlers. Events are already emitted by every earlier resource phase through `emit_event`; this phase makes them queryable. The list supports `type` with wildcard matching (e.g. `charge.*`), `types` array (mutually exclusive with `type`), `created` range filter, and the `delivery_success` quirk (true returns all, false returns nothing -- no actual delivery exists).

## Steps

1. Extend `resources/events.py` with: event serializer (`serialize`), two `ParamSpec`s (list, retrieve), and two hand-written handlers (`list_`, `retrieve`)
2. Wire the two routes in `dispatch/routes.py` with the new handlers, params, and response declarations
3. Update `test_tools.py` wired surface assertion for the two new operations
4. Write `tests/test_events.py` with comprehensive tests
5. Run checks and iterate

## Tests

- test_customer_created_event_shape: emit via customer create, retrieve event, verify full object shape (id, object, api_version, created, data.object, livemode, pending_webhooks, request, type)
- test_list_events_newest_first: multiple events list in reverse chronological order
- test_list_events_type_filter: exact type filtering (e.g. `customer.created`)
- test_list_events_type_wildcard: wildcard type matching (`customer.*` matches `customer.created` and `customer.updated`)
- test_list_events_types_array: the `types` array filter matches multiple types
- test_list_events_type_and_types_mutually_exclusive: sending both is rejected
- test_list_events_created_range: range filter on created timestamp
- test_list_events_delivery_success_true: returns all events
- test_list_events_delivery_success_false: returns no events
- test_list_events_pagination: starting_after/ending_before pagination
- test_retrieve_event_by_id: retrieve by id
- test_retrieve_unknown_event_404: missing id returns 404
- test_event_previous_attributes: an update event carries data.previous_attributes
- test_event_request_field: event.request contains request id and idempotency key
- test_unknown_parameter_refused: unknown parameter returns parameter_unknown
