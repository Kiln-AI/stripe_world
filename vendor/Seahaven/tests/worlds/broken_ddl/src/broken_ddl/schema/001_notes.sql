-- Not a table SQLite will make: `TEXTUAL` is not a type a STRICT table accepts.
CREATE TABLE notes (
    id TEXT PRIMARY KEY,
    body TEXTUAL NOT NULL
) STRICT;
