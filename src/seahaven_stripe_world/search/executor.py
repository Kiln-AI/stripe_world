"""Execute a parsed search query against SQLite and return the search-result envelope.

The executor turns ``Clause`` objects (from the parser) into SQL WHERE clauses
using the ``SearchSpec`` field definitions (from fields.py). Token and numeric
fields are fully resolved in SQL. String-type phrase matching for the ``:``
operator routes through FTS5 ``MATCH`` with phrase queries, so every clause
resolves in one SQL layer with no Python post-filter.

Ranking and pagination
~~~~~~~~~~~~~~~~~~~~~~
ProjectTracker's search (``vendor/Seahaven/…/tools/search.py``) deliberately
has **no cursor**, because ``bm25()`` is a score relative to one query string
against one index and a keyset cursor cut from it would only be valid for that
exact query. Stripe's search API **requires** ``page`` / ``next_page`` cursor
pagination.

Resolution: ``next_page`` is an *opaque, server-issued* token (pagination.md
§2 — "you do not construct it yourself the way you do ``starting_after``"),
so it legitimately encodes an offset together with a query fingerprint. The
caller repeats the same ``query`` string on each page request and the ranking
is therefore stable across that page sequence. The ordering is made total by
breaking ``rank`` ties on ``id`` (the same strategy ProjectTracker uses with
``issues.id``), so two runs of one query agree row for row.

When no FTS5 clause is active (pure token/numeric/metadata/substring queries),
ranking is undefined and results are ordered by ``x_seq DESC`` (reverse
chronological) with an ``x_seq``-based keyset cursor, identical to the list
endpoints.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
from collections.abc import Callable, Mapping
from typing import Any, Final

import seahaven

from seahaven_stripe_world import _time
from seahaven_stripe_world.search.fields import SearchField, SearchSpec
from seahaven_stripe_world.search.parser import Clause, Combinator, Operator
from seahaven_stripe_world.stripe_errors import invalid_request

__all__ = ["execute"]

_TOTAL_COUNT_CAP: Final = 10_000
_DEFAULT_LIMIT: Final = 10
_MAX_LIMIT: Final = 100
_MIN_SUBSTR: Final = 3


def execute(
    ctx: seahaven.Ctx,
    spec: SearchSpec,
    clauses: list[Clause],
    combinator: Combinator,
    serializer: Callable[[seahaven.Ctx, Mapping[str, Any]], dict[str, Any]],
    *,
    query_string: str,
    limit: int | None = None,
    page_cursor: str | None = None,
    include_total_count: bool = False,
) -> dict[str, Any]:
    """Run a search query and return the ``search_result`` envelope."""
    effective_limit = _clamp_limit(limit)

    # Build the field lookup.
    field_map: dict[str, SearchField] = {f.name: f for f in spec.fields}

    # ── Classify and compile clauses ──────────────────────────────────────
    #
    # Each clause compiles into one of three buckets:
    #   sql_parts  — SQL WHERE fragments for token, numeric, metadata,
    #                string-substr, string-null, and negated-FTS subqueries
    #   fts_terms  — FTS5 MATCH terms for non-negated string-exact clauses
    #                (combined into one MATCH for the JOIN approach)
    #   Both buckets produce bind parameters.

    sql_parts: list[str] = []
    sql_binds: list[Any] = []
    fts_terms: list[str] = []  # non-negated FTS5 column-scoped queries

    for clause in clauses:
        _compile_clause(clause, field_map, spec, sql_parts, sql_binds, fts_terms)

    # Whether the FTS5 JOIN path is active: requires a non-negated
    # string-exact clause and an FTS5 table on the spec.
    use_fts_join = bool(fts_terms) and spec.fts_table is not None

    # ── Build the query ───────────────────────────────────────────────────

    t = spec.table
    base_where = _soft_delete_filter(t)
    joiner = " AND " if combinator == Combinator.AND else " OR "

    if use_fts_join:
        # Combine FTS5 terms with the combinator.  For AND this is
        # ``term1 AND term2``; for OR ``term1 OR term2``.
        fts_match_str = f" {combinator.value} ".join(fts_terms)

        if combinator == Combinator.AND:
            # JOIN: the MATCH condition lives in the JOIN, and the SQL
            # parts (token/numeric/metadata/negated-FTS) are in the WHERE.
            select = (
                f"SELECT {t}.*, bm25({spec.fts_table}) AS _rank "
                f"FROM {t} "
                f"INNER JOIN {spec.fts_table} "
                f"ON {t}.rowid = {spec.fts_table}.rowid "
                f"WHERE {spec.fts_table} MATCH ?"
            )
            binds: list[Any] = [fts_match_str]

            where_extra = _combine_parts(sql_parts, " AND ")
            if where_extra:
                select += f" AND {where_extra}"
                binds.extend(sql_binds)
            if base_where:
                select += f" AND {base_where}"

            order_by = f"ORDER BY {spec.fts_table}.rank, {t}.id"
        else:
            # OR with FTS: the FTS MATCH is one arm of the OR, combined
            # with the SQL parts.  Cannot use JOIN (would exclude rows
            # matching only non-FTS clauses), so use a subquery.
            fts_sub = (
                f"{t}.rowid IN (SELECT rowid FROM {spec.fts_table} WHERE {spec.fts_table} MATCH ?)"
            )
            all_parts = [f"({fts_sub})"] + [f"({p})" for p in sql_parts]
            combined = " OR ".join(all_parts)
            if base_where:
                combined = f"{base_where} AND ({combined})"

            select = f"SELECT {t}.* FROM {t} WHERE {combined}"
            binds = [fts_match_str, *list(sql_binds)]
            order_by = f"ORDER BY {t}.x_seq DESC"
            # OR path falls back to x_seq ordering — no bm25 available.
            use_fts_join = False
    else:
        # Pure SQL: no FTS5 involvement.
        combined_sql = _combine_parts(sql_parts, joiner)
        if combined_sql and base_where:
            where_clause = f"{base_where} AND ({combined_sql})"
        elif combined_sql:
            where_clause = combined_sql
        elif base_where:
            where_clause = base_where
        else:
            where_clause = "1=1"

        select = f"SELECT {t}.* FROM {t} WHERE {where_clause}"
        binds = list(sql_binds)
        order_by = f"ORDER BY {t}.x_seq DESC"

    # ── total_count ───────────────────────────────────────────────────────
    #
    # ``total_count`` is opt-in via ``expand[]=total_count`` on search
    # endpoints (functional_spec §6.2).  Only compute it when requested.

    total_count: int | None = None
    if include_total_count:
        # Wrap the matching query in a COUNT(*).  This counts all true
        # matches, uncapped, then we clamp to 10 000.
        count_sql = f"SELECT COUNT(*) AS cnt FROM ({select}) _tc"
        count_row = ctx.db.one(count_sql, *binds)
        raw_count = int(count_row["cnt"]) if count_row else 0
        total_count = min(raw_count, _TOTAL_COUNT_CAP)

    # ── Pagination ────────────────────────────────────────────────────────
    #
    # Two modes, determined by whether the ordering uses bm25:
    #
    # 1. bm25 ranking (use_fts_join is True): offset-based pagination.
    #    The cursor encodes ``off:<offset>:<query_hash>`` — the query hash
    #    prevents using a cursor from one query against a different one.
    #
    # 2. x_seq DESC (use_fts_join is False): keyset cursor on x_seq.
    #    The cursor encodes ``seq:<x_seq>``, the same mechanism as list
    #    endpoints.

    if use_fts_join:
        # bm25 path: offset-based pagination.
        # The query fingerprint covers the original query string and resource,
        # so a cursor from one search cannot be replayed against a different one
        # — including queries that share FTS terms but differ in SQL filters.
        q_fingerprint = _query_fingerprint(query_string, spec.table)

        offset = 0
        if page_cursor is not None:
            offset = _decode_offset_cursor(page_cursor, q_fingerprint)

        query_sql = f"{select} {order_by} LIMIT ? OFFSET ?"
        rows = ctx.db.rows(query_sql, *binds, effective_limit + 1, offset)

        has_more = len(rows) > effective_limit
        page_rows = list(rows[:effective_limit])

        next_page: str | None = None
        if has_more:
            next_page = _encode_offset_cursor(offset + effective_limit, q_fingerprint)
    else:
        # x_seq path: keyset cursor.
        if page_cursor is not None:
            cursor_seq = _decode_seq_cursor(page_cursor)
            select += f" AND {t}.x_seq < {cursor_seq}"

        query_sql = f"{select} {order_by} LIMIT ?"
        rows = ctx.db.rows(query_sql, *binds, effective_limit + 1)

        has_more = len(rows) > effective_limit
        page_rows = list(rows[:effective_limit])

        next_page = None
        if has_more and page_rows:
            last_seq = int(page_rows[-1]["x_seq"])
            next_page = _encode_seq_cursor(last_seq)

    # ── Serialize ─────────────────────────────────────────────────────────

    data = [serializer(ctx, row) for row in page_rows]

    result: dict[str, Any] = {
        "object": "search_result",
        "data": data,
        "has_more": has_more,
        "next_page": next_page,
        "url": spec.url,
    }
    # Only include ``total_count`` when opted-in via expand (§6.2).  When
    # not expanded, the key is absent entirely — not ``null``.
    if include_total_count:
        result["total_count"] = total_count

    return result


def _combine_parts(parts: list[str], joiner: str) -> str:
    """Combine SQL WHERE fragments with the given joiner."""
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return joiner.join(f"({part})" for part in parts)


# --- Clause compilation ------------------------------------------------------


def _compile_clause(
    clause: Clause,
    field_map: dict[str, SearchField],
    spec: SearchSpec,
    sql_parts: list[str],
    sql_binds: list[Any],
    fts_terms: list[str],
) -> None:
    """Compile one clause into either a SQL fragment or an FTS5 term."""
    # Metadata is handled generically.
    if clause.field == "metadata":
        if not spec.has_metadata:
            raise invalid_request(
                f"Unsupported search field: '{clause.field}'.",
                code="invalid_query",
                pre_execution=True,
            )
        _compile_metadata(clause, sql_parts, sql_binds)
        return

    field = field_map.get(clause.field)
    if field is None:
        raise invalid_request(
            f"Unsupported search field: '{clause.field}'.",
            code="invalid_query",
            pre_execution=True,
        )

    # Validate operator against field type.
    _validate_operator(clause, field)

    if field.type == "token":
        _compile_token(clause, field, sql_parts, sql_binds)
    elif field.type == "numeric":
        _compile_numeric(clause, field, sql_parts, sql_binds)
    elif field.type == "string":
        _compile_string(clause, field, spec, sql_parts, sql_binds, fts_terms)


def _validate_operator(clause: Clause, field: SearchField) -> None:
    """Check that the operator is legal for the field's type."""
    op = clause.operator
    if field.type == "token":
        if op != Operator.EXACT:
            raise invalid_request(
                f"Unsupported operator for field '{clause.field}'.",
                code="invalid_query",
                pre_execution=True,
            )
    elif field.type == "string":
        if op not in (Operator.EXACT, Operator.SUBSTR):
            raise invalid_request(
                f"Unsupported operator for field '{clause.field}'.",
                code="invalid_query",
                pre_execution=True,
            )
    elif field.type == "numeric" and op == Operator.SUBSTR:
        raise invalid_request(
            f"Unsupported operator for field '{clause.field}'.",
            code="invalid_query",
            pre_execution=True,
        )


