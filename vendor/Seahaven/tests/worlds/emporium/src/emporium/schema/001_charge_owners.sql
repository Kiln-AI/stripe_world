-- STRICT, an explicit primary key, and no wall clock anywhere.
CREATE TABLE charge_owners (
    charge_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL
) STRICT;
