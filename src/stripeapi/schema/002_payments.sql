-- StripeAPI payments tables (components/data_model.md §4). Same conventions as 001_core:
-- STRICT tables, explicit primary keys, no wall-clock expression anywhere, money INTEGER
-- minor units, JSON columns guarded by json_valid + json_type. The payments tables land one
-- resource phase at a time: payment_methods (Phase 6), the money path (Phase 8), refunds
-- and disputes (Phase 9), setup_intents (Phase 10), the balance ledger and payouts
-- (Phase 11).

CREATE TABLE payment_methods (
    id              TEXT PRIMARY KEY,
    x_seq           INTEGER NOT NULL,
    created         TEXT NOT NULL,
    type            TEXT NOT NULL CHECK (type IN (
        'acss_debit','affirm','afterpay_clearpay','alipay','alma','amazon_pay','au_becs_debit',
        'bacs_debit','bancontact','billie','bizum','blik','boleto','card','card_present','cashapp',
        'crypto','custom','customer_balance','eps','fpx','giropay','grabpay','ideal',
        'interac_present','kakao_pay','klarna','konbini','kr_card','link','mb_way','mobilepay',
        'multibanco','naver_pay','nz_bank_account','oxxo','p24','pay_by_bank','payco','paynow',
        'paypal','payto','pix','promptpay','revolut_pay','samsung_pay','satispay','scalapay',
        'sepa_debit','sofort','sunbit','swish','twint','upi','us_bank_account','wechat_pay','zip')),
    allow_redisplay TEXT CHECK (allow_redisplay IS NULL OR allow_redisplay IN ('always', 'limited', 'unspecified')),
    billing_details TEXT NOT NULL CHECK (json_valid(billing_details) AND json_type(billing_details) = 'object'),
    customer        TEXT REFERENCES customers (id),
    metadata        TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    -- The rail sub-object, serialised under the key named by `type`. Full
    -- payment_method_card / payment_method_us_bank_account shapes for those two rails,
    -- `{}` for the other 55. One column, not 57: see data_model §3.9.
    rail            TEXT NOT NULL CHECK (json_valid(rail) AND json_type(rail) = 'object'),
    -- Failure-injection tag from the magic-card table; never serialised.
    x_behavior      TEXT
) STRICT;

CREATE UNIQUE INDEX payment_methods_by_seq  ON payment_methods (x_seq DESC);
CREATE INDEX payment_methods_by_customer ON payment_methods (customer, x_seq DESC);
CREATE INDEX payment_methods_by_type     ON payment_methods (type, x_seq DESC);

-- charges ↔ payment_intents are mutually referential and both crossing columns
-- are nullable, so the write order is PI (latest_charge NULL) → charge → UPDATE
-- PI, satisfying non-deferred FKs at every step (data_model §4). The charge's
-- two ledger columns referenced a table that did not exist until Phase 11, so
-- they landed without their FK clauses then (SQLite refuses to prepare an
-- INSERT into a table whose FK parent was never created); Phase 11 restores
-- the clauses beside the ledger table itself.

