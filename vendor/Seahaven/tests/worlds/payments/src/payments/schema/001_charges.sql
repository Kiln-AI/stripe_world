-- STRICT, an explicit primary key, and no wall clock anywhere.
CREATE TABLE charges (
    id TEXT PRIMARY KEY,
    amount INTEGER NOT NULL,
    currency TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;
