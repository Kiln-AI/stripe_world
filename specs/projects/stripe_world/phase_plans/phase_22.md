---
status: draft
---

# Phase 22: Search (FTS5 rebuild)

## Overview

Rebuild the search executor and schema to use FTS5 instead of the SQL LIKE + Python post-filter
hybrid that the prior implementation used. The parser, field allowlists, handlers, route wiring,
and most tests are kept from the prior pass; the schema and executor are replaced.

FTS5 is mandated by the project owner. The mechanism resolves five CR findings by construction:
total_count overcounting, OR combinator dropping matches, phrase matching punctuation sensitivity,
and has_more/pagination unreliability all disappear when every clause resolves in one SQL layer.
A sixth finding (total_count expand-gating) is orthogonal and is fixed in the params/executor flow.

## Steps

1. **`005_search.sql`** — real FTS5 schema. External-content virtual tables (`content='<table>',
   content_rowid='rowid'`) plus three triggers (after insert/delete/update) for each of the four
   tables with `string`-typed search fields: customers (email, name, phone), products (description,
   name, url), prices (lookup_key, product), invoices (number, parent_subscription, status).
   Tables with only token/numeric fields (charges, payment_intents, subscriptions) have no FTS5
   table.

2. **`fields.py`** — add `fts_table: str | None` attribute to `SearchSpec`. Set to the FTS5 table
   name for customers, products, prices, invoices; `None` for the other three. Revert
   `invoice.status` and `invoice.subscription` to `string` (Stripe documents them as such).

3. **`executor.py`** — rewrite. String-exact (`:`) clauses compile to FTS5 MATCH with column-scoped
   phrase queries. Non-negated FTS5 clauses combine into one MATCH for the JOIN path (AND with bm25
   ranking) or individual subqueries (OR). Negated FTS5 clauses use `NOT IN` subqueries. String
   substring (`~`), token, numeric, metadata stay in plain SQL. Two pagination modes: bm25+id
   offset cursors when FTS5 JOIN is active; x_seq keyset cursors otherwise. `total_count` is
   opt-in via `include_total_count` flag.

4. **`response.py`** — add `include_total_count: bool = False` to `Request`.

5. **`params.py`** — strip `total_count` from the expand list before static path validation
   (it is not a nested object reference), set `include_total_count=True` on the Request.

6. **`resources/search.py`** — pass `include_total_count=req.include_total_count` to the executor.

7. **Fixture regeneration** — the schema hash changes when `005_search.sql` gains content.
   Regenerate `fixtures/empty/`.

8. **`test_empty_fixture.py`** — add the four FTS5 virtual tables to the expected table list.

9. **`test_search.py`** — update tests that referenced `total_count` to either omit the assertion
   or pass `expand=["total_count"]`. Add `test_total_count_absent_by_default` and
   `test_total_count_present_when_expanded`.

## Tests

- `TestParser` — kept from prior pass, strategy-independent.
- `TestSearchEnvelope::test_empty_search_returns_correct_shape` — updated: asserts `total_count`
  is absent by default.
- `TestSearchPagination::test_total_count_absent_by_default` — new: absent without expand.
- `TestSearchPagination::test_total_count_present_when_expanded` — new: accurate with expand.
- `TestPostFilterCorrectness` (renamed findings) — all confirmed dissolved by FTS5.
- All 52 search tests pass. Full suite (900+ tests) passes.
