"""Opening world databases, and the wrapper world code holds as `ctx.db`.

A convenience layer and one error type, not a barrier: world code is trusted, the
raw connection is public, and nothing here knows what a tool is. The containment
that matters is `sandbox.py`, and it applies to SQL an *agent* wrote.

Three doors are opened here. `build_blank` writes a fresh database from a world's
DDL, `open_instance` opens the writable connection an instance serves calls from,
and `open_inspection` opens a second, read-only view of the same file -- and of
every other node's file, attached -- for looking at state without disturbing it.
All three carry the clock and a seeded `random()`, so nothing a world's SQL runs
reads the host. `build_blank` runs before the instance exists, so its caller
derives the instant and the seed first and hands them over; what comes back is a
plain connection, because the overrides belong to the build and a world's schema
is not a door anyone keeps open.
"""

import re
import string
from collections.abc import Iterable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any, Literal, NamedTuple, cast
from urllib.parse import quote

import apsw

from seahaven.clock import Clock, register_clock_functions
from seahaven.errors import DbError, WorldBug
from seahaven.ids import BUILD_STREAM, INSTANCE_STREAM, register_random_functions

__all__ = [
    "SCHEMA_CHECK_CLOCK",
    "SCHEMA_CHECK_SEED",
    "Db",
    "Exec",
    "SqlValue",
    "build_blank",
    "open_inspection",
    "open_instance",
    "shadow_tables",
    "world_tables",
]

# In SQLite's own order: NULL, INTEGER, REAL, TEXT, BLOB, its five storage classes.
type SqlValue = None | int | float | str | bytes  # noqa: RUF036

# The one path `build_blank` accepts that is not a file.
MEMORY = ":memory:"

_FTS5 = re.compile(r"\busing\s+fts5\b", re.IGNORECASE)

# SQLite folds identifiers on ASCII only. `str.lower()` also folds Unicode, and a
# Unicode fold can map a name SQLite treats as distinct onto an allowlisted one,
# so the fold here is exactly SQLite's and no wider. `sandbox.py` imports it for
# its table and function allowlists: one rule for the package, and private
# because it is not part of what `seahaven` offers a world.
_ASCII_FOLD = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def _fold_ascii(name: str) -> str:
    """A SQL name as SQLite compares it: folded on ASCII, and only on ASCII."""
    return name.translate(_ASCII_FOLD)


# Authorizer action codes that change a database or its schema. An inspection
# connection is opened read-only as well; this is the second lock on the door,
# and it turns SQLite's terse "attempt to write a readonly database" into a
# refusal of the action itself, before anything runs.
_DENIED_ON_INSPECTION = frozenset(
    {
        apsw.SQLITE_ALTER_TABLE,
        apsw.SQLITE_ANALYZE,
        apsw.SQLITE_ATTACH,
        apsw.SQLITE_CREATE_INDEX,
        apsw.SQLITE_CREATE_TABLE,
        apsw.SQLITE_CREATE_TEMP_INDEX,
        apsw.SQLITE_CREATE_TEMP_TABLE,
        apsw.SQLITE_CREATE_TEMP_TRIGGER,
        apsw.SQLITE_CREATE_TEMP_VIEW,
        apsw.SQLITE_CREATE_TRIGGER,
        apsw.SQLITE_CREATE_VIEW,
        apsw.SQLITE_CREATE_VTABLE,
        apsw.SQLITE_DELETE,
        apsw.SQLITE_DETACH,
        apsw.SQLITE_DROP_INDEX,
        apsw.SQLITE_DROP_TABLE,
        apsw.SQLITE_DROP_TEMP_INDEX,
        apsw.SQLITE_DROP_TEMP_TABLE,
        apsw.SQLITE_DROP_TEMP_TRIGGER,
        apsw.SQLITE_DROP_TEMP_VIEW,
        apsw.SQLITE_DROP_TRIGGER,
        apsw.SQLITE_DROP_VIEW,
        apsw.SQLITE_DROP_VTABLE,
        apsw.SQLITE_INSERT,
        apsw.SQLITE_REINDEX,
        apsw.SQLITE_UPDATE,
    }
)


