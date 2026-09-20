-- StripeAPI payments tables (components/data_model.md §4). Same conventions as 001_core:
-- STRICT tables, explicit primary keys, no wall-clock expression anywhere, money INTEGER
-- minor units, JSON columns guarded by json_valid + json_type. The payments tables land one
-- resource phase at a time: payment_methods (Phase 6), then the money path (Phase 8).

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
-- PI, satisfying non-deferred FKs at every step (data_model §4).
--
-- Recording-driven correction to data_model §4 (Phase 8, 2026-09-20): the two
-- balance-ledger columns land WITHOUT their `REFERENCES balance_transactions`
-- clauses. SQLite refuses to prepare any INSERT into a table whose FK parent
-- does not exist — NULL column values do not help — and the ledger table is
-- Phase 11's, so the verbatim clauses would make the whole money path
-- unwritable until then. The FKs join in Phase 11, when the parent exists;
-- until then both columns are NULL anyway (recorded: no ledger row exists at
-- charge time except after a manual capture, and that difference is an
-- allow-list entry).

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
    balance_transaction            TEXT,
    billing_details                TEXT NOT NULL CHECK (json_valid(billing_details) AND json_type(billing_details) = 'object'),
    calculated_statement_descriptor TEXT,
    captured                       INTEGER NOT NULL CHECK (captured IN (0, 1)),
    currency                       TEXT NOT NULL,
    customer                       TEXT REFERENCES customers (id),
    description                    TEXT,
    disputed                       INTEGER NOT NULL DEFAULT 0 CHECK (disputed IN (0, 1)),
    failure_balance_transaction    TEXT,
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
