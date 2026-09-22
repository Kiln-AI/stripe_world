-- The billing tables (Phases 12–16; DDL verbatim from components/data_model.md §5).
--
-- `invoices` and `customer_balance_transactions` landed with Phase 12
-- because that phase's subscription create writes both: the first invoice
-- is inseparable from the subscription that mints it, and finalization
-- applies the customer balance. `invoiceitems` (Phase 13) is the
-- pending-item store the invoice sweep reads. `credit_notes` landed with
-- Phase 15, and `subscription_schedules` with Phase 16.

CREATE TABLE subscriptions (
    id                          TEXT PRIMARY KEY,
    x_seq                       INTEGER NOT NULL,
    created                     TEXT NOT NULL,
    billing_cycle_anchor        TEXT NOT NULL,
    billing_cycle_anchor_config TEXT CHECK (billing_cycle_anchor_config IS NULL OR (json_valid(billing_cycle_anchor_config) AND json_type(billing_cycle_anchor_config) = 'object')),
    -- serialised as {"type": <value>}; `flexible` changes proration, so it is a column
    billing_mode                TEXT NOT NULL DEFAULT 'classic' CHECK (billing_mode IN ('classic', 'flexible')),
    billing_schedules           TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(billing_schedules) AND json_type(billing_schedules) = 'array'),
    billing_thresholds          TEXT CHECK (billing_thresholds IS NULL OR (json_valid(billing_thresholds) AND json_type(billing_thresholds) = 'object')),
    cancel_at                   TEXT,
    cancel_at_period_end        INTEGER NOT NULL DEFAULT 0 CHECK (cancel_at_period_end IN (0, 1)),
    canceled_at                 TEXT,
    cancellation_details        TEXT CHECK (cancellation_details IS NULL OR (json_valid(cancellation_details) AND json_type(cancellation_details) = 'object')),
    collection_method           TEXT NOT NULL CHECK (collection_method IN ('charge_automatically', 'send_invoice')),
    currency                    TEXT NOT NULL,
    customer                    TEXT NOT NULL REFERENCES customers (id),
    days_until_due              INTEGER,
    default_payment_method      TEXT REFERENCES payment_methods (id),
    default_tax_rates           TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(default_tax_rates) AND json_type(default_tax_rates) = 'array'),
    description                 TEXT,
    discounts                   TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(discounts) AND json_type(discounts) = 'array'),
    ended_at                    TEXT,
    invoice_settings            TEXT NOT NULL CHECK (json_valid(invoice_settings) AND json_type(invoice_settings) = 'object'),
    latest_invoice              TEXT REFERENCES invoices (id),
    metadata                    TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    next_pending_invoice_item_invoice TEXT,
    pause_collection            TEXT CHECK (pause_collection IS NULL OR (json_valid(pause_collection) AND json_type(pause_collection) = 'object')),
    payment_settings            TEXT CHECK (payment_settings IS NULL OR (json_valid(payment_settings) AND json_type(payment_settings) = 'object')),
    pending_invoice_item_interval TEXT CHECK (pending_invoice_item_interval IS NULL OR (json_valid(pending_invoice_item_interval) AND json_type(pending_invoice_item_interval) = 'object')),
    pending_setup_intent        TEXT REFERENCES setup_intents (id),
    pending_update              TEXT CHECK (pending_update IS NULL OR (json_valid(pending_update) AND json_type(pending_update) = 'object')),
    schedule                    TEXT REFERENCES subscription_schedules (id),
    start_date                  TEXT NOT NULL,
    status                      TEXT NOT NULL CHECK (status IN
                                    ('active','canceled','incomplete','incomplete_expired','past_due',
                                     'paused','trialing','unpaid')),
    trial_end                   TEXT,
    trial_settings              TEXT CHECK (trial_settings IS NULL OR (json_valid(trial_settings) AND json_type(trial_settings) = 'object')),
    trial_start                 TEXT,
    CHECK ((trial_start IS NULL) = (trial_end IS NULL)),
    CHECK (days_until_due IS NULL OR collection_method = 'send_invoice')
) STRICT;

