# Row-level change representations: a survey

Eight formats, compared on the five axes the research plan asks for. The SQLite session extension
and `sqldiff` have their own file — see [sqlite-session-and-sqldiff.md](./sqlite-session-and-sqldiff.md).

All sources fetched 2026-09-14. This session's egress proxy blocks most documentation hosts
(sqlite.org, docs.dolthub.com, debezium.io, datatracker.ietf.org, docs.datomic.com, git-scm.com,
stripe.com, opentelemetry.io, protobuf.dev, docs.confluent.io); where a primary source lives in a
public git repo I read it from `raw.githubusercontent.com`, and where it does not I say so and cite
search-surfaced text instead.

---

## Comparison table

| Format | Net diff or log? | Row identity | Update = whole row or changed columns? | JSON-native? | Carries "before"? |
|---|---|---|---|---|---|
| SQLite **changeset** | net diff | declared PRIMARY KEY | PK + changed columns, on *both* sides | no (binary; needs an iterator) | yes, but only for changed columns |
| SQLite **patchset** | net diff | declared PRIMARY KEY | PK + new values of changed columns | no (binary) | **no** |
| `sqldiff` | net diff (two files) | true PK (rowid by default) | changed columns only | no (SQL text) | no |
| **Dolt** `dolt_commit_diff_$T` | net diff (two-point) | table PK; keyless ⇒ add/remove pairs | full `from_*` and `to_*` column sets | yes, via SQL/JSON | yes (full row) |
| **Dolt** `dolt_diff_$T` | ordered log (per adjacent commit) | same | same | yes | yes |
| **Dolt** `dolt diff -r json` | net diff | same | `from_row` / `to_row` objects, whole rows, NULLs omitted | **yes** | yes |
| **Debezium / CDC** | ordered log | event key = PK | `after` = whole row; `before` = whole row only if the source is configured for it | yes | configurable |
| **Datomic** log | ordered log of datoms | entity id `?e` | per-attribute assert/retract (finest grain of all) | EDN, not JSON | retraction datoms give the old value |
| **JSON Patch** (RFC 6902) | ordered op list | JSON Pointer path (positional) | per-field `replace` ops | yes | **no** |
| **JSON Merge Patch** (RFC 7386) | net diff | object key path | changed keys only; `null` = delete | yes | **no** |
| **jsondiffpatch delta** | net diff | object key / array index | changed keys only | yes (but positional magic numbers) | yes (`[old, new]`) |
| **git unified diff** | net diff | line position (`@@` hunks) | n/a — text lines | no | yes (`-` lines) |

---

## Dolt

