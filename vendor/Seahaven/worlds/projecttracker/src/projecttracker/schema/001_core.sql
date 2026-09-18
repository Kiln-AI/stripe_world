-- ProjectTracker's core schema: the nine tables this world's tools read and write.
--
-- Every table is STRICT with an explicit primary key, and every timestamp is
-- canonical text (`2026-06-01T09:00:00.000Z`) written by the tool that makes the
-- row, never by a DDL default: one format across every door, and no wall-clock
-- expression anywhere in the schema.
--
-- Foreign keys are declared and enforced -- every connection this world opens
-- runs with `PRAGMA foreign_keys = ON` -- but a tool never lets SQLite be the one
-- to report a missing parent: a constraint failure arrives as a `DbError`, and
-- the error handler turns that into `INTERNAL`, which tells an agent nothing. The
-- tools look the parent up first and raise `NOT_FOUND`. The constraints are the
-- second lock on the door, for the SQL a tool has yet to be written for.

CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'member', 'viewer')),
    created_at TEXT NOT NULL
) STRICT;

-- `issue_counter` is the per-team issue-key counter, and it is on the team rather
-- than on the project because the key it mints is the team's: `ENG-41` is the
-- forty-first issue of team ENG, whichever of the team's projects it is in.
-- (`components/projecttracker.md` §1 lists the column under `projects`; §3 calls
-- it a per-team counter and §5 asks for `ENG-41` after forty issues spread over
-- two projects of one team. A per-project counter would mint `ENG-1` twice in one
-- team, which `issues.key UNIQUE` refuses outright. `phase_plans/phase_10.md`
-- records the deviation.)
CREATE TABLE teams (
    id TEXT PRIMARY KEY,
    key TEXT NOT NULL UNIQUE CHECK (
        length(key) BETWEEN 2 AND 5 AND key NOT GLOB '*[^A-Z]*'
    ),
    name TEXT NOT NULL,
    issue_counter INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE team_members (
    team_id TEXT NOT NULL REFERENCES teams (id),
    user_id TEXT NOT NULL REFERENCES users (id),
    joined_at TEXT NOT NULL,
    PRIMARY KEY (team_id, user_id)
) STRICT;

CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL REFERENCES teams (id),
    name TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('planned', 'active', 'done')),
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE issues (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects (id),
    key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('backlog', 'todo', 'in_progress', 'done', 'canceled')
    ),
    priority INTEGER NOT NULL CHECK (priority BETWEEN 0 AND 4),
    assignee_id TEXT REFERENCES users (id),
    creator_id TEXT NOT NULL REFERENCES users (id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    due_at TEXT,
    archived_at TEXT
) STRICT;

-- The five indexes: one per column `list_issues` filters on, and one per column it
-- orders by. Each ordering is a keyset scan of `(column, id)`, which is the index
-- plus the primary key SQLite appends to it.
CREATE INDEX issues_by_project ON issues (project_id);
CREATE INDEX issues_by_status ON issues (status);
CREATE INDEX issues_by_assignee ON issues (assignee_id);
CREATE INDEX issues_by_created_at ON issues (created_at);
CREATE INDEX issues_by_updated_at ON issues (updated_at);

CREATE TABLE labels (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL REFERENCES teams (id),
    name TEXT NOT NULL,
    color TEXT NOT NULL CHECK (color GLOB '#[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'),
    UNIQUE (team_id, name)
) STRICT;

CREATE TABLE issue_labels (
    issue_id TEXT NOT NULL REFERENCES issues (id),
    label_id TEXT NOT NULL REFERENCES labels (id),
    PRIMARY KEY (issue_id, label_id)
) STRICT;

CREATE TABLE comments (
    id TEXT PRIMARY KEY,
    issue_id TEXT NOT NULL REFERENCES issues (id),
    author_id TEXT NOT NULL REFERENCES users (id),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;

-- The audit trail every write to an issue appends to. `payload` is a JSON object
-- in TEXT, because a STRICT table has no JSON storage class -- the CHECK is what
-- makes the column mean JSON rather than merely hold it.
CREATE TABLE issue_events (
    id TEXT PRIMARY KEY,
    issue_id TEXT NOT NULL REFERENCES issues (id),
    actor_id TEXT NOT NULL REFERENCES users (id),
    kind TEXT NOT NULL CHECK (kind IN ('created', 'status', 'assignee', 'comment')),
    payload TEXT NOT NULL CHECK (json_valid(payload)),
    created_at TEXT NOT NULL
) STRICT;
