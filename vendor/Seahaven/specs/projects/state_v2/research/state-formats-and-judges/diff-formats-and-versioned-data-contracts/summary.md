# Diff Formats and Versioned Data Contracts

## Bottom Line

Row-level change formats split on three axes, and every surveyed system makes the same trade-offs in
the same places: **net diff vs ordered log**, **whole rows vs changed columns only**, and **primary
key vs positional identity**. SQLite's changeset is a net diff keyed on the declared PRIMARY KEY that
carries *only the changed columns, on both sides* — so a judge can never read an unchanged column
out of it, a PK rewrite arrives as DELETE+INSERT rather than UPDATE, rows with a NULL in any PK
column vanish silently, and the order of changes within a table is documented as **undefined**. Dolt
is the most instructive contrast: it exposes both a two-point net diff and a per-commit log, doubles
every column into `from_X`/`to_X` so that "status went from A to B" is a plain SQL predicate, and
emits whole rows (omitting NULLs) in its JSON. JSON Patch and Merge Patch are apply-oriented and
carry no before-values, which disqualifies both as grading substrates. On versioning, seven
independent bodies of practice (CloudEvents, JSON Schema, OpenTelemetry, Stripe, Avro, Protobuf,
Google AIP) converged on the same two-part answer: **a required version identifier inside every
document**, plus **a written contract for which changes are invisible**, with a **chain of small pure
per-version transformations** as the escape hatch when the contract is not enough. That chain — OTel's
schema files, Stripe's version-change modules, event-sourcing upcasters — is the documented form of
"version field plus formatter", and all three implementations agree on its shape.

## Key Findings

