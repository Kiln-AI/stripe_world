-- Infrastructure tables (components/data_model.md §5, cross_cutting.md §3.1.3).
-- `counters` and `events` are needed from the dispatcher phase on — `x_seq`
-- assignment and event emission; `idempotency_keys` joins in the money-path
-- phase, when the first keyed POST exists (cross_cutting.md §3.1).

CREATE TABLE events (
    id                      TEXT PRIMARY KEY,
    x_seq                   INTEGER NOT NULL,
    created                 TEXT NOT NULL,
    api_version             TEXT NOT NULL DEFAULT '2026-08-26.dahlia',
    -- {"object": <the full API object as of the change>, "previous_attributes": {...}?}
    -- Its timestamps are Unix seconds: it is a wire snapshot, emitted verbatim (data_model §3.10).
    data                    TEXT NOT NULL CHECK (json_valid(data) AND json_type(data) = 'object'),
    request_id              TEXT,
    request_idempotency_key TEXT,
    type                    TEXT NOT NULL
) STRICT;

CREATE UNIQUE INDEX events_by_seq ON events (x_seq DESC);
CREATE INDEX events_by_type    ON events (type, x_seq DESC);

-- `type` carries NO CHECK. The closed set is 266 values and is not derivable from
-- spec3.json at all (event.type is a bare string by design); it lives in
-- spec/event_types.py and emit_event() raises WorldBug on an unknown type at call time
-- (architecture §6.3). A 266-value CHECK would be an unreadable second copy of a list
-- that is already enforced one layer up, and it would refuse a legitimate value the
-- moment the two drifted. This is the one deliberate exception to "closed set -> CHECK".

-- The only mutable counter in the world: one row per listable table for `x_seq`
-- (data_model §3.1 rule 10). `value` is the last number handed out, so the first row
-- of any table gets 1. Seeding static reference rows from a schema file is explicitly
-- allowed (capability-map §Schema) and these INSERTs read no clock.
--
-- invoice.number does NOT draw from here: its sequence is per-customer and lives on
-- customers.next_invoice_sequence, which is a real Stripe field. See data_model §3.13.
CREATE TABLE counters (
    name  TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0 CHECK (value >= 0)
) STRICT;

INSERT INTO counters (name, value) VALUES
    ('customers', 0), ('products', 0), ('prices', 0), ('coupons', 0),
    ('promotion_codes', 0), ('tax_rates', 0), ('payment_methods', 0),
    ('payment_intents', 0), ('charges', 0), ('refunds', 0), ('disputes', 0),
    ('setup_intents', 0), ('balance_transactions', 0), ('payouts', 0),
    ('subscriptions', 0), ('subscription_items', 0), ('subscription_schedules', 0),
    ('invoices', 0), ('invoiceitems', 0), ('credit_notes', 0),
    ('customer_balance_transactions', 0), ('events', 0);

-- The idempotency layer's store (cross_cutting.md §3.1.3, DDL verbatim).
-- `status` and `body` are null while in flight, non-null once complete; `body`
-- holds the rendered response body through the one canonical dump. The table
-- is untracked (world.py): a middleware write must never appear in a graded
-- episode's change log, and the table has no `x_seq` because it is never
-- listed — its only reads are by primary key.
CREATE TABLE idempotency_keys (
    key          TEXT    NOT NULL PRIMARY KEY,
    method       TEXT    NOT NULL,
    path         TEXT    NOT NULL,
    request_hash TEXT    NOT NULL,
    state        TEXT    NOT NULL CHECK (state IN ('in_flight', 'complete')),
    status       INTEGER,
    body         TEXT    CHECK (body IS NULL OR json_valid(body)),
    created      TEXT    NOT NULL
) STRICT;