CREATE UNIQUE INDEX subscriptions_by_seq  ON subscriptions (x_seq DESC);
CREATE INDEX subscriptions_by_customer ON subscriptions (customer, x_seq DESC);
CREATE INDEX subscriptions_by_status   ON subscriptions (status, x_seq DESC);
CREATE INDEX subscriptions_by_schedule ON subscriptions (schedule);

-- NOTE: there is no current_period_start/current_period_end on `subscription` at
-- 2026-08-26.dahlia. The billing period lives per item, below. A single period pair on
-- this row would be drift from the real object.

CREATE TABLE subscription_items (
    id                   TEXT PRIMARY KEY,
    x_seq                INTEGER NOT NULL,
    created              TEXT NOT NULL,
    billed_until         TEXT,
    billing_thresholds   TEXT CHECK (billing_thresholds IS NULL OR (json_valid(billing_thresholds) AND json_type(billing_thresholds) = 'object')),
    current_period_end   TEXT NOT NULL,
    current_period_start TEXT NOT NULL,
    discounts            TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(discounts) AND json_type(discounts) = 'array'),
    metadata             TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    price                TEXT NOT NULL REFERENCES prices (id),
    quantity             INTEGER,
    subscription         TEXT NOT NULL REFERENCES subscriptions (id),
    tax_rates            TEXT CHECK (tax_rates IS NULL OR (json_valid(tax_rates) AND json_type(tax_rates) = 'array')),
    CHECK (current_period_start < current_period_end)
) STRICT;

-- `subscription` is a REQUIRED query parameter on GET /v1/subscription_items, so this
-- index is the whole list path. `price` serves GET /v1/subscriptions?price=… .
CREATE UNIQUE INDEX subscription_items_by_seq   ON subscription_items (x_seq DESC);
CREATE INDEX subscription_items_by_subscription ON subscription_items (subscription, x_seq DESC);
CREATE INDEX subscription_items_by_price        ON subscription_items (price, subscription);

