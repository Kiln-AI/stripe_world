-- StripeAPI payments tables (components/data_model.md §4). Same conventions as 001_core:
-- STRICT tables, explicit primary keys, no wall-clock expression anywhere, money INTEGER
-- minor units, JSON columns guarded by json_valid + json_type. The payments tables land one
-- resource phase at a time; payment_methods is this slice's, the money-path tables join in
-- Phase 8.

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