CREATE TABLE payment_intents (
    id                          TEXT PRIMARY KEY,
    x_seq                       INTEGER NOT NULL,
    created                     TEXT NOT NULL,
    amount                      INTEGER NOT NULL,
    amount_capturable           INTEGER NOT NULL DEFAULT 0,
    amount_received             INTEGER NOT NULL DEFAULT 0,
    automatic_payment_methods   TEXT CHECK (automatic_payment_methods IS NULL OR (json_valid(automatic_payment_methods) AND json_type(automatic_payment_methods) = 'object')),
    canceled_at                 TEXT,
    cancellation_reason         TEXT CHECK (cancellation_reason IS NULL OR cancellation_reason IN
                                    ('abandoned','automatic','duplicate','expired','failed_invoice',
                                     'fraudulent','requested_by_customer','void_invoice')),
    capture_method              TEXT NOT NULL CHECK (capture_method IN ('automatic', 'automatic_async', 'manual')),
    client_secret               TEXT,
    confirmation_method         TEXT NOT NULL CHECK (confirmation_method IN ('automatic', 'manual')),
    currency                    TEXT NOT NULL,
    customer                    TEXT REFERENCES customers (id),
    description                 TEXT,
    last_payment_error          TEXT CHECK (last_payment_error IS NULL OR (json_valid(last_payment_error) AND json_type(last_payment_error) = 'object')),
    latest_charge               TEXT REFERENCES charges (id),
    metadata                    TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    next_action                 TEXT CHECK (next_action IS NULL OR (json_valid(next_action) AND json_type(next_action) = 'object')),
    payment_method              TEXT REFERENCES payment_methods (id),
    payment_method_options      TEXT CHECK (payment_method_options IS NULL OR (json_valid(payment_method_options) AND json_type(payment_method_options) = 'object')),
    payment_method_types        TEXT NOT NULL DEFAULT '["card"]' CHECK (json_valid(payment_method_types) AND json_type(payment_method_types) = 'array'),
    receipt_email               TEXT,
    setup_future_usage          TEXT CHECK (setup_future_usage IS NULL OR setup_future_usage IN ('off_session', 'on_session')),
    shipping                    TEXT CHECK (shipping IS NULL OR (json_valid(shipping) AND json_type(shipping) = 'object')),
    statement_descriptor        TEXT,
    statement_descriptor_suffix TEXT,
    status                      TEXT NOT NULL CHECK (status IN
                                    ('canceled','processing','requires_action','requires_capture',
                                     'requires_confirmation','requires_payment_method','succeeded'))
) STRICT;

CREATE UNIQUE INDEX payment_intents_by_seq  ON payment_intents (x_seq DESC);
CREATE INDEX payment_intents_by_customer ON payment_intents (customer, x_seq DESC);

CREATE TABLE charges (
    id                             TEXT PRIMARY KEY,
    x_seq                          INTEGER NOT NULL,
    created                        TEXT NOT NULL,
    amount                         INTEGER NOT NULL,
    amount_captured                INTEGER NOT NULL DEFAULT 0,
    amount_refunded                INTEGER NOT NULL DEFAULT 0,
    balance_transaction            TEXT REFERENCES balance_transactions (id),
    billing_details                TEXT NOT NULL CHECK (json_valid(billing_details) AND json_type(billing_details) = 'object'),
    calculated_statement_descriptor TEXT,
    captured                       INTEGER NOT NULL CHECK (captured IN (0, 1)),
    currency                       TEXT NOT NULL,
    customer                       TEXT REFERENCES customers (id),
    description                    TEXT,
    disputed                       INTEGER NOT NULL DEFAULT 0 CHECK (disputed IN (0, 1)),
    failure_balance_transaction    TEXT REFERENCES balance_transactions (id),
    failure_code                   TEXT,
    failure_message                TEXT,
    fraud_details                  TEXT CHECK (fraud_details IS NULL OR (json_valid(fraud_details) AND json_type(fraud_details) = 'object')),
    metadata                       TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    outcome                        TEXT CHECK (outcome IS NULL OR (json_valid(outcome) AND json_type(outcome) = 'object')),
    paid                           INTEGER NOT NULL CHECK (paid IN (0, 1)),
    payment_intent                 TEXT REFERENCES payment_intents (id),
    payment_method                 TEXT REFERENCES payment_methods (id),
    payment_method_details         TEXT CHECK (payment_method_details IS NULL OR (json_valid(payment_method_details) AND json_type(payment_method_details) = 'object')),
    receipt_email                  TEXT,
    receipt_number                 TEXT,
    receipt_url                    TEXT,
    refunded                       INTEGER NOT NULL DEFAULT 0 CHECK (refunded IN (0, 1)),
    shipping                       TEXT CHECK (shipping IS NULL OR (json_valid(shipping) AND json_type(shipping) = 'object')),
    statement_descriptor           TEXT,
    statement_descriptor_suffix    TEXT,
    status                         TEXT NOT NULL CHECK (status IN ('failed', 'pending', 'succeeded')),
    CHECK (amount_refunded <= amount_captured)
) STRICT;

CREATE UNIQUE INDEX charges_by_seq        ON charges (x_seq DESC);
CREATE INDEX charges_by_customer       ON charges (customer, x_seq DESC);
CREATE INDEX charges_by_payment_intent ON charges (payment_intent, x_seq DESC);