def _compile_token(
    clause: Clause,
    field: SearchField,
    sql_parts: list[str],
    sql_binds: list[Any],
) -> None:
    """Token: case-insensitive exact match."""
    col = field.column
    if clause.value is None:
        # null: field is NULL.
        expr = f"{col} IS NULL" if not clause.negated else f"{col} IS NOT NULL"
        sql_parts.append(expr)
        return

    value = clause.value
    if field.is_boolean:
        # Convert "true"/"false" to 0/1 for the INTEGER column.
        if value.lower() == "true":
            bind_val: Any = 1
        elif value.lower() == "false":
            bind_val = 0
        else:
            bind_val = value
        expr = f"{col} = ?"
        if clause.negated:
            expr = f"NOT ({expr})"
        sql_parts.append(expr)
        sql_binds.append(bind_val)
        return

    if field.is_timestamp:
        # The query value is Unix seconds; the column is ISO text.
        expr = f"{col} = ?"
        if clause.negated:
            expr = f"NOT ({expr})"
        sql_parts.append(expr)
        sql_binds.append(_time.from_unix(int(value)))
        return

    # Standard case-insensitive exact match.
    expr = f"LOWER({col}) = LOWER(?)"
    if clause.negated:
        expr = f"NOT ({expr})"
    sql_parts.append(expr)
    sql_binds.append(value)


