-- Full-text search over the four searchable tables whose field allowlists
-- include at least one ``string``-typed column: customers, products, prices
-- and invoices.  External-content FTS5 virtual tables (``content='<table>'``,
-- ``content_rowid='rowid'``) plus the three triggers (after insert / after
-- delete / after update) that keep the index in step.
--
-- Pattern: ``vendor/Seahaven/worlds/projecttracker/src/projecttracker/schema/
-- 002_search.sql``.  External content means the text lives once, in the
-- source table, and the index stores only its terms.  The triggers are
-- necessary because FTS5 cannot see a write to the table it shadows.  The
-- ``'delete'`` command rows are FTS5's own protocol for retracting a document
-- and they carry the *old* text because that is what was indexed.
--
-- None of the three triggers reads a clock, and none of them can: a trigger
-- that stamped a timestamp would be the one place in this world where a row's
-- time did not come from ``ctx.clock``.
--
-- Tables with only ``token`` and ``numeric`` search fields (charges,
-- payment_intents, subscriptions) have no FTS5 table — their queries resolve
-- entirely in plain SQL.

-- ── customers ─────────────────────────────────────────────────────────────

CREATE VIRTUAL TABLE customers_fts USING fts5 (
    email,
    name,
    phone,
    content = 'customers',
    content_rowid = 'rowid'
);

CREATE TRIGGER customers_fts_after_insert AFTER INSERT ON customers BEGIN
    INSERT INTO customers_fts (rowid, email, name, phone)
    VALUES (new.rowid, new.email, new.name, new.phone);
END;

CREATE TRIGGER customers_fts_after_delete AFTER DELETE ON customers BEGIN
    INSERT INTO customers_fts (customers_fts, rowid, email, name, phone)
    VALUES ('delete', old.rowid, old.email, old.name, old.phone);
END;

CREATE TRIGGER customers_fts_after_update AFTER UPDATE ON customers BEGIN
    INSERT INTO customers_fts (customers_fts, rowid, email, name, phone)
    VALUES ('delete', old.rowid, old.email, old.name, old.phone);
    INSERT INTO customers_fts (rowid, email, name, phone)
    VALUES (new.rowid, new.email, new.name, new.phone);
END;

-- ── products ──────────────────────────────────────────────────────────────

CREATE VIRTUAL TABLE products_fts USING fts5 (
    description,
    name,
    url,
    content = 'products',
    content_rowid = 'rowid'
);

CREATE TRIGGER products_fts_after_insert AFTER INSERT ON products BEGIN
    INSERT INTO products_fts (rowid, description, name, url)
    VALUES (new.rowid, new.description, new.name, new.url);
END;

CREATE TRIGGER products_fts_after_delete AFTER DELETE ON products BEGIN
    INSERT INTO products_fts (products_fts, rowid, description, name, url)
    VALUES ('delete', old.rowid, old.description, old.name, old.url);
END;

CREATE TRIGGER products_fts_after_update AFTER UPDATE ON products BEGIN
    INSERT INTO products_fts (products_fts, rowid, description, name, url)
    VALUES ('delete', old.rowid, old.description, old.name, old.url);
    INSERT INTO products_fts (rowid, description, name, url)
    VALUES (new.rowid, new.description, new.name, new.url);
END;

-- ── prices ────────────────────────────────────────────────────────────────

CREATE VIRTUAL TABLE prices_fts USING fts5 (
    lookup_key,
    product,
    content = 'prices',
    content_rowid = 'rowid'
);

CREATE TRIGGER prices_fts_after_insert AFTER INSERT ON prices BEGIN
    INSERT INTO prices_fts (rowid, lookup_key, product)
    VALUES (new.rowid, new.lookup_key, new.product);
END;

CREATE TRIGGER prices_fts_after_delete AFTER DELETE ON prices BEGIN
    INSERT INTO prices_fts (prices_fts, rowid, lookup_key, product)
    VALUES ('delete', old.rowid, old.lookup_key, old.product);
END;

CREATE TRIGGER prices_fts_after_update AFTER UPDATE ON prices BEGIN
    INSERT INTO prices_fts (prices_fts, rowid, lookup_key, product)
    VALUES ('delete', old.rowid, old.lookup_key, old.product);
    INSERT INTO prices_fts (rowid, lookup_key, product)
    VALUES (new.rowid, new.lookup_key, new.product);
END;

-- ── invoices ──────────────────────────────────────────────────────────────

-- invoice.status and invoice.subscription (column: parent_subscription) are
-- typed ``string`` in Stripe's docs (verified 2026-09-22).  Their values are
-- single-token (a closed enum and an id), so FTS5 phrase matching reduces to
-- a simple term match — correct and cheap.

CREATE VIRTUAL TABLE invoices_fts USING fts5 (
    number,
    parent_subscription,
    status,
    content = 'invoices',
    content_rowid = 'rowid'
);

CREATE TRIGGER invoices_fts_after_insert AFTER INSERT ON invoices BEGIN
    INSERT INTO invoices_fts (rowid, number, parent_subscription, status)
    VALUES (new.rowid, new.number, new.parent_subscription, new.status);
END;

CREATE TRIGGER invoices_fts_after_delete AFTER DELETE ON invoices BEGIN
    INSERT INTO invoices_fts (invoices_fts, rowid, number, parent_subscription, status)
    VALUES ('delete', old.rowid, old.number, old.parent_subscription, old.status);
END;

CREATE TRIGGER invoices_fts_after_update AFTER UPDATE ON invoices BEGIN
    INSERT INTO invoices_fts (invoices_fts, rowid, number, parent_subscription, status)
    VALUES ('delete', old.rowid, old.number, old.parent_subscription, old.status);
    INSERT INTO invoices_fts (rowid, number, parent_subscription, status)
    VALUES (new.rowid, new.number, new.parent_subscription, new.status);
END;