CREATE TABLE invoices (
    id                               TEXT PRIMARY KEY,
    x_seq                            INTEGER NOT NULL,
    created                          TEXT NOT NULL,
    amount_due                       INTEGER NOT NULL DEFAULT 0,
    amount_overpaid                  INTEGER NOT NULL DEFAULT 0,
    amount_paid                      INTEGER NOT NULL DEFAULT 0,
    amount_paid_off_stripe           INTEGER NOT NULL DEFAULT 0,
    amount_remaining                 INTEGER NOT NULL DEFAULT 0,
    amount_shipping                  INTEGER NOT NULL DEFAULT 0,
    attempt_count                    INTEGER NOT NULL DEFAULT 0,
    attempted                        INTEGER NOT NULL DEFAULT 0 CHECK (attempted IN (0, 1)),
    auto_advance                     INTEGER NOT NULL DEFAULT 1 CHECK (auto_advance IN (0, 1)),
    automatically_finalizes_at       TEXT,
    billing_reason                   TEXT CHECK (billing_reason IS NULL OR billing_reason IN
                                         ('automatic_pending_invoice_item_invoice','manual','quote_accept',
                                          'subscription','subscription_create','subscription_cycle',
                                          'subscription_threshold','subscription_update','upcoming')),
    collection_method                TEXT NOT NULL CHECK (collection_method IN ('charge_automatically', 'send_invoice')),
    currency                         TEXT NOT NULL,
    customer                         TEXT NOT NULL REFERENCES customers (id),
    customer_address                 TEXT CHECK (customer_address IS NULL OR (json_valid(customer_address) AND json_type(customer_address) = 'object')),
    customer_email                   TEXT,
    customer_name                    TEXT,
    customer_phone                   TEXT,
    customer_shipping                TEXT CHECK (customer_shipping IS NULL OR (json_valid(customer_shipping) AND json_type(customer_shipping) = 'object')),
    customer_tax_exempt              TEXT CHECK (customer_tax_exempt IS NULL OR customer_tax_exempt IN ('exempt', 'none', 'reverse')),
    default_payment_method           TEXT REFERENCES payment_methods (id),
    default_tax_rates                TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(default_tax_rates) AND json_type(default_tax_rates) = 'array'),
    description                      TEXT,
    discounts                        TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(discounts) AND json_type(discounts) = 'array'),
    due_date                         TEXT,
    effective_at                     TEXT,
    ending_balance                   INTEGER,
    footer                           TEXT,
    last_finalization_error          TEXT CHECK (last_finalization_error IS NULL OR (json_valid(last_finalization_error) AND json_type(last_finalization_error) = 'object')),
    lines                            TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(lines) AND json_type(lines) = 'array'),
    metadata                         TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    next_payment_attempt             TEXT,
    number                           TEXT,
    -- invoice.parent, flattened: `subscription` is a list filter, so it cannot live
    -- inside a JSON blob. Rebuilt on serialisation as
    -- {"type":"subscription_details","subscription_details":{"subscription":…,…}}
    parent_type                      TEXT CHECK (parent_type IS NULL OR parent_type IN ('quote_details', 'subscription_details')),
    parent_subscription              TEXT REFERENCES subscriptions (id),
    parent_subscription_proration_date TEXT,
    payment_settings                 TEXT NOT NULL CHECK (json_valid(payment_settings) AND json_type(payment_settings) = 'object'),
    period_end                       TEXT NOT NULL,
    period_start                     TEXT NOT NULL,
    post_payment_credit_notes_amount INTEGER NOT NULL DEFAULT 0,
    pre_payment_credit_notes_amount  INTEGER NOT NULL DEFAULT 0,
    starting_balance                 INTEGER NOT NULL DEFAULT 0,
    statement_descriptor             TEXT,
    status                           TEXT CHECK (status IS NULL OR status IN ('draft', 'open', 'paid', 'uncollectible', 'void')),
    status_transitions               TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(status_transitions) AND json_type(status_transitions) = 'object'),
    subtotal                         INTEGER NOT NULL DEFAULT 0,
    subtotal_excluding_tax           INTEGER,
    total                            INTEGER NOT NULL DEFAULT 0,
    total_discount_amounts           TEXT CHECK (total_discount_amounts IS NULL OR (json_valid(total_discount_amounts) AND json_type(total_discount_amounts) = 'array')),
    total_excluding_tax              INTEGER,
    total_pretax_credit_amounts      TEXT CHECK (total_pretax_credit_amounts IS NULL OR (json_valid(total_pretax_credit_amounts) AND json_type(total_pretax_credit_amounts) = 'array')),
    total_taxes                      TEXT CHECK (total_taxes IS NULL OR (json_valid(total_taxes) AND json_type(total_taxes) = 'array')),
    -- Internal: the charge that paid this invoice.  Not serialized (no
    -- `charge` field exists on `invoice` at 2026-08-26.dahlia).  Written by
    -- `_mark_paid` so credit notes can refund the correct charge (Phase 15).
    _charge                          TEXT REFERENCES charges (id),
    CHECK (parent_subscription IS NULL OR parent_type = 'subscription_details'),
    -- `uncollectible` joins `draft` in the un-numbered set (recorded, cassette
    -- 01): the invoice_now cancel collapse marks its credit-only final invoice
    -- uncollectible WITHOUT ever finalizing it, so no number exists to carry.
    CHECK (number IS NOT NULL OR status IN ('draft', 'uncollectible'))
) STRICT;

CREATE UNIQUE INDEX invoices_by_seq           ON invoices (x_seq DESC);
-- number is NULL until finalization, so the uniqueness has to be partial. See §3.13.
CREATE UNIQUE INDEX invoices_number            ON invoices (number) WHERE number IS NOT NULL;
CREATE INDEX invoices_by_customer          ON invoices (customer, x_seq DESC);
CREATE INDEX invoices_by_status            ON invoices (status, x_seq DESC);
CREATE INDEX invoices_by_subscription      ON invoices (parent_subscription, x_seq DESC);
CREATE INDEX invoices_by_collection_method ON invoices (collection_method, x_seq DESC);

-- No top-level `subscription` column and no `days_until_due`: neither field exists on
-- `invoice` at 2026-08-26.dahlia. Functional spec §4 is right and the widely-documented
-- older shape is wrong for this version.