def _compile_numeric(
    clause: Clause,
    field: SearchField,
    sql_parts: list[str],
    sql_binds: list[Any],
) -> None:
    """Numeric: exact match or comparison operators."""
    col = field.column
    if clause.value is None:
        expr = f"{col} IS NULL" if not clause.negated else f"{col} IS NOT NULL"
        sql_parts.append(expr)
        return

    value = clause.value
    if field.is_timestamp:
        # Convert Unix seconds to ISO for comparison.
        bind_val: Any = _time.from_unix(int(value))
    else:
        # Try to parse as int, fall back to string for SQL comparison.
        try:
            bind_val = int(value)
        except ValueError:
            bind_val = value

    op_map = {
        Operator.EXACT: "=",
        Operator.GT: ">",
        Operator.GTE: ">=",
        Operator.LT: "<",
        Operator.LTE: "<=",
    }
    sql_op = op_map[clause.operator]
    expr = f"{col} {sql_op} ?"
    if clause.negated:
        expr = f"NOT ({expr})"
    sql_parts.append(expr)
    sql_binds.append(bind_val)


def _compile_string(
    clause: Clause,
    field: SearchField,
    spec: SearchSpec,
    sql_parts: list[str],
    sql_binds: list[Any],
    fts_terms: list[str],
) -> None:
    """String: phrase match via FTS5, or substring via SQL LIKE.

    - **null**: SQL ``IS NULL`` / ``IS NOT NULL``.
    - **SUBSTR** (``~``): SQL ``LIKE`` with case-insensitive comparison
      (FTS5 does not support infix substring matching).
    - **EXACT** (``:``): FTS5 ``MATCH`` with a column-scoped phrase query,
      so the tokenizer handles word boundaries and punctuation. Negated
      EXACT uses a ``NOT IN`` subquery against the FTS5 table.
    """
    col = field.column
    if clause.value is None:
        expr = f"{col} IS NULL" if not clause.negated else f"{col} IS NOT NULL"
        sql_parts.append(expr)
        return

    value = clause.value

    if clause.operator == Operator.SUBSTR:
        # Substring match: LIKE with case-insensitive comparison.
        if len(value) < _MIN_SUBSTR:
            raise invalid_request(
                f"Substrings must be a minimum of {_MIN_SUBSTR} characters.",
                code="invalid_query",
                pre_execution=True,
            )
        # Escape SQL LIKE wildcards in the value.
        escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        expr = f"{col} LIKE ? ESCAPE '\\'"
        if clause.negated:
            expr = f"(NOT ({expr}) OR {col} IS NULL)"
        sql_parts.append(expr)
        sql_binds.append(f"%{escaped}%")
        return

    # EXACT (`:`) — route through FTS5 MATCH.
    fts_table = spec.fts_table
    if fts_table is None:
        # Defensive: should not happen (string fields require an FTS table)
        # but fall back to SQL LIKE if the spec lacks one.
        escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        expr = f"{col} LIKE ? ESCAPE '\\'"
        if clause.negated:
            expr = f"(NOT ({expr}) OR {col} IS NULL)"
        sql_parts.append(expr)
        sql_binds.append(f"%{escaped}%")
        return

    # Build the FTS5 column-scoped phrase query.
    fts_phrase = _fts5_phrase(value)
    fts_expr = f"{col} : {fts_phrase}"

    if clause.negated:
        # Negated: use a NOT IN subquery with a bind parameter.
        sub = f"{spec.table}.rowid NOT IN (SELECT rowid FROM {fts_table} WHERE {fts_table} MATCH ?)"
        sql_parts.append(sub)
        sql_binds.append(fts_expr)
    else:
        # Non-negated: add to the FTS5 terms list for the JOIN path.
        fts_terms.append(fts_expr)


