"""Parse Stripe's search query language into structured clauses.

Grammar (from https://docs.stripe.com/search):

    query     = clause ((AND | OR | WS) clause)*   # up to 10 clauses
    clause    = ["-"] field operator value
    field     = IDENT ("." IDENT)* | metadata "[" QUOTED_KEY "]"
    operator  = ":" | "~" | ">" | ">=" | "<" | "<="
    value     = QUOTED_STRING | BARE_NUMBER | "null"

Rules:
- AND and OR are case-insensitive; space alone defaults to AND.
- AND and OR cannot be mixed in one query.
- Up to 10 clauses.
- Quoted strings use ``\\`` to escape internal quotes.
- ``null`` (case-insensitive) is a special presence/absence token.
- Metadata fields: ``metadata["key"]`` with the key quoted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Final

__all__ = [
    "Clause",
    "Combinator",
    "Operator",
    "ParseError",
    "parse",
]


class ParseError(Exception):
    """A query that cannot be parsed: returned as a Stripe ``invalid_request_error``."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.stripe_message = message


class Operator(Enum):
    EXACT = ":"  # token/string exact, numeric equal
    SUBSTR = "~"  # string substring (min 3 chars)
    GT = ">"
    GTE = ">="
    LT = "<"
    LTE = "<="


class Combinator(Enum):
    AND = "AND"
    OR = "OR"


@dataclass(frozen=True, slots=True)
class Clause:
    """One parsed clause from a search query."""

    field: str  # "email", "amount", "metadata"
    operator: Operator
    value: str | None  # None when the value is ``null``
    negated: bool = False
    metadata_key: str | None = None  # set when field == "metadata"


# --- Tokenizer ----------------------------------------------------------------

_MAX_CLAUSES: Final = 10

# Matches a quoted string, handling backslash-escaped quotes.
_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')

# Matches metadata field: metadata["key"] or metadata['key']
_METADATA = re.compile(r'metadata\["((?:[^"\\]|\\.)*)"\]|metadata\[\'((?:[^\'\\]|\\.)*)\'\]')

# Operator patterns, longest first so >= is tried before >.
_OPERATORS: Final = (">=", "<=", ">", "<", "~", ":")


def _unescape(s: str) -> str:
    """Remove backslash escaping from a quoted value."""
    return s.replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")


def parse(query: str) -> tuple[list[Clause], Combinator]:
    """Parse a search query into clauses and the combinator joining them.

    Raises ``ParseError`` for syntax errors, mixed AND/OR, empty queries,
    or more than 10 clauses.
    """
    if not query or not query.strip():
        raise ParseError(
            "We were unable to parse your search query. The query must contain at least one clause."
        )

    clauses: list[Clause] = []
    combinator: Combinator | None = None
    pos = 0
    text = query.strip()

    while pos < len(text):
        # Skip whitespace.
        while pos < len(text) and text[pos] in " \t":
            pos += 1
        if pos >= len(text):
            break

        # Check for AND / OR keyword between clauses.
        if clauses:
            upper_rest = text[pos:].upper()
            if upper_rest.startswith("AND ") or upper_rest.startswith("AND\t"):
                if combinator is not None and combinator != Combinator.AND:
                    raise ParseError(
                        "We were unable to parse your search query. "
                        "You can't combine AND and OR in the same query."
                    )
                combinator = Combinator.AND
                pos += 4
                continue
            if upper_rest.startswith("OR ") or upper_rest.startswith("OR\t"):
                if combinator is not None and combinator != Combinator.OR:
                    raise ParseError(
                        "We were unable to parse your search query. "
                        "You can't combine AND and OR in the same query."
                    )
                combinator = Combinator.OR
                pos += 3
                continue
            # Space between clauses defaults to AND.
            if combinator is None:
                combinator = Combinator.AND

        # Parse one clause.
        clause, pos = _parse_clause(text, pos)
        clauses.append(clause)

        if len(clauses) > _MAX_CLAUSES:
            raise ParseError(
                "We were unable to parse your search query. "
                f"You can combine up to {_MAX_CLAUSES} query clauses in a search."
            )

    if not clauses:
        raise ParseError(
            "We were unable to parse your search query. The query must contain at least one clause."
        )

    return clauses, combinator if combinator is not None else Combinator.AND


def _parse_clause(text: str, pos: int) -> tuple[Clause, int]:
    """Parse one clause starting at ``pos``. Returns (clause, new_pos)."""
    # Negation prefix.
    negated = False
    if pos < len(text) and text[pos] == "-":
        negated = True
        pos += 1

    # Field name.
    field, metadata_key, pos = _parse_field(text, pos)

    # Operator.
    operator, pos = _parse_operator(text, pos)

    # Value.
    value, pos = _parse_value(text, pos)

    return Clause(
        field=field,
        operator=operator,
        value=value,
        negated=negated,
        metadata_key=metadata_key,
    ), pos


def _parse_field(text: str, pos: int) -> tuple[str, str | None, int]:
    """Parse the field part of a clause. Returns (field_name, metadata_key, new_pos)."""
    # Check for metadata["key"] pattern.
    m = _METADATA.match(text, pos)
    if m:
        key = m.group(1) if m.group(1) is not None else m.group(2)
        return "metadata", _unescape(key), m.end()

    # Regular dotted field name.
    start = pos
    while pos < len(text) and (text[pos].isalnum() or text[pos] in "._"):
        pos += 1
    if pos == start:
        raise ParseError(
            f"We were unable to parse your search query. Unexpected character at position {pos}."
        )
    return text[start:pos], None, pos


def _parse_operator(text: str, pos: int) -> tuple[Operator, int]:
    """Parse the operator after a field name."""
    for op_str in _OPERATORS:
        if text[pos : pos + len(op_str)] == op_str:
            return Operator(op_str), pos + len(op_str)
    raise ParseError(
        f"We were unable to parse your search query. Expected an operator at position {pos}."
    )


def _parse_value(text: str, pos: int) -> tuple[str | None, int]:
    """Parse the value part of a clause."""
    if pos >= len(text):
        raise ParseError(
            "We were unable to parse your search query. Unexpected end of query after operator."
        )

    # Quoted string value.
    if text[pos] == '"':
        m = _QUOTED.match(text, pos)
        if not m:
            raise ParseError(
                "We were unable to parse your search query. Unterminated quoted string."
            )
        return _unescape(m.group(1)), m.end()

    # Bare word/number: read until whitespace, end of string, or next clause.
    start = pos
    while pos < len(text) and text[pos] not in " \t":
        pos += 1
    raw = text[start:pos]

    # null is a special token.
    if raw.lower() == "null":
        return None, pos

    return raw, pos