Sources (the dolthub.com docs site is blocked; its markdown source is public):
- [`site/dolt/src/content/reference/sql/version-control/dolt-system-tables.md`](https://raw.githubusercontent.com/dolthub/docs-2/master/site/dolt/src/content/reference/sql/version-control/dolt-system-tables.md)
- [`site/dolt/src/content/reference/cli/cli.md`](https://raw.githubusercontent.com/dolthub/docs-2/master/site/dolt/src/content/reference/cli/cli.md)
- [`integration-tests/bats/json-diff.bats`](https://raw.githubusercontent.com/dolthub/dolt/main/integration-tests/bats/json-diff.bats) — the executable spec for the JSON output shape

Dolt ships **two** row-diff system tables per user table, and the distinction is exactly the
net-diff-vs-log distinction:

> "`dolt_commit_diff_$TABLENAME` is the analogue of the `dolt diff` CLI command. It represents the
> two-dot diff between the two commits provided. The `dolt_diff_$TABLENAME` system table also exposes
> diff information, but instead of a two-way diff, it returns a log of individual diffs between all
> adjacent commits in the history of the current branch. In other words, if a row was changed in 10
> separate commits, `dolt_diff_$TABLENAME` will show 10 separate rows — one for each individual delta.
> In contrast, `dolt_commit_diff_$TABLENAME` would show a single row that combines all the individual
> commit deltas into one diff."

Both have the same shape — a **column-doubling** schema:

```text
+------------------+----------+
| field            | type     |
+------------------+----------+
| from_commit      | TEXT     |
| from_commit_date | DATETIME |
| to_commit        | TEXT     |
| to_commit_date   | DATETIME |
| diff_type        | TEXT     |
| other cols       |          |
+------------------+----------+
```

> "For every column `X` in your table at the currently checked out branch, there are columns in the
> result set named `from_X` and `to_X` with the same type as `X` in the current schema."

`diff_type` is one of `added`, `modified`, `removed`. `to_commit` accepts the magic value
`WORKING`. `dolt_commit_diff_$T` **requires** both `from_commit` and `to_commit` in the WHERE clause
or it errors.

**The design lesson**: making the diff a *table* means the assertion language is just SQL. The
documented example judge is a one-liner:

```sql
SELECT to_county, from_county, to_num_inmates_rated_for, from_num_inmates_rated_for,
       abs(to_num_inmates_rated_for - from_num_inmates_rated_for) AS delta
FROM dolt_diff_jails
WHERE from_commit = HASHOF("HEAD~3") AND diff_type = "modified"
ORDER BY delta DESC LIMIT 10;
```

That is "number of changed rows in table X where status went from A to B" written the obvious way.
Column-doubling is what makes it work: `from_status = 'A' AND to_status = 'B'` is a plain predicate.
(The assertion-language subtopic owns the comparison of expression languages; I note only that the
`from_`/`to_` prefix trick is what makes a *generic* language sufficient.)

`DOLT_DIFF()` is a table function alternative "for cases where a table's schema has changed between
the `to` and `from` commits", because the system tables project everything through the *current*
branch's schema. `--skinny` / `-sk` "Shows only primary key columns and any columns with data
changes" — a deliberate trade of completeness for reviewability.

### The JSON wire shape

`dolt diff -r json` (valid values for `-r`: "tabular, sql, json. Defaults to tabular"). From the
bats tests, verbatim expected output:

```json
{"tables":[{"name":"test","schema_diff":["ALTER TABLE `test` DROP `c2`;","ALTER TABLE `test` ADD `c3` varchar(10);"],"data_diff":[{"from_row":{"c1":2,"c2":3,"pk":1},"to_row":{}},{"from_row":{"c1":5,"c2":6,"pk":4},"to_row":{"c1":100,"pk":4}},{"from_row":{},"to_row":{"c1":8,"c3":"9","pk":7}}]}]}
```

Read that carefully, it is instructive:

- one object per table, with `schema_diff` (an array of DDL strings) and `data_diff`;
- an **insert** is `{"from_row":{}, "to_row":{...}}`; a **delete** is `{"from_row":{...},"to_row":{}}`;
  there is no explicit `op` field — the empty object *is* the op;
- an **update** carries both whole rows — `{"from_row":{"c1":5,"c2":6,"pk":4},"to_row":{"c1":100,"pk":4}}`.
  `to_row` omits `c3` because it is NULL, not because it is unchanged: elsewhere in the same test,
  an unchanged `c3` is repeated on both sides (`{"from_row":{"c1":8,"c3":"9","pk":7},"to_row":{"c1new":16,"c3":"9","pk":7}}`).
  **Dolt sends whole rows and omits NULLs.** That is the opposite trade from SQLite's changeset.

**Keyless tables** degrade the way you would fear. From the same test file, an UPDATE on a keyless
table produces a delete/insert pair — the test asserts `{"from_row":{},"to_row":{"pk":1,"val":2}}`
occurs twice and `{"from_row":{"pk":1,"val":1},"to_row":{}}` occurs twice — and the test itself
comments: *"The JSON output order for keyless diff isn't guaranteed, so we just count number of
times the row diff strings occur."* Same lesson as SQLite: no primary key, no row identity, no
update op, no stable order.

---

## Debezium and CDC change-event envelopes

Sources: [`Envelope.java`](https://raw.githubusercontent.com/debezium/debezium/main/debezium-connector-common/src/main/java/io/debezium/data/Envelope.java)
(the canonical field names), and
[`documentation/modules/ROOT/pages/connectors/postgresql.adoc`](https://raw.githubusercontent.com/debezium/debezium/main/documentation/modules/ROOT/pages/connectors/postgresql.adoc)
(the doc source for debezium.io, which is blocked).

The envelope's field names, from the source:

| constant | field | javadoc |
|---|---|---|
| `BEFORE` | `before` | "The `before` field is used to store the state of a record before an operation." |
| `AFTER` | `after` | "The `after` field is used to store the state of a record after an operation." |
| `OPERATION` | `op` | "The `op` field is used to store the kind of operation on a record." |
| `SOURCE` | `source` | "The `origin` field is used to store the information about the source of a record." |
| `TRANSACTION` | `transaction` | "The optional metadata information associated with transaction - like transaction id." |
| `TIMESTAMP` | `ts_ms` | "the local time at which the connector processed/generated the event" |
| — | `ts_us`, `ts_ns` | same, in µs / ns |

Operation codes: `r` READ ("most typically during snapshots"), `c` CREATE, `u` UPDATE, `d` DELETE,
`t` TRUNCATE, `m` MESSAGE.

A verbatim update event from the Postgres connector doc:

```json
{
    "schema": { ... },
    "payload": {
        "before": { "id": 1 },
        "after": {
            "id": 1,
            "first_name": "Anne Marie",
            "last_name": "Kretchmar",
            "email": "annek@noanswer.org"
        },
        "source": {
            "version": "...", "connector": "postgresql", "name": "PostgreSQL_server",
            "ts_ms": 1559033904863, "snapshot": false,
            "db": "postgres", "schema": "public", "table": "customers",
            "txId": 556, "lsn": 24023128, "xmin": null
        },
        "op": "u",
        "ts_ms": 1465584025523
    }
}
```

The doc's own annotation is the key caveat:

> "`before`:: An optional field that contains values that were in the row before the database commit.
> In this example, only the primary key column, `id`, is present because the table's `REPLICA
> IDENTITY` setting is, by default, `DEFAULT`. For an _update_ event to contain the previous values
> of all columns in the row, you would have to change the `customers` table by running
> `ALTER TABLE customers REPLICA IDENTITY FULL`."

So CDC's "before" is only as good as the source database's replication configuration, and the
default gives you PK-only. `after` is always the **whole new row**, never a changed-columns subset.

**Shape takeaways worth stealing.** The Debezium envelope is the most widely copied change-event
shape in the industry, and its three-part split is the reason: `op` (a one-character verb),
`before`/`after` (the data), and `source` (provenance — which server, which LSN/txId, which
table, whether it was a snapshot). The provenance block is the part most row-diff formats omit and
then regret. Note also that the event is a **log entry**, delivered per commit and ordered by LSN;
net-diff behaviour (an insert then a delete cancelling out) is explicitly *not* what CDC does.

Kafka Connect wraps each event in `{"schema": ..., "payload": ...}` — an inline, per-message schema.
It is verbose enough that most deployments turn it off or move to the Schema Registry; see
[versioned-data-contracts.md](./versioned-data-contracts.md).

---

## Datomic's transaction log

`docs.datomic.com` is blocked from this session, so this section rests on search-surfaced doc text
plus Cognitect's own tutorial file on GitHub
([`day-of-datomic/tutorial/log.clj`](https://raw.githubusercontent.com/Datomic/day-of-datomic/master/tutorial/log.clj)).
Treat the prose as second-hand; the query below is verbatim from Cognitect's repo.

Datomic's unit of change is the **datom**: `[e a v tx added]` — entity, attribute, value,
transaction, and a boolean `added` (true = assertion, false = retraction). A transaction's `tx-data`
is the set of datoms it created, assertions and retractions together. The log is "a recording of all
transaction data in historic order".

```clojure
;; What else happened at the same time (i.e. during the same transaction)
;; as Joe moving to Broadway?
(d/q '[:find ?e ?a ?v ?tx ?op
       :in ?log ?tx
       :where [(tx-data ?log ?tx)[[?e ?a ?v _ ?op]]]]
     (d/log conn) 13194139534317)
```

The tutorial's own comment on that result is the point: *"Note that we see the same wall clock time
we just queried for, as well as 4 other datoms. One is the assertion of Joe moving to Broadway. One
is the retraction of his previous street (1st)."*

**The interesting property for a state format**: Datomic is the only system here whose change record
is *per attribute*, not per row. An "update" is literally a retraction datom carrying the old value
plus an assertion datom carrying the new one — the same information a SQLite changeset UPDATE
carries, decomposed into two facts. It is an ordered log, never a net diff, and the "what does the
world look like now" question is answered by a different API (`d/db`, `d/pull`) rather than by
folding the log. Two views, two APIs, no attempt to make one serve both — which is a real design
choice worth noticing.

---

## JSON Patch — RFC 6902 (April 2013, Standards Track)

Read from a mirror of the RFC text
([rfcmirror/rfc6902.txt](https://raw.githubusercontent.com/fire833/rfcmirror/master/rfc/rfc6902.txt))
because datatracker and rfc-editor are both blocked.

> "A JSON Patch document is a JSON document that represents an array of objects. Each object
> represents a single operation to be applied to the target JSON document."

> "Operation objects MUST have exactly one "op" member, whose value indicates the operation to
> perform. Its value MUST be one of "add", "remove", "replace", "move", "copy", or "test"; other
> values are errors. Additionally, operation objects MUST have exactly one "path" member. That
> member's value is a string containing a JSON-Pointer value [RFC6901]."

The canonical example from §3:

```json
[
  { "op": "test", "path": "/a/b/c", "value": "foo" },
  { "op": "remove", "path": "/a/b/c" },
  { "op": "add", "path": "/a/b/c", "value": [ "foo", "bar" ] },
  { "op": "replace", "path": "/a/b/c", "value": 42 },
  { "op": "move", "from": "/a/b/c", "path": "/a/b/d" },
  { "op": "copy", "from": "/a/b/d", "path": "/a/b/e" }
]
```

> "Operations are applied sequentially in the order they appear in the array. Each operation in the
> sequence is applied to the target document; the resulting document becomes the target of the next
> operation."

Media type: `application/json-patch+json`.

**Why it is a poor fit for grading a database diff.** It is an *ordered, position-dependent program*,
not a description of state. `replace` carries no old value, so "did status go from A to B" is
unanswerable from the patch alone (`test` exists to *assert* a precondition at apply time, not to
record one). Array element identity is by index, so an insert at the head renumbers everything
after it. `move` and `copy` make two semantically identical patches textually very different. It is
excellent for HTTP PATCH — its actual purpose — and bad as a queryable diff.

## JSON Merge Patch — RFC 7386 (October 2014, Standards Track)

The whole spec is one pseudocode function:

```text
define MergePatch(Target, Patch):
  if Patch is an Object:
    if Target is not an Object:
      Target = {} # Ignore the contents and set it to an empty Object
    for each Name/Value pair in Patch:
      if Value is null:
        if Name exists in Target:
          remove the Name/Value pair from Target
      else:
        Target[Name] = MergePatch(Target[Name], Value)
    return Target
  else:
    return Patch
```

A merge patch *looks like* the document, containing only changed keys, with `null` meaning delete:

```json
{ "a":"z", "c": { "f": null } }
```

The RFC names its own two limitations verbatim:

> "This design means that merge patch documents are suitable for describing modifications to JSON
> documents that primarily use objects for their structure and do not make use of explicit null
> values. The merge patch format is not appropriate for all JSON syntaxes."

> "If the patch is anything other than an object, the result will always be to replace the entire
> target with the entire patch. Also, it is not possible to patch part of a target that is not an
> object, such as to replace just some of the values in an array."

Media type: `application/merge-patch+json`.

**Assessment**: the cheapest possible "changed fields only" encoding, and the one closest in spirit
to a changeset's UPDATE — but it cannot represent a literal `null`, cannot touch arrays element-wise,
and carries no before-values. For a *row* it is almost right; for a *table of rows* you would need
the row key to be an object key, which means the format silently stops working for composite keys.

## jsondiffpatch delta format

Source: [`docs/deltas.md`](https://raw.githubusercontent.com/benjamine/jsondiffpatch/master/docs/deltas.md).

A positional-array encoding:

| change | delta |
|---|---|
| added | `[newValue]` |
| modified | `[oldValue, newValue]` |
| deleted | `[oldValue, 0, 0]` |
| nested object | `{ property1: innerDelta1, ... }` (only changed properties) |
| array | `{ _t: 'a', 0: innerDelta, _3: innerDelta }` — bare index = new position, `_N` = original position |
| array move | `['', destinationIndex, 3]` |
| text diff | `[unidiff, 0, 2]` (for strings over ~60 chars) |

It is the only JSON format surveyed that keeps **both** old and new values inline (`[old, new]`),
which makes it genuinely queryable for "went from A to B". Against it: the magic trailing numbers
(0 = delete, 2 = text diff, 3 = move) are opaque to anyone who has not read the docs, an LLM writing
a judge against it will get them wrong, and array handling is index-based with the same renumbering
fragility as JSON Patch. There is also `omitRemovedValues: true`, which replaces the old value with
`0` and makes the delta irreversible — an option worth knowing exists before someone enables it.

## Git-style textual diffs

Source: [`Documentation/diff-generate-patch.adoc`](https://raw.githubusercontent.com/git/git/master/Documentation/diff-generate-patch.adoc).

Unified diff: a per-file header, optional extended headers (`old mode`, `new mode`,
`deleted file mode`, `new file mode`, `copy from`/`copy to`, `rename from`/`rename to`,
`similarity index`, `dissimilarity index`, `index <hash>..<hash> <mode>`), then hunks headed
`@@ <from-file-range> <to-file-range> @@`. Combined diffs for merges widen the marker to `@@@` with
"(number of parents + 1) `@` characters" and prefix each line with one column per parent.

Two properties matter for this research:

1. **It is a net diff between two texts, with no notion of identity beyond line position.** Renames
   are *detected heuristically* (similarity index), not recorded by the writer. The analogue of "the
   row moved" does not exist as a first-class concept — which is exactly the failure mode a PK-based
   row diff avoids.
2. **It is unparseable by a generic expression language.** Any judge over a unified diff is a regex
   over `+`/`-` lines. This is the baseline that every structured format in this file is trying to
   beat, and the reason "just diff the SQL dump" is a bad idea for a machine-graded environment even
   though it is a great idea for a human reviewer.

The one thing git gets right that structured diffs often miss: it is **human-reviewable at a
glance**, and the review workflow built on it (review the diff, not the end state) is the ergonomic
model benchmark authors keep reaching for. If a `state()` payload is ever printed for a human, a
rendered-text view of the diff alongside the structured one costs little.

---

## Cross-cutting observations

1. **Net diff vs ordered log is the first fork, and you usually want both.** The systems that are
   happiest have *two* surfaces: Dolt (`dolt_commit_diff_$T` two-point, `dolt_diff_$T` log),
   Datomic (`d/db` for state, `d/log` for history). SQLite's session extension gives you only the
   net diff — deliberately, and that is what makes two runs that reached the same state agree.
2. **Row identity is the load-bearing decision, and every format degrades the same way without it.**
   SQLite silently records nothing for a PK-less table. Dolt turns updates into delete/insert pairs
   with undefined ordering. JSON Patch/jsondiffpatch fall back to array indices and renumber. The
   only robust answer is "declare a key per table and refuse to diff without one".
3. **Whole rows vs changed columns is the second fork, and the formats split cleanly.** SQLite
   changeset = PK + changed columns on both sides (compact, but you cannot read an unchanged
   column). Dolt = whole rows on both sides (verbose, but every predicate is writable). Debezium =
   whole `after`, configurable `before`. A judge that wants `to_status = 'B' AND from_status = 'A'`
   needs only the changed columns; a judge that wants "the assignee of every changed row" needs the
   whole row. Both kinds of judge exist, which argues for making the full current row reachable
   *beside* the diff rather than inside it.
4. **`op` as an explicit verb beats inferring it.** Dolt's JSON makes you infer insert/delete from an
   empty `from_row`/`to_row`; Debezium and SQLite name the operation. Explicit is cheaper to judge
   and survives a format reader that does not know the convention.
5. **Nothing here is ordered usefully.** SQLite: "undefined" within a table. Dolt keyless: "isn't
   guaranteed". If an eval wants determinism, it must sort, and the sort key must be the row key.