-- refunds + disputes (Phase 9, probed 2026-09-20). The ledger table exists
-- from Phase 11 on, so both reference columns carry their FK clauses — the
-- Phase 8 deferral comment above is discharged by the same phase that first
-- writes them (recorded: refunds carry a txn_ at creation live).

CREATE TABLE refunds (
    id                          TEXT PRIMARY KEY,
    x_seq                       INTEGER NOT NULL,
    created                     TEXT NOT NULL,
    amount                      INTEGER NOT NULL,
    balance_transaction         TEXT REFERENCES balance_transactions (id),
    charge                      TEXT REFERENCES charges (id),
    currency                    TEXT NOT NULL,
    customer                    TEXT REFERENCES customers (id),
    description                 TEXT,
    destination_details         TEXT CHECK (destination_details IS NULL OR (json_valid(destination_details) AND json_type(destination_details) = 'object')),
    failure_balance_transaction TEXT REFERENCES balance_transactions (id),
    failure_reason              TEXT,
    instructions_email          TEXT,
    metadata                    TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    payment_intent              TEXT REFERENCES payment_intents (id),
    payment_method              TEXT REFERENCES payment_methods (id),
    pending_reason              TEXT CHECK (pending_reason IS NULL OR pending_reason IN ('charge_pending', 'insufficient_funds', 'processing')),
    reason                      TEXT CHECK (reason IS NULL OR reason IN
                                    ('duplicate', 'expired_uncaptured_charge', 'fraudulent', 'requested_by_customer')),
    receipt_number              TEXT,
    -- doc-only enum: spec types this `string`, the description closes the set
    status                      TEXT CHECK (status IS NULL OR status IN
                                    ('pending', 'requires_action', 'succeeded', 'failed', 'canceled'))
) STRICT;

CREATE UNIQUE INDEX refunds_by_seq        ON refunds (x_seq DESC);
CREATE INDEX refunds_by_charge         ON refunds (charge, x_seq DESC);
CREATE INDEX refunds_by_payment_intent ON refunds (payment_intent, x_seq DESC);

-- Dispute ids are `du_` at the pinned version — probed 2026-09-20, every
-- live dispute minted `du_…`, correcting data_model §4/§3.2's `dp_` (the
-- stripe-mock fixtures' older shape). dispute.balance_transactions is
-- derived from the ledger (Phase 11), not a column; until then the
-- serializer emits [].
CREATE TABLE disputes (
    id                         TEXT PRIMARY KEY,
    x_seq                      INTEGER NOT NULL,
    created                    TEXT NOT NULL,
    amount                     INTEGER NOT NULL,
    charge                     TEXT NOT NULL REFERENCES charges (id),
    currency                   TEXT NOT NULL,
    enhanced_eligibility_types TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(enhanced_eligibility_types) AND json_type(enhanced_eligibility_types) = 'array'),
    evidence                   TEXT NOT NULL CHECK (json_valid(evidence) AND json_type(evidence) = 'object'),
    evidence_details           TEXT NOT NULL CHECK (json_valid(evidence_details) AND json_type(evidence_details) = 'object'),
    is_charge_refundable       INTEGER NOT NULL CHECK (is_charge_refundable IN (0, 1)),
    metadata                   TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    payment_intent             TEXT REFERENCES payment_intents (id),
    payment_method_details     TEXT CHECK (payment_method_details IS NULL OR (json_valid(payment_method_details) AND json_type(payment_method_details) = 'object')),
    -- doc-only enum, 15 values
    reason                     TEXT NOT NULL CHECK (reason IN
                                   ('bank_cannot_process','check_returned','credit_not_processed',
                                    'customer_initiated','debit_not_authorized','duplicate','fraudulent',
                                    'general','incorrect_account_details','insufficient_funds','noncompliant',
                                    'product_not_received','product_unacceptable','subscription_canceled',
                                    'unrecognized')),
    status                     TEXT NOT NULL CHECK (status IN
                                   ('lost','needs_response','prevented','under_review','warning_closed',
                                    'warning_needs_response','warning_under_review','won'))
) STRICT;

CREATE UNIQUE INDEX disputes_by_seq        ON disputes (x_seq DESC);
CREATE INDEX disputes_by_charge         ON disputes (charge, x_seq DESC);
CREATE INDEX disputes_by_payment_intent ON disputes (payment_intent, x_seq DESC);