# Pragmas an inspection connection may still run: introspection with no setter
# form at all, so naming one cannot let a write through.
_READ_ONLY_PRAGMAS = frozenset(
    {
        "collation_list",
        "compile_options",
        "data_version",
        "database_list",
        "foreign_key_check",
        "foreign_key_list",
        "freelist_count",
        "function_list",
        "index_info",
        "index_list",
        "index_xinfo",
        "integrity_check",
        "module_list",
        "page_count",
        "pragma_list",
        "quick_check",
        "table_info",
        "table_list",
        "table_xinfo",
    }
)


class Exec(NamedTuple):
    """What a statement did: rows changed, and SQLite's last inserted rowid.

    `last_rowid` is `sqlite3_last_insert_rowid` as SQLite reports it, which
    belongs to the connection rather than to the statement: it is `0` until the
    connection's first insert, and after a statement that inserted nothing it
    still names the previous insert. Read it straight after an `INSERT`, as
    SQLite's own documentation says.

    It is an `int`, never `None`: SQLite always has an answer, `0` is a legal
    rowid rather than an absence, and an optional here would put an unreachable
    `assert` in front of every use of it in world code.
    """

    rowcount: int
    last_rowid: int


class Db:
    """The connection wrapper world code sees as `ctx.db`."""

    def __init__(self, conn: apsw.Connection, helper: apsw.Connection | None = None) -> None:
        self._conn = conn
        # The private connection the clock overrides evaluate on. Owned here so
        # that closing the instance closes both.
        self._helper = helper

    @property
    def conn(self) -> apsw.Connection:
        """The raw APSW connection, for what the wrapper does not cover.

        Errors raised through it are APSW's, not `DbError`. Do not close it,
        change its pragmas or its authorizer, or open a second connection to the
        same file: the clock and randomness functions, the change log's per-call
        session and the per-call transaction all run on this connection.
        """
        return self._conn

    @property
    def in_transaction(self) -> bool:
        return self._conn.in_transaction

    def one(self, sql: str, *params: SqlValue) -> dict[str, Any] | None:
        """The first row as a dict, or `None`. Stops the statement where it is."""
        with _as_db_error():
            cursor = self._conn.execute(sql, params)
            try:
                return next(_dicts(cursor), None)
            finally:
                # The statement is left mid-step; retire it now rather than
                # leaving it to refcounting inside the caller's transaction.
                # Deleting this is an equivalent mutant under CPython, where this
                # frame holds the cursor's last reference and it is finalised as
                # the frame goes: what the line buys is a statement retired at a
                # moment this module chooses rather than one the interpreter's
                # collector chooses. `test_one_leaves_no_statement_in_flight`
                # states the property and passes either way.
                cursor.close(force=True)

    def rows(self, sql: str, *params: SqlValue) -> list[dict[str, Any]]:
        """Every row as a dict.

        Duplicate column names in a `SELECT` keep the last, as SQLite's own row
        factories do; alias them when it matters.
        """
        with _as_db_error():
            return list(_dicts(self._conn.execute(sql, params)))

    def execute(self, sql: str, *params: SqlValue) -> Exec:
        """Run SQL for its effect, discarding any rows it returns.

        More than one statement runs as one call, and the `Exec` then describes
        the last of them. The one-statement-per-call rule is the sandbox's, and
        it applies to SQL an agent wrote, not to a world's own.
        """
        with _as_db_error():
            for _ in self._conn.execute(sql, params):
                pass
            return Exec(self._conn.changes(), self._conn.last_insert_rowid())

    def executemany(self, sql: str, rows: Iterable[Sequence[SqlValue]]) -> int:
        """Run one statement once per row of bindings; returns the rows changed."""
        with _as_db_error():
            before = self._conn.total_changes()
            for _ in self._conn.executemany(sql, rows):
                pass
            return self._conn.total_changes() - before

    def transaction(self) -> AbstractContextManager[None]:
        """BEGIN at the top level, SAVEPOINT when nested; ROLLBACK on an exception.

        APSW's connection context manager is the mechanism, which is also what
        the per-call transaction uses one layer up, so a tool's own
        `db.transaction()` nests inside it as a savepoint.
        """
        return self._transaction()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        # Only the BEGIN and the COMMIT are wrapped as `DbError`. An exception
        # from the body is the caller's and reaches them exactly as raised,
        # including an `apsw.Error` from the raw connection.
        with _as_db_error():
            self._conn.__enter__()
        try:
            yield
        except BaseException as error:
            self._conn.__exit__(type(error), error, error.__traceback__)
            raise
        with _as_db_error():
            self._conn.__exit__(None, None, None)

    def close(self) -> None:
        try:
            self._conn.close()
        finally:
            # The helper is released whatever the world connection's close does.
            # That close can raise -- not on outstanding statements, which apsw
            # 3.53 closes over without complaint (checked with a mid-step cursor,
            # an open blob and a backup in flight), but `sqlite3_close` can still
            # fail -- and the helper must not be the casualty: nothing else
            # closes it. It is not garbage either, whatever its `:memory:` name
            # suggests, because every override in `_FUNCTIONS` is a closure over
            # it (the three in `_CONSTANTS` are not -- they hold a string read at
            # registration -- but seven closures are enough), so a `Db` that does
            # not close it leaves a live SQLite connection per instance.
            # `test_closing_the_database_closes_the_clock_helper` pins it.
            if self._helper is not None:
                self._helper.close()