def _fts5_phrase(value: str) -> str:
    """Wrap a value as an FTS5 phrase (double-quoted).

    FTS5 phrases do not support internal double-quote escaping. Any double
    quotes in the value are stripped — the tokenizer would have discarded
    them as punctuation anyway.
    """
    clean = value.replace('"', "")
    return f'"{clean}"'


def _compile_metadata(
    clause: Clause,
    sql_parts: list[str],
    sql_binds: list[Any],
) -> None:
    """Metadata: json_extract on the metadata column."""
    key = clause.metadata_key
    if key is None:
        raise invalid_request(
            "Unsupported search field: 'metadata'.",
            code="invalid_query",
            pre_execution=True,
        )

    json_path = f"$.{key}"

    if clause.value is None:
        # null: the key is absent.
        expr = "json_extract(metadata, ?) IS NULL"
        if clause.negated:
            expr = "json_extract(metadata, ?) IS NOT NULL"
        sql_parts.append(expr)
        sql_binds.append(json_path)
        return

    # Case-insensitive exact match on the metadata value.
    expr = "LOWER(json_extract(metadata, ?)) = LOWER(?)"
    if clause.negated:
        expr = f"NOT ({expr})"
    sql_parts.append(expr)
    sql_binds.append(json_path)
    sql_binds.append(clause.value)