-- setup_intents (Phase 10, probed 2026-09-20). mandate / single_use_mandate /
-- latest_attempt are scope-boundary stubs (data_model §4): latest_attempt
-- mints a `setatt_…` id on every confirm attempt, the two mandate columns
-- stay NULL on the card rail (recorded), and none of the three ever resolves
-- to an object. attach_to_self is a stored column the recorded wire never
-- carries; the serializer leaves it unmapped.
CREATE TABLE setup_intents (
    id                     TEXT PRIMARY KEY,
    x_seq                  INTEGER NOT NULL,
    created                TEXT NOT NULL,
    attach_to_self         INTEGER NOT NULL DEFAULT 0 CHECK (attach_to_self IN (0, 1)),
    automatic_payment_methods TEXT CHECK (automatic_payment_methods IS NULL OR (json_valid(automatic_payment_methods) AND json_type(automatic_payment_methods) = 'object')),
    cancellation_reason    TEXT CHECK (cancellation_reason IS NULL OR cancellation_reason IN ('abandoned', 'duplicate', 'requested_by_customer')),
    client_secret          TEXT,
    customer               TEXT REFERENCES customers (id),
    description            TEXT,
    flow_directions        TEXT CHECK (flow_directions IS NULL OR (json_valid(flow_directions) AND json_type(flow_directions) = 'array')),
    last_setup_error       TEXT CHECK (last_setup_error IS NULL OR (json_valid(last_setup_error) AND json_type(last_setup_error) = 'object')),
    latest_attempt         TEXT,      -- stub: `setatt_…`, never resolved
    mandate                TEXT,      -- stub: `mandate_…`, never resolved
    metadata               TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    next_action            TEXT CHECK (next_action IS NULL OR (json_valid(next_action) AND json_type(next_action) = 'object')),
    payment_method         TEXT REFERENCES payment_methods (id),
    payment_method_options TEXT CHECK (payment_method_options IS NULL OR (json_valid(payment_method_options) AND json_type(payment_method_options) = 'object')),
    payment_method_types   TEXT NOT NULL DEFAULT '["card"]' CHECK (json_valid(payment_method_types) AND json_type(payment_method_types) = 'array'),
    single_use_mandate     TEXT,      -- stub
    status                 TEXT NOT NULL CHECK (status IN
                               ('canceled','processing','requires_action','requires_confirmation',
                                'requires_payment_method','succeeded')),
    -- doc-only enum: description names on_session / off_session, default off_session
    usage                  TEXT NOT NULL DEFAULT 'off_session' CHECK (usage IN ('on_session', 'off_session'))
) STRICT;

CREATE UNIQUE INDEX setup_intents_by_seq        ON setup_intents (x_seq DESC);
CREATE INDEX setup_intents_by_customer       ON setup_intents (customer, x_seq DESC);
CREATE INDEX setup_intents_by_payment_method ON setup_intents (payment_method, x_seq DESC);

-- The balance ledger and payouts (Phase 11, probed 2026-09-20). Every wire
-- fact below is pinned by cassette 05's dispute bodies, the live bt listings
-- and the Phase 11 probe round: charge bt is type `charge`, refund bt type
-- `refund` (resolving the research's legacy/newer pair question at this
-- version), both dispute rows type `adjustment` under reporting categories
-- `dispute` / `dispute_reversal`. `balance_transactions` ↔ `payouts` are the
-- fourth mutually-referential pair; both crossing columns are nullable, same
-- two-step write as the other three.

