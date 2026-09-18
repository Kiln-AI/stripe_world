-- Full-text search over issues: an FTS5 index and the three triggers that keep it
-- in step with the table.
--
-- External content (`content='issues'`), so the text lives once, in `issues`, and
-- the index stores only its terms. That is what makes the triggers necessary:
-- FTS5 cannot see a write to the table it shadows, so every insert, delete and
-- update of `issues` has to tell it. The `'delete'` command rows are FTS5's own
-- protocol for retracting a document, and they carry the *old* text because that
-- is what was indexed.
--
-- None of the three reads a clock, and none of them can: a trigger that stamped a
-- timestamp would be the one place in this world where a row's time did not come
-- from `ctx.clock`.

CREATE VIRTUAL TABLE issues_fts USING fts5 (
    title,
    description,
    content = 'issues',
    content_rowid = 'rowid'
);

CREATE TRIGGER issues_fts_after_insert AFTER INSERT ON issues BEGIN
    INSERT INTO issues_fts (rowid, title, description)
    VALUES (new.rowid, new.title, new.description);
END;

CREATE TRIGGER issues_fts_after_delete AFTER DELETE ON issues BEGIN
    INSERT INTO issues_fts (issues_fts, rowid, title, description)
    VALUES ('delete', old.rowid, old.title, old.description);
END;

CREATE TRIGGER issues_fts_after_update AFTER UPDATE ON issues BEGIN
    INSERT INTO issues_fts (issues_fts, rowid, title, description)
    VALUES ('delete', old.rowid, old.title, old.description);
    INSERT INTO issues_fts (rowid, title, description)
    VALUES (new.rowid, new.title, new.description);
END;