- **A SQLite changeset UPDATE is not a before-row and an after-row.** Verbatim from
  `ext/session/sqlite3session.c`: "Within the old.* record associated with an UPDATE change, all
  fields associated with table columns that are not PRIMARY KEY columns and are not modified by the
  UPDATE change are set to 'undefined'… Within the new.* record, fields associated with table columns
  that are not modified are set to 'undefined'." So an update carries PK + changed columns only, on
  both sides. Seahaven's `changes.py` already models this correctly, but any judge reading
  `before`/`after` must expect columns to be *absent*.
  ([source](https://raw.githubusercontent.com/sqlite/sqlite/master/ext/session/sqlite3session.c))
- **Changeset ordering within a table is explicitly undefined.** "The order in which the changes
  related to a single table are stored is undefined." Reading `sessionGenerateChangeset` confirms it
  emits hash-bucket order over the PK — not rowid order. Seahaven's `render()` docstring currently
  says rowid order; that is an undocumented implementation detail at best.
  ([source](https://raw.githubusercontent.com/sqlite/sqlite/master/ext/session/sqlite3session.h))
- **Three silent data-loss modes in the session extension.** Tables with no explicit PRIMARY KEY
  attach happily and record nothing; rows with NULL in any PK column are never recorded; and a PK
  rewrite is emitted as DELETE followed by INSERT, never as UPDATE. All three are stated verbatim in
  `sqlite3session.h`. Since **3.42.0** (bisected on the GitHub mirror's release tags),
  `SQLITE_SESSION_OBJCONFIG_ROWID` can track PK-less tables by synthesising a leftmost
  `_rowid_ INTEGER PRIMARY KEY`.
- **The session extension already computes an "indirect change" flag** — set when a change came from a
  trigger or FK action rather than the user's statement. It is the only built-in handle on the
  "irrelevant/audit changes" problem, it is available from `sqlite3changeset_op()`, and it cannot be
  reconstructed after the fact.
- **Dolt's `from_X`/`to_X` column-doubling is the single most judge-friendly design found.** It turns
  a row diff into an ordinary table, so `WHERE from_status='A' AND to_status='B' AND diff_type='modified'`
  is the whole judge. Dolt ships the net diff (`dolt_commit_diff_$T`, requires both endpoints) and the
  log (`dolt_diff_$T`, one row per adjacent-commit delta) as *separate* tables rather than trying to
  make one serve both.
  ([source](https://raw.githubusercontent.com/dolthub/docs-2/master/site/dolt/src/content/reference/sql/version-control/dolt-system-tables.md))
- **`dolt diff -r json` sends whole rows and infers the op from emptiness**:
  `{"tables":[{"name":"test","schema_diff":[...],"data_diff":[{"from_row":{...},"to_row":{}}]}]}` —
  empty `to_row` = delete, empty `from_row` = insert, no `op` field. NULL columns are omitted, but
  *unchanged* columns are repeated on both sides. Keyless tables degrade to delete/insert pairs with
  order Dolt's own tests describe as "not guaranteed".
  ([source](https://raw.githubusercontent.com/dolthub/dolt/main/integration-tests/bats/json-diff.bats))
- **Debezium's envelope is the industry's most-copied change-event shape**: `op` (`c`/`u`/`d`/`r`/`t`/`m`),
  `before`, `after`, `source` (provenance: connector, db, schema, table, txId, lsn, snapshot-flag),
  `ts_ms`. `after` is always the whole new row; `before` is **PK-only by default** and needs
  `REPLICA IDENTITY FULL` on Postgres to carry all columns. The `source` provenance block is the part
  most row-diff formats omit and cannot add retroactively.
  ([Envelope.java](https://raw.githubusercontent.com/debezium/debezium/main/debezium-connector-common/src/main/java/io/debezium/data/Envelope.java),
  [postgresql.adoc](https://raw.githubusercontent.com/debezium/debezium/main/documentation/modules/ROOT/pages/connectors/postgresql.adoc))
- **JSON Patch (RFC 6902) and Merge Patch (RFC 7386) are both wrong for grading.** JSON Patch is an
  ordered program whose `replace` carries no old value and whose array identity is positional; Merge
  Patch is a net changed-keys-only diff that "is not appropriate for all JSON syntaxes" (its own
  words), cannot express a literal `null`, and cannot patch inside arrays. jsondiffpatch is the only
  JSON delta format that keeps `[oldValue, newValue]` inline — at the cost of magic trailing integers
  (0 = delete, 2 = text diff, 3 = array move) that an LLM will get wrong.
- **Datomic decomposes an update into a retraction datom (old value) plus an assertion datom (new
  value)**, `[e a v tx added]`, per *attribute* rather than per row, in an ordered log — and answers
  "what does the world look like now" through an entirely different API. Two questions, two
  surfaces, no attempt to unify. (Second-hand: docs.datomic.com is blocked; the query form is
  verbatim from [Cognitect's day-of-datomic](https://raw.githubusercontent.com/Datomic/day-of-datomic/master/tutorial/log.clj).)
- **Every durable-format community puts the version inside the document, required, at the root.**
  CloudEvents `specversion` is REQUIRED and deliberately carries only major.minor "to allow 'patch'
  changes to the specification to be made without changing this property's value in the
  serialization"; JSON Schema recommends `$schema` on every document and blesses non-resolvable `$id`s
  ("those identifiers are not necessarily network-addressable. They are just identifiers"); OTel
  requires `schema_url` ending in the version, at two levels with a precedence rule.
- **OpenTelemetry is the most complete published "version field plus formatter" design.** A
  `schema_url` of the form `http[s]://server/path/<version>` identifies an immutable YAML schema file
  ("schema files are immutable once they are published"); the file's `versions:` map lists typed
  transformations (`rename_attributes`, `rename_metrics`, `split`) per version; converting from X to Y
  applies "the transformations specified in each version in the range [X..Y] … one by one", reversible.
  The stability spec says outright that convention stability *means* "changes must be describable via
  schema files".
  ([schemas/README.md](https://raw.githubusercontent.com/open-telemetry/opentelemetry-specification/main/specification/schemas/README.md))
- **Upcasters are the same design pointed the other way, and the reachable primary source states the
  pattern cleanly**: "Upcasting is a process of transforming the old JSON schema into the new one.
  It's performed on the fly each time the event is read. You can think of it as a pluggable middleware
  between the deserialization and application logic… Thanks to that, we can keep only the last version
  of the event schema in our stream aggregation or projection handling." Stripe does the mirror image
  (newest implementation, chain of downgraders to each pinned version). For saved artifacts, put the
  chain on the **reader**.
  ([Marten docs](https://raw.githubusercontent.com/JasperFx/marten/master/docs/events/versioning.md))
- **AIP-180's "semantic" compatibility is the rule people forget**: "APIs must not change the expected
  format or algorithm used to construct the value of an existing field—even if OUTPUT_ONLY—within an
  API version." Types can stay identical while a document silently stops meaning what a judge assumed.
- **Avro's Parsing Canonical Form + fingerprint** is a cheap, stealable way to identify a schema by
  content rather than by a hand-maintained number: strip `doc`/`aliases`, expand fullnames, order keys
  `name, type, fields, symbols, items, values, size`, hash. "If the Parsing Canonical Forms of two
  different schemas are textually equal, then those schemas are 'the same' as far as any reader is
  concerned."

## Details

- [sqlite-session-and-sqldiff.md](./sqlite-session-and-sqldiff.md) — the full verbatim spec text for
  changesets, patchsets, the binary format, the three silent-loss modes, `OBJCONFIG_ROWID`, indirect
  changes, changegroups, and how `sqldiff` differs (two files, true-rowid keys, SQL-text output).
  Read this before making any decision about what `state()` does with `inst.changes()`.
- [row-change-formats.md](./row-change-formats.md) — the eight-format survey with a comparison table:
  Dolt (system tables, CLI, JSON, keyless degradation), Debezium/CDC envelopes, Datomic datoms,
  RFC 6902, RFC 7386, jsondiffpatch, git unified diff. Read this when choosing the wire shape of a
  change record.
- [versioned-data-contracts.md](./versioned-data-contracts.md) — CloudEvents, JSON Schema, OTel
  schema files and stability guarantees, Stripe pinning, Avro resolution + canonical form, Protobuf
  update rules, Confluent compatibility modes, AIP-180, and upcasters, ending in a table of the seven
  recurring elements. Read this when writing the versioning section of the spec.
- [implications-for-state-format.md](./implications-for-state-format.md) — my inferences for Seahaven
  specifically, grounded against `src/seahaven/changes.py`. Clearly separated from findings.

## Open Questions / Gaps

- **Egress restrictions shaped this research.** This session's proxy blocks sqlite.org,
  docs.dolthub.com, debezium.io, datatracker.ietf.org, rfc-editor.org, docs.datomic.com, git-scm.com,
  stripe.com, opentelemetry.io, protobuf.dev, docs.confluent.io, learn.microsoft.com, medium.com and
  Wikipedia. I routed around it by reading the same text from the projects' public git repos on
  `raw.githubusercontent.com` (which is how every quote above was obtained) plus json-schema.org,
  which is reachable. **Sections resting on search-result summaries rather than fetched text, and
  therefore second-hand: Stripe's versioning mechanism, Confluent's compatibility modes, Postgres
  `REPLICA IDENTITY` semantics beyond what Debezium's own adoc says, and Datomic's log prose.** Each
  is flagged in place.
- **`SQLITE_SESSION_OBJCONFIG_ROWID`'s introducing version** is from bisecting the GitHub mirror's
  `version-3.4x.0` tags (absent at 3.41.0, present at 3.42.0), not from an official changelog entry —
  sqlite.org's release notes are unreachable.
- **OTel telemetry-schema adoption** — the design is documented, but I could not verify from a
  reachable source how many consumers actually perform schema translation. Do not assume it is widely
  implemented beyond the Collector's schema processor.
- **JSON Schema guidance on versioning `$id` itself** — the structuring page does not cover it, and I
  found no authoritative json-schema.org recommendation. Absence of guidance, not guidance against.
- **Whether `apsw` exposes the indirect flag and `OBJCONFIG_ROWID`** — I did not check APSW's binding
  surface; that is a quick local verification rather than a research question.

## Sources

- [sqlite/sqlite `ext/session/sqlite3session.h`](https://raw.githubusercontent.com/sqlite/sqlite/master/ext/session/sqlite3session.h) — authoritative for the session API's documented semantics (`master`, fetched 2026-09-14); this is the text sqlite.org's `session/*.html` pages are generated from.
- [sqlite/sqlite `ext/session/sqlite3session.c`](https://raw.githubusercontent.com/sqlite/sqlite/master/ext/session/sqlite3session.c) — authoritative for the binary CHANGESET/PATCHSET formats and the actual emission order.
- [sqlite/sqlite `tool/sqldiff.c`](https://raw.githubusercontent.com/sqlite/sqlite/master/tool/sqldiff.c) — authoritative for `sqldiff` behaviour and options.
- [dolthub/docs-2 `dolt-system-tables.md`](https://raw.githubusercontent.com/dolthub/docs-2/master/site/dolt/src/content/reference/sql/version-control/dolt-system-tables.md) and [`cli.md`](https://raw.githubusercontent.com/dolthub/docs-2/master/site/dolt/src/content/reference/cli/cli.md) — the source of docs.dolthub.com.
- [dolthub/dolt `integration-tests/bats/json-diff.bats`](https://raw.githubusercontent.com/dolthub/dolt/main/integration-tests/bats/json-diff.bats) — executable spec for `dolt diff -r json`, including keyless-table behaviour.
- [debezium/debezium `Envelope.java`](https://raw.githubusercontent.com/debezium/debezium/main/debezium-connector-common/src/main/java/io/debezium/data/Envelope.java) — canonical envelope field names and operation codes.
- [debezium/debezium `connectors/postgresql.adoc`](https://raw.githubusercontent.com/debezium/debezium/main/documentation/modules/ROOT/pages/connectors/postgresql.adoc) — verbatim update-event sample and the `REPLICA IDENTITY` caveat; source of debezium.io.
- [RFC 6902, JSON Patch](https://raw.githubusercontent.com/fire833/rfcmirror/master/rfc/rfc6902.txt) — April 2013, Standards Track (read from a mirror; datatracker and rfc-editor are blocked).
- [RFC 7386, JSON Merge Patch](https://raw.githubusercontent.com/fire833/rfcmirror/master/rfc/rfc7386.txt) — October 2014, Standards Track (same mirror).
- [jsondiffpatch `docs/deltas.md`](https://raw.githubusercontent.com/benjamine/jsondiffpatch/master/docs/deltas.md) — authoritative for the delta encoding.
- [git `Documentation/diff-generate-patch.adoc`](https://raw.githubusercontent.com/git/git/master/Documentation/diff-generate-patch.adoc) — unified and combined diff formats.
- [Datomic/day-of-datomic `tutorial/log.clj`](https://raw.githubusercontent.com/Datomic/day-of-datomic/master/tutorial/log.clj) — Cognitect's own log-API example; surrounding prose is second-hand from search.
- [cloudevents/spec `cloudevents/spec.md`](https://raw.githubusercontent.com/cloudevents/spec/main/cloudevents/spec.md) — version 1.0.3-wip as fetched; authoritative for `specversion`.
- [json-schema.org — `$schema` reference](https://json-schema.org/understanding-json-schema/reference/schema) and [structuring / `$id`](https://json-schema.org/understanding-json-schema/structuring) — current site, reachable directly.
- [open-telemetry/opentelemetry-specification `schemas/README.md`](https://raw.githubusercontent.com/open-telemetry/opentelemetry-specification/main/specification/schemas/README.md), [`schemas/file_format_v1.1.0.md`](https://raw.githubusercontent.com/open-telemetry/opentelemetry-specification/main/specification/schemas/file_format_v1.1.0.md), [`versioning-and-stability.md`](https://raw.githubusercontent.com/open-telemetry/opentelemetry-specification/main/specification/versioning-and-stability.md) — authoritative for `schema_url`, schema files, and OTel's stability guarantees.
- [apache/avro Specification](https://raw.githubusercontent.com/apache/avro/main/doc/content/en/docs/%2B%2Bversion%2B%2B/Specification/_index.md) — Schema Resolution and Parsing Canonical Form.
- [protocolbuffers/protocolbuffers.github.io `proto3.md`](https://raw.githubusercontent.com/protocolbuffers/protocolbuffers.github.io/main/content/programming-guides/proto3.md) — "Updating A Message Type"; source of protobuf.dev.
- [aip-dev/google.aip.dev `aip/general/0180.md`](https://raw.githubusercontent.com/aip-dev/google.aip.dev/master/aip/general/0180.md) — AIP-180 backwards compatibility.
- [JasperFx/marten `docs/events/versioning.md`](https://raw.githubusercontent.com/JasperFx/marten/master/docs/events/versioning.md) — the reachable primary source on upcasting; cites Greg Young's *Versioning in an Event Sourced System* and Oskar Dudycz's posts, both on blocked hosts.
- Search-surfaced only (second-hand, flagged in place): Stripe's [API versioning docs](https://docs.stripe.com/api/versioning) and [blog post](https://stripe.com/blog/api-versioning); [Confluent schema evolution and compatibility types](https://docs.confluent.io/platform/current/schema-registry/fundamentals/schema-evolution.html); [Datomic transaction/log docs](https://docs.datomic.com/reference/log.html).