def open_instance(path: Path, clock: Clock, seed: bytes) -> Db:
    """Open the writable connection an instance serves its tool calls from."""
    conn = apsw.Connection(str(path))
    _harden(conn)
    conn.pragma("journal_mode", "WAL")
    conn.pragma("synchronous", "NORMAL")
    # One writer per instance by construction, so waiting on a lock can only
    # mean a bug. Fail loudly instead of hanging. Said rather than left to the
    # default: APSW opens a connection with no busy handler at all, so deleting
    # this line is an equivalent mutant and the whole suite stays green under it.
    # What is pinned is the value -- a timeout long enough to matter fails
    # `test_a_second_writer_fails_instead_of_waiting`, five seconds of waiting
    # against its half-second bound -- and the line is here anyway because a
    # default nobody stated is one that can change under the project with nothing
    # to notice.
    conn.set_busy_timeout(0)
    # SQLite's own sqlite3_limit defaults are left alone: world code is trusted,
    # and the sandbox lowers the value cap for the length of an agent statement.

    register_random_functions(conn, seed, INSTANCE_STREAM)
    return Db(conn, register_clock_functions(conn, clock))


def open_inspection(
    path: Path,
    clock: Clock,
    seed: bytes,
    stream: bytes,
    attachments: Sequence[tuple[str, Path]] = (),
) -> Db:
    """Open a second, read-only view of an instance for looking at its state.

    `stream` names which read-only door this is -- `INSPECTION_STREAM` for the
    handle a caller reads state through, `CONTROL_STREAM` for the control tool's
    own -- so that two doors onto one instance do not hand out the same random
    values. See `ids.register_random_functions`. One door is one stream whatever
    it is attached to: a composite instance's read-only handles each draw from a
    single stream of their own, not one per node, since the nodes are schemas on
    one connection.

    `attachments` are `(schema name, file)` pairs -- a composite instance's added
    nodes, under the schema names their paths derive -- and this is the one place
    in the framework where two nodes' files meet. The schema name is *bound*, so
    no identifier is ever interpolated into the statement.

    The order is load-bearing. `_deny_writes` denies `SQLITE_ATTACH`, so every
    attach has to happen before it is installed and none can happen after: that
    single ordering is what makes the cross-node view possible and what closes
    it, and it is why this list cannot grow once the connection is open.
    """
    conn = apsw.Connection(
        _read_only_uri(path), flags=apsw.SQLITE_OPEN_READONLY | apsw.SQLITE_OPEN_URI
    )
    _harden(conn)
    register_random_functions(conn, seed, stream)
    helper = register_clock_functions(conn, clock)
    for schema, attached in attachments:
        conn.execute("ATTACH DATABASE ? AS ?", (_read_only_uri(attached), schema))
    # Installed once and for the connection's life: nothing toggles this one.
    # The sandbox's authorizer replaces an authorizer rather than stacking on it,
    # so an inspection connection is never a door the sandbox opens.
    conn.authorizer = _deny_writes
    return Db(conn, helper)


