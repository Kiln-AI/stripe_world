-- STRICT, an explicit primary key, and no wall clock anywhere.
CREATE TABLE notes (
    id TEXT PRIMARY KEY,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;