CREATE TABLE invoiceitems (
    id               TEXT PRIMARY KEY,
    x_seq            INTEGER NOT NULL,
    -- `invoiceitem` has NO `created` field; its creation timestamp is `date`. See §3.11.
    date             TEXT NOT NULL,
    amount           INTEGER NOT NULL,
    currency         TEXT NOT NULL,
    customer         TEXT NOT NULL REFERENCES customers (id),
    description      TEXT,
    discountable     INTEGER NOT NULL DEFAULT 1 CHECK (discountable IN (0, 1)),
    discounts        TEXT CHECK (discounts IS NULL OR (json_valid(discounts) AND json_type(discounts) = 'array')),
    frozen_fields    TEXT CHECK (frozen_fields IS NULL OR (json_valid(frozen_fields) AND json_type(frozen_fields) = 'array')),
    -- Bare, deliberately: deleting a draft invoice does NOT release its
    -- swept items (recorded, cassette 13) — they stay attached to the dead
    -- invoice, refuse later deletes, and read as deleted on update. A
    -- REFERENCES clause would refuse exactly that. The parent lookup is
    -- the writer's job (`resources/invoiceitems.py`), like
    -- `customer_balance_transactions.credit_note` below.
    invoice          TEXT,
    metadata         TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    net_amount       INTEGER,
    parent           TEXT CHECK (parent IS NULL OR (json_valid(parent) AND json_type(parent) = 'object')),
    period_end       TEXT NOT NULL,
    period_start     TEXT NOT NULL,
    pricing          TEXT CHECK (pricing IS NULL OR (json_valid(pricing) AND json_type(pricing) = 'object')),
    proration        INTEGER NOT NULL DEFAULT 0 CHECK (proration IN (0, 1)),
    proration_details TEXT CHECK (proration_details IS NULL OR (json_valid(proration_details) AND json_type(proration_details) = 'object')),
    quantity         INTEGER NOT NULL DEFAULT 1,
    quantity_decimal TEXT NOT NULL DEFAULT '1',
    tax_rates        TEXT CHECK (tax_rates IS NULL OR (json_valid(tax_rates) AND json_type(tax_rates) = 'array'))
) STRICT;

CREATE UNIQUE INDEX invoiceitems_by_seq     ON invoiceitems (x_seq DESC);
CREATE INDEX invoiceitems_by_customer ON invoiceitems (customer, x_seq DESC);
CREATE INDEX invoiceitems_by_invoice  ON invoiceitems (invoice, x_seq DESC);
-- GET /v1/invoiceitems?pending=true is exactly "not yet swept onto an invoice"
CREATE INDEX invoiceitems_pending     ON invoiceitems (x_seq DESC) WHERE invoice IS NULL;

CREATE TABLE customer_balance_transactions (
    id             TEXT PRIMARY KEY,
    x_seq          INTEGER NOT NULL,
    created        TEXT NOT NULL,
    amount         INTEGER NOT NULL,
    credit_note    TEXT REFERENCES credit_notes (id),
    currency       TEXT NOT NULL,
    customer       TEXT NOT NULL REFERENCES customers (id),
    description    TEXT,
    ending_balance INTEGER NOT NULL,
    invoice        TEXT REFERENCES invoices (id),
    metadata       TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    type           TEXT NOT NULL CHECK (type IN (
        'adjustment','applied_to_invoice','checkout_session_subscription_payment',
        'checkout_session_subscription_payment_canceled','credit_note','initial','invoice_overpaid',
        'invoice_too_large','invoice_too_small','migration','unapplied_from_invoice',
        'unspent_receiver_credit'))
) STRICT;

-- There is no top-level list path; the only list is scoped to one customer.
CREATE UNIQUE INDEX cbt_by_seq  ON customer_balance_transactions (x_seq DESC);
CREATE INDEX cbt_by_customer    ON customer_balance_transactions (customer, x_seq DESC);
CREATE INDEX cbt_by_invoice     ON customer_balance_transactions (invoice, x_seq DESC);
CREATE INDEX cbt_by_credit_note ON customer_balance_transactions (credit_note);

-- Phase 15: the credit_notes table and its line-item freeze (DDL verbatim from
-- components/data_model.md §5). credit_note.lines are frozen JSON (written once
-- by the transaction that writes the parent, never independently mutated).