def _read_only_uri(path: Path) -> str:
    """A file as SQLite opens it for reading and nothing else."""
    return f"file:{quote(Path(path).as_posix())}?mode=ro"


# What a build with no instance behind it runs on. `world.py` proves the DDL
# executes, `lint/ddl.py` interrogates the schema and `conformance.py` compares
# against it; none of the three keeps a row, and all three would otherwise read
# the host to throw the answer away. They are passed explicitly at each call
# site rather than defaulted here, so that a caller who does have an instance
# cannot reach them by forgetting.
SCHEMA_CHECK_CLOCK = Clock.from_iso("1970-01-01T00:00:00.000Z")
SCHEMA_CHECK_SEED = b"schema check"


def build_blank(
    path: Path | Literal[":memory:"], ddl: str, *, clock: Clock, seed: bytes
) -> apsw.Connection:
    """Create a database holding a world's schema and nothing else.

    Returns the open connection. Pass `":memory:"` to build one for comparison
    without touching the filesystem. The framework owns no tables of its own, so
    what comes back is exactly what the DDL says.

    A schema file may seed reference rows, and that DML runs here rather than on
    an instance's connection: `clock` and `seed` are what make it replay. `seed`
    is the node's, drawn from `BUILD_STREAM` so that a row the schema seeded and
    the first value the instance's own connection draws are different bytes.

    The connection that comes back carries neither override. Nothing can restore
    SQLite's own `random()` on a connection that has shadowed it -- registering
    `None` over an override leaves the name unresolvable rather than built in --
    so the build runs on a connection of its own, and what the caller gets is a
    second, plain connection onto what that one wrote.
    """
    if path != MEMORY and Path(path).exists():
        raise WorldBug(f"refusing to build a blank database over an existing file: {path}")
    try:
        return _build(path, ddl, clock, seed)
    except Exception:
        # SQLite created the file the moment the connection opened. Leaving it
        # there would turn the next attempt at this path into "refusing to build
        # over an existing file", hiding the error the DDL actually has.
        if path != MEMORY:
            Path(path).unlink(missing_ok=True)
        raise


def _build(
    path: Path | Literal[":memory:"], ddl: str, clock: Clock, seed: bytes
) -> apsw.Connection:
    """Run the DDL on a connection carrying the overrides; hand back a plain one."""
    builder = apsw.Connection(str(path))
    try:
        helper = register_clock_functions(builder, clock)
        try:
            register_random_functions(builder, seed, BUILD_STREAM)
            builder.pragma("foreign_keys", "ON")
            with builder:
                # Iterated, not just executed: APSW runs a multi-statement string
                # lazily, stopping at the first statement that returns a row, so a
                # DDL file with a `SELECT` or a `RETURNING` in it would leave
                # everything below that statement unrun and report nothing.
                for _ in builder.execute(ddl):
                    pass
            return _plain_connection(path, builder)
        finally:
            # The helper is nothing else's and nothing else closes it, so it goes
            # whatever the build did -- and whatever closing it does, the builder
            # goes too: it holds the file open, and the caller is about to unlink
            # it on the failure path.
            helper.close()
    finally:
        builder.close()


