-- StripeAPI core resources. Every table STRICT, every table an explicit primary key,
-- no wall-clock expression anywhere: timestamps are canonical TEXT written by the
-- handler from ctx.clock.iso(). Money is INTEGER minor units. Rates are TEXT decimal
-- literals, never REAL. JSON columns are TEXT guarded by json_valid + json_type.
--
-- Foreign keys are declared (Seahaven opens every connection with foreign_keys = ON)
-- but no handler lets SQLite report a missing parent: resources/_lookup.py SELECTs the
-- parent first and raises Stripe's resource_missing. The constraints are the second lock.
--
-- The customers table is DDL verbatim from components/data_model.md §3; the other five
-- core tables join in the catalog phase, with two recording-driven corrections to that
-- DDL (Phase 7 probes, 2026-09-20, both cited where they land).

CREATE TABLE customers (
    id                    TEXT PRIMARY KEY,
    x_seq                 INTEGER NOT NULL,
    created               TEXT NOT NULL,
    deleted               INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1)),
    address               TEXT CHECK (address IS NULL OR (json_valid(address) AND json_type(address) = 'object')),
    balance               INTEGER NOT NULL DEFAULT 0,
    currency              TEXT,
    delinquent            INTEGER CHECK (delinquent IS NULL OR delinquent IN (0, 1)),
    description           TEXT,
    discount              TEXT CHECK (discount IS NULL OR (json_valid(discount) AND json_type(discount) = 'object')),
    email                 TEXT,
    invoice_prefix        TEXT NOT NULL,
    invoice_settings      TEXT NOT NULL CHECK (json_valid(invoice_settings) AND json_type(invoice_settings) = 'object'),
    metadata              TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    name                  TEXT,
    next_invoice_sequence INTEGER NOT NULL DEFAULT 1,
    phone                 TEXT,
    preferred_locales     TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(preferred_locales) AND json_type(preferred_locales) = 'array'),
    shipping              TEXT CHECK (shipping IS NULL OR (json_valid(shipping) AND json_type(shipping) = 'object')),
    tax_exempt            TEXT CHECK (tax_exempt IS NULL OR tax_exempt IN ('exempt', 'none', 'reverse'))
) STRICT;

CREATE UNIQUE INDEX customers_by_seq ON customers (x_seq DESC);
-- invoice.number is {invoice_prefix}-{next_invoice_sequence:04d}; the prefix is what makes
-- two customers' `-0001` invoices distinct, so it is unique. See components/data_model.md §3.13.
CREATE UNIQUE INDEX customers_invoice_prefix ON customers (invoice_prefix);
CREATE INDEX customers_by_email   ON customers (email, x_seq DESC);

-- products.default_price and prices.product are mutually referential. Both are
-- declared; the create path writes the product with default_price NULL, then the
-- price, then UPDATEs the product. SQLite's non-deferred FKs are satisfied at every
-- step, so no DEFERRABLE clause is needed (and none is available on a STRICT table
-- without also changing the connection's pragmas, which world code may not do).
CREATE TABLE products (
    id                   TEXT PRIMARY KEY,
    x_seq                INTEGER NOT NULL,
    created              TEXT NOT NULL,
    updated              TEXT NOT NULL,
    deleted              INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1)),
    active               INTEGER NOT NULL CHECK (active IN (0, 1)),
    default_price        TEXT REFERENCES prices (id),
    description          TEXT,
    images               TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(images) AND json_type(images) = 'array'),
    marketing_features   TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(marketing_features) AND json_type(marketing_features) = 'array'),
    metadata             TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    name                 TEXT NOT NULL,
    package_dimensions   TEXT CHECK (package_dimensions IS NULL OR (json_valid(package_dimensions) AND json_type(package_dimensions) = 'object')),
    shippable            INTEGER CHECK (shippable IS NULL OR shippable IN (0, 1)),
    statement_descriptor TEXT,
    tax_code             TEXT,
    unit_label           TEXT,
    url                  TEXT
) STRICT;

CREATE UNIQUE INDEX products_by_seq ON products (x_seq DESC);
CREATE INDEX products_by_active  ON products (active, x_seq DESC);

CREATE TABLE prices (
    id                  TEXT PRIMARY KEY,
    x_seq               INTEGER NOT NULL,
    created             TEXT NOT NULL,
    active              INTEGER NOT NULL CHECK (active IN (0, 1)),
    billing_scheme      TEXT NOT NULL CHECK (billing_scheme IN ('per_unit', 'tiered')),
    currency            TEXT NOT NULL,
    currency_options    TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(currency_options) AND json_type(currency_options) = 'object'),
    custom_unit_amount  TEXT CHECK (custom_unit_amount IS NULL OR (json_valid(custom_unit_amount) AND json_type(custom_unit_amount) = 'object')),
    lookup_key          TEXT,
    metadata            TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    nickname            TEXT,
    product             TEXT NOT NULL REFERENCES products (id),
    recurring           TEXT CHECK (recurring IS NULL OR (json_valid(recurring) AND json_type(recurring) = 'object')),
    tax_behavior        TEXT CHECK (tax_behavior IS NULL OR tax_behavior IN ('exclusive', 'inclusive', 'unspecified')),
    tiers               TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(tiers) AND json_type(tiers) = 'array'),
    tiers_mode          TEXT CHECK (tiers_mode IS NULL OR tiers_mode IN ('graduated', 'volume')),
    transform_quantity  TEXT CHECK (transform_quantity IS NULL OR (json_valid(transform_quantity) AND json_type(transform_quantity) = 'object')),
    type                TEXT NOT NULL CHECK (type IN ('one_time', 'recurring')),
    unit_amount         INTEGER,
    unit_amount_decimal TEXT,
    CHECK ((type = 'recurring') = (recurring IS NOT NULL))
) STRICT;