CREATE TABLE credit_notes (
    id                           TEXT PRIMARY KEY,
    x_seq                        INTEGER NOT NULL,
    created                      TEXT NOT NULL,
    amount                       INTEGER NOT NULL,
    amount_shipping              INTEGER NOT NULL DEFAULT 0,
    currency                     TEXT NOT NULL,
    customer                     TEXT NOT NULL REFERENCES customers (id),
    customer_balance_transaction TEXT REFERENCES customer_balance_transactions (id),
    discount_amount              INTEGER NOT NULL DEFAULT 0,
    discount_amounts             TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(discount_amounts) AND json_type(discount_amounts) = 'array'),
    effective_at                 TEXT,
    invoice                      TEXT NOT NULL REFERENCES invoices (id),
    lines                        TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(lines) AND json_type(lines) = 'array'),
    memo                         TEXT,
    metadata                     TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    number                       TEXT NOT NULL,
    out_of_band_amount           INTEGER,
    post_payment_amount          INTEGER NOT NULL DEFAULT 0,
    pre_payment_amount           INTEGER NOT NULL DEFAULT 0,
    pretax_credit_amounts        TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(pretax_credit_amounts) AND json_type(pretax_credit_amounts) = 'array'),
    reason                       TEXT CHECK (reason IS NULL OR reason IN
                                     ('duplicate', 'fraudulent', 'order_change', 'product_unsatisfactory')),
    refunds                      TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(refunds) AND json_type(refunds) = 'array'),
    status                       TEXT NOT NULL CHECK (status IN ('issued', 'void')),
    subtotal                     INTEGER NOT NULL,
    subtotal_excluding_tax       INTEGER,
    total                        INTEGER NOT NULL,
    total_excluding_tax          INTEGER,
    total_taxes                  TEXT CHECK (total_taxes IS NULL OR (json_valid(total_taxes) AND json_type(total_taxes) = 'array')),
    type                         TEXT NOT NULL CHECK (type IN ('mixed', 'post_payment', 'pre_payment')),
    voided_at                    TEXT,
    CHECK ((status = 'void') = (voided_at IS NOT NULL)),
    CHECK (amount = pre_payment_amount + post_payment_amount)
) STRICT;

CREATE UNIQUE INDEX credit_notes_by_seq  ON credit_notes (x_seq DESC);
CREATE INDEX credit_notes_by_customer ON credit_notes (customer, x_seq DESC);
CREATE INDEX credit_notes_by_invoice  ON credit_notes (invoice, x_seq DESC);

-- Phase 16: subscription_schedules. DDL verbatim from components/data_model.md §5.
-- `phases` is the scoped-down array: each element carries items, start_date,
-- end_date, iterations, trial, discounts, proration_behavior, collection_method
-- and metadata, not the full 21-property phase schema. Timestamps inside it are
-- Unix seconds (data_model §3.10).

CREATE TABLE subscription_schedules (
    id                    TEXT PRIMARY KEY,
    x_seq                 INTEGER NOT NULL,
    created               TEXT NOT NULL,
    billing_mode          TEXT NOT NULL DEFAULT 'classic' CHECK (billing_mode IN ('classic', 'flexible')),
    canceled_at           TEXT,
    completed_at          TEXT,
    current_phase         TEXT CHECK (current_phase IS NULL OR (json_valid(current_phase) AND json_type(current_phase) = 'object')),
    customer              TEXT NOT NULL REFERENCES customers (id),
    default_settings      TEXT NOT NULL CHECK (json_valid(default_settings) AND json_type(default_settings) = 'object'),
    end_behavior          TEXT NOT NULL CHECK (end_behavior IN ('cancel', 'none', 'release', 'renew')),
    metadata              TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    phases                TEXT NOT NULL CHECK (json_valid(phases) AND json_type(phases) = 'array'),
    released_at           TEXT,
    released_subscription TEXT,
    status                TEXT NOT NULL CHECK (status IN ('active', 'canceled', 'completed', 'not_started', 'released')),
    subscription          TEXT REFERENCES subscriptions (id)
) STRICT;

CREATE UNIQUE INDEX subscription_schedules_by_seq      ON subscription_schedules (x_seq DESC);
CREATE INDEX subscription_schedules_by_customer     ON subscription_schedules (customer, x_seq DESC);
CREATE INDEX subscription_schedules_by_subscription ON subscription_schedules (subscription);