def _plain_connection(
    path: Path | Literal[":memory:"], builder: apsw.Connection
) -> apsw.Connection:
    """A second connection onto what `builder` wrote, with none of its overrides.

    A file is simply reopened. `":memory:"` has no path to reopen, so the pages
    are carried across instead -- `serialize` names the built database whole and
    `deserialize` makes it this connection's, which is the same database rather
    than a second build of it.
    """
    conn = apsw.Connection(str(path))
    try:
        if path == MEMORY:
            conn.deserialize("main", builder.serialize("main"))
        conn.pragma("foreign_keys", "ON")
    except Exception:
        conn.close()
        raise
    return conn


def shadow_tables(conn: apsw.Connection) -> frozenset[str]:
    """The tables FTS5 keeps for itself.

    Derived by prefix from the virtual tables rather than from a fixed list of
    five, because `content=` and `columnsize=` change which shadow tables exist.
    A world table whose name collides with one is refused by the DDL check, which
    is the only ambiguity the prefix rule has.
    """
    tables = _master_tables(conn)
    prefixes = tuple(f"{name}_" for name, sql in tables if _FTS5.search(sql))
    return frozenset(name for name, _ in tables if name.startswith(prefixes))


def world_tables(conn: apsw.Connection) -> list[str]:
    """Every table the world owns, in name order.

    An FTS5 virtual table is the world's and is listed; its shadow tables and
    SQLite's own are not.
    """
    shadow = shadow_tables(conn)
    return [
        name
        for name, _ in _master_tables(conn)
        if not name.startswith("sqlite_") and name not in shadow
    ]


def _master_tables(conn: apsw.Connection) -> list[tuple[str, str]]:
    """`(name, sql)` for every table in `sqlite_master`, in name order.

    A table SQLite wrote itself has no `sql`, which arrives as empty text. The
    cast says what the schema table holds; APSW types every column as any SQLite
    value.
    """
    rows = conn.execute(
        "SELECT name, ifnull(sql, '') FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    return cast(list[tuple[str, str]], rows)


def _harden(conn: apsw.Connection) -> None:
    conn.pragma("foreign_keys", "ON")
    conn.config(apsw.SQLITE_DBCONFIG_DEFENSIVE, 1)
    # Off, so a function reached from a DEFAULT clause or a trigger must be
    # marked INNOCUOUS. That is what stops a schema smuggling in a wall clock.
    conn.config(apsw.SQLITE_DBCONFIG_TRUSTED_SCHEMA, 0)
    conn.enable_load_extension(False)


def _deny_writes(
    action: int,
    third: str | None,
    fourth: str | None,
    database: str | None,
    trigger: str | None,
    /,
) -> int:
    if action in _DENIED_ON_INSPECTION:
        return apsw.SQLITE_DENY
    # A pragma is judged by name, not by shape: the authorizer cannot tell
    # `PRAGMA journal_mode = DELETE` from `PRAGMA table_info('notes')`, since a
    # value and an argument arrive in the same place. So the ones that only
    # report are named, and everything else -- settable, or acting, like
    # `optimize` and `wal_checkpoint` -- is refused.
    if action == apsw.SQLITE_PRAGMA and (
        third is None or _fold_ascii(third) not in _READ_ONLY_PRAGMAS
    ):
        return apsw.SQLITE_DENY
    return apsw.SQLITE_OK


def _dicts(cursor: apsw.Cursor) -> Iterator[dict[str, Any]]:
    """Rows as dicts, named from the statement's description.

    Iterating an APSW cursor yields a tuple per row, one column or many (the bare
    value a single-column row can arrive as belongs to `Cursor.get`, which
    nothing here uses). Checked against apsw 3.53, the floor this project pins.
    """
    names: list[str] | None = None
    for row in cursor:
        if names is None:
            names = [name for name, _declared_type in cursor.get_description()]
        yield dict(zip(names, row, strict=True))


@contextmanager
def _as_db_error() -> Iterator[None]:
    try:
        yield
    except apsw.Error as error:
        raise DbError(str(error), getattr(error, "extendedresult", None)) from error
