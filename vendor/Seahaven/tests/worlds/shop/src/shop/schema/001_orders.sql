-- STRICT, an explicit primary key, and no wall clock anywhere.
CREATE TABLE orders (
    id TEXT PRIMARY KEY,
    total INTEGER NOT NULL,
    placed_at TEXT NOT NULL
) STRICT;