# --- Pagination ---------------------------------------------------------------


def _encode_seq_cursor(x_seq: int) -> str:
    """Encode an x_seq into an opaque page cursor (keyset mode)."""
    return base64.urlsafe_b64encode(f"seq:{x_seq}".encode()).decode()


def _decode_seq_cursor(cursor: str) -> int:
    """Decode a keyset page cursor back to an x_seq value."""
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode()).decode()
        if decoded.startswith("seq:"):
            return int(decoded[4:])
        if decoded.startswith("off:"):
            # An offset cursor was passed to a keyset query. This can
            # happen if the query changed between pages; treat the
            # cursor as invalid rather than silently misbehaving.
            pass
    except Exception:
        pass
    raise invalid_request(
        f"Invalid page cursor: '{cursor}'.",
        code="invalid_query",
        pre_execution=True,
    )


def _query_fingerprint(query: str, resource: str) -> str:
    """Return a short hash that identifies the query+resource pair."""
    h = hashlib.sha256(f"{resource}:{query}".encode()).hexdigest()[:12]
    return h


def _encode_offset_cursor(offset: int, fingerprint: str) -> str:
    """Encode an offset into an opaque page cursor (bm25 mode).

    Format: ``off:<offset>:<fingerprint>`` — the fingerprint ties the
    cursor to the originating query so it cannot be replayed against a
    different search.
    """
    raw = f"off:{offset}:{fingerprint}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_offset_cursor(cursor: str, expected_fingerprint: str) -> int:
    """Decode an offset page cursor and verify its fingerprint."""
    # Parse the cursor outside the try/except so that a fingerprint mismatch
    # (which raises StripeApiError) is never caught by the fallback handler —
    # the architectural lint forbids catching StripeApiError here.
    decoded: str | None = None
    with contextlib.suppress(Exception):
        decoded = base64.urlsafe_b64decode(cursor.encode()).decode()

    if decoded is not None and decoded.startswith("off:"):
        parts = decoded.split(":", 2)
        if len(parts) == 3:
            try:
                offset_val = int(parts[1])
            except ValueError:
                pass
            else:
                fp = parts[2]
                if fp != expected_fingerprint:
                    raise invalid_request(
                        "Page cursor does not match the current query.",
                        code="invalid_query",
                        pre_execution=True,
                    )
                return offset_val
    # A keyset cursor or malformed offset — fall through to error.

    raise invalid_request(
        f"Invalid page cursor: '{cursor}'.",
        code="invalid_query",
        pre_execution=True,
    )


def _clamp_limit(limit: int | None) -> int:
    """Clamp the limit to 1..100, defaulting to 10."""
    if limit is None:
        return _DEFAULT_LIMIT
    if limit < 1:
        return 1
    if limit > _MAX_LIMIT:
        return _MAX_LIMIT
    return limit


def _soft_delete_filter(table: str) -> str:
    """Exclude soft-deleted rows for tables that use soft delete."""
    soft_delete_tables = {"customers", "products", "coupons"}
    if table in soft_delete_tables:
        return "deleted = 0"
    return ""
