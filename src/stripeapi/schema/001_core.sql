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
-- core tables join in the catalog phase.

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