CREATE TABLE balance_transactions (
    id                 TEXT PRIMARY KEY,
    x_seq              INTEGER NOT NULL,
    created            TEXT NOT NULL,
    amount             INTEGER NOT NULL,
    available_on       TEXT NOT NULL,
    balance_type       TEXT NOT NULL DEFAULT 'payments' CHECK (balance_type IN
                           ('issuing', 'payments', 'refund_and_dispute_prefunding', 'risk_reserved')),
    currency           TEXT NOT NULL,
    description        TEXT,
    exchange_rate      TEXT,
    fee                INTEGER NOT NULL DEFAULT 0,
    fee_details        TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(fee_details) AND json_type(fee_details) = 'array'),
    net                INTEGER NOT NULL,
    reporting_category TEXT NOT NULL,   -- open string: spec declares no set and links out
    -- 16-way polymorphic reference (charge | refund | dispute | payout in scope). No FK
    -- is possible against four tables; resources/_lookup.py resolves it on expansion and
    -- an invariant test asserts every `source` resolves in exactly one table.
    source             TEXT,
    -- doc-only enum: "either available or pending"
    status             TEXT NOT NULL CHECK (status IN ('available', 'pending')),
    type               TEXT NOT NULL CHECK (type IN (
        'adjustment','advance','advance_funding','anticipation_repayment','application_fee',
        'application_fee_refund','charge','climate_order_purchase','climate_order_refund',
        'connect_collection_transfer','contribution','fee_credit_funding','inbound_transfer',
        'inbound_transfer_reversal','issuing_authorization_hold','issuing_authorization_release',
        'issuing_dispute','issuing_transaction','obligation_outbound','obligation_reversal_inbound',
        'payment','payment_failure_refund','payment_network_reserve_hold',
        'payment_network_reserve_release','payment_refund','payment_reversal','payment_unreconciled',
        'payout','payout_cancel','payout_failure','payout_minimum_balance_hold',
        'payout_minimum_balance_release','refund','refund_failure','reserve_hold','reserve_release',
        'reserve_transaction','reserved_funds','stripe_balance_payment_debit',
        'stripe_balance_payment_debit_reversal','stripe_fee','stripe_fx_fee','tax_fee','tax_fund',
        'topup','topup_reversal','transfer','transfer_cancel','transfer_failure','transfer_refund')),
    -- world-internal: the payout this row was swept into, answering
    -- GET /v1/balance_transactions?payout=… . Not an API field.
    x_payout           TEXT REFERENCES payouts (id),
    CHECK (net = amount - fee)
) STRICT;

CREATE UNIQUE INDEX balance_transactions_by_seq      ON balance_transactions (x_seq DESC);
CREATE INDEX balance_transactions_by_type         ON balance_transactions (type, x_seq DESC);
CREATE INDEX balance_transactions_by_source       ON balance_transactions (source, x_seq DESC);
CREATE INDEX balance_transactions_by_payout       ON balance_transactions (x_payout, x_seq DESC);
-- the available/pending split in §6.4 of the architecture is a scan of this index
CREATE INDEX balance_transactions_by_available_on ON balance_transactions (available_on, currency);

CREATE TABLE payouts (
    id                          TEXT PRIMARY KEY,
    x_seq                       INTEGER NOT NULL,
    created                     TEXT NOT NULL,
    amount                      INTEGER NOT NULL,
    arrival_date                TEXT NOT NULL,
    automatic                   INTEGER NOT NULL CHECK (automatic IN (0, 1)),
    balance_transaction         TEXT REFERENCES balance_transactions (id),
    currency                    TEXT NOT NULL,
    description                 TEXT,
    destination                 TEXT,   -- stub: `ba_…` / `card_…`, never resolved
    failure_balance_transaction TEXT REFERENCES balance_transactions (id),
    failure_code                TEXT,
    failure_message             TEXT,
    metadata                    TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    -- doc-only enum
    method                      TEXT NOT NULL DEFAULT 'standard' CHECK (method IN ('standard', 'instant')),
    original_payout             TEXT REFERENCES payouts (id),
    reconciliation_status       TEXT NOT NULL DEFAULT 'not_applicable'
                                  CHECK (reconciliation_status IN ('completed', 'in_progress', 'not_applicable')),
    reversed_by                 TEXT REFERENCES payouts (id),
    -- doc-only enum
    source_type                 TEXT NOT NULL DEFAULT 'bank_account' CHECK (source_type IN ('card', 'fpx', 'bank_account')),
    statement_descriptor        TEXT,
    -- doc-only enum
    status                      TEXT NOT NULL CHECK (status IN ('paid', 'pending', 'in_transit', 'canceled', 'failed')),
    type                        TEXT NOT NULL CHECK (type IN ('bank_account', 'card'))
) STRICT;

CREATE UNIQUE INDEX payouts_by_seq      ON payouts (x_seq DESC);
CREATE INDEX payouts_by_status       ON payouts (status, x_seq DESC);