CREATE UNIQUE INDEX prices_by_seq ON prices (x_seq DESC);
CREATE INDEX prices_by_product ON prices (product, x_seq DESC);
CREATE INDEX prices_by_active  ON prices (active, x_seq DESC);
-- Recording-driven correction to data_model §3 (Phase 7 probe, 2026-09-20): a
-- lookup_key conflicts even when its holder is inactive (probed: reuse from a
-- deactivated holder answers "A price (`…`) already uses that lookup key."), so
-- the uniqueness is over every holder, not the live ones the design assumed.
-- `transfer_lookup_key` dodges it by clearing the holder's key first (probed).
CREATE UNIQUE INDEX prices_lookup_key ON prices (lookup_key) WHERE lookup_key IS NOT NULL;

CREATE TABLE coupons (
    id                 TEXT PRIMARY KEY,
    x_seq              INTEGER NOT NULL,
    created            TEXT NOT NULL,
    deleted            INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1)),
    amount_off         INTEGER,
    applies_to         TEXT CHECK (applies_to IS NULL OR (json_valid(applies_to) AND json_type(applies_to) = 'object')),
    currency           TEXT,
    currency_options   TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(currency_options) AND json_type(currency_options) = 'object'),
    duration           TEXT NOT NULL CHECK (duration IN ('forever', 'once', 'repeating')),
    duration_in_months INTEGER,
    max_redemptions    INTEGER,
    metadata           TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    name               TEXT,
    percent_off        TEXT,
    redeem_by          TEXT,
    times_redeemed     INTEGER NOT NULL DEFAULT 0,
    valid              INTEGER NOT NULL CHECK (valid IN (0, 1)),
    CHECK ((amount_off IS NULL) <> (percent_off IS NULL)),
    CHECK ((amount_off IS NULL) OR (currency IS NOT NULL)),
    CHECK ((duration = 'repeating') = (duration_in_months IS NOT NULL))
) STRICT;

CREATE UNIQUE INDEX coupons_by_seq ON coupons (x_seq DESC);

-- `valid` is computed by Stripe from redeem_by vs now and times_redeemed vs
-- max_redemptions. It is a stored column rather than a generated one because the
-- redeem_by half needs `now`, and SH103 forbids a clock expression in a generated
-- column. resources/coupons.py recomputes it on every write that can change it; the
-- clock is frozen, so a row written once stays correct for the instance's life.

CREATE TABLE promotion_codes (
    id              TEXT PRIMARY KEY,
    x_seq           INTEGER NOT NULL,
    created         TEXT NOT NULL,
    active          INTEGER NOT NULL CHECK (active IN (0, 1)),
    code            TEXT NOT NULL,
    coupon          TEXT NOT NULL REFERENCES coupons (id),
    customer        TEXT REFERENCES customers (id),
    expires_at      TEXT,
    max_redemptions INTEGER,
    metadata        TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    restrictions    TEXT NOT NULL CHECK (json_valid(restrictions) AND json_type(restrictions) = 'object'),
    times_redeemed  INTEGER NOT NULL DEFAULT 0
) STRICT;

CREATE UNIQUE INDEX promotion_codes_by_seq  ON promotion_codes (x_seq DESC);
CREATE INDEX promotion_codes_by_code     ON promotion_codes (code, x_seq DESC);
CREATE INDEX promotion_codes_by_coupon   ON promotion_codes (coupon, x_seq DESC);
CREATE INDEX promotion_codes_by_customer ON promotion_codes (customer, x_seq DESC);
-- The recorded refusal names active codes ("An active promotion code with
-- `code: X` already exists.", Phase 7 probe), so the uniqueness is partial on
-- active — the one behavior difference from prices' lookup_key above.
CREATE UNIQUE INDEX promotion_codes_active_code ON promotion_codes (code) WHERE active = 1;

CREATE TABLE tax_rates (
    id                   TEXT PRIMARY KEY,
    x_seq                INTEGER NOT NULL,
    created              TEXT NOT NULL,
    active               INTEGER NOT NULL CHECK (active IN (0, 1)),
    country              TEXT,
    description          TEXT,
    display_name         TEXT NOT NULL,
    effective_percentage TEXT,
    flat_amount          TEXT CHECK (flat_amount IS NULL OR (json_valid(flat_amount) AND json_type(flat_amount) = 'object')),
    inclusive            INTEGER NOT NULL CHECK (inclusive IN (0, 1)),
    jurisdiction         TEXT,
    jurisdiction_level   TEXT CHECK (jurisdiction_level IS NULL OR jurisdiction_level IN
                             ('city', 'country', 'county', 'district', 'multiple', 'state')),
    metadata             TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    percentage           TEXT NOT NULL,
    rate_type            TEXT CHECK (rate_type IS NULL OR rate_type IN ('flat_amount', 'percentage')),
    state                TEXT,
    tax_type             TEXT CHECK (tax_type IS NULL OR tax_type IN
                             ('amusement_tax', 'communications_tax', 'gst', 'hst', 'igst', 'jct',
                              'lease_tax', 'mass_transit_parking_tax', 'parking_tax', 'pst', 'qst',
                              'retail_delivery_fee', 'rst', 'sales_tax', 'service_tax', 'vat'))
) STRICT;

CREATE UNIQUE INDEX tax_rates_by_seq ON tax_rates (x_seq DESC);
CREATE INDEX tax_rates_by_active  ON tax_rates (active, x_seq DESC);
