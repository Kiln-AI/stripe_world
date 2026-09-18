---
status: complete
---

# Functional Spec: State Format

What `state()` returns, how a caller chooses the format it returns, and what a world and a
consumer may rely on for as long as a saved state document exists. Written from
`project_overview.md` and the decisions log in `ideas_for_discussion.md`, which records why each
choice was made; this document records what is built. Where the two disagree, this one is wrong
and should be fixed.

This is the second version of the spec. The first was built against a Seahaven where an instance
was one store; `project_overview.md` ("Attempted and restarted") says what happened. Every
composition term below -- node, canonical path, `main`, `NodeReport` -- is defined by
`specs/projects/world_composition/functional_spec.md` and is cited from there, not redefined.

## 1. Purpose

An eval or RL framework runs an episode against a Seahaven world, reads `state()` once at the end,
and saves it as `final_state`. Judges run against that document, possibly hundreds per world, and
possibly years later, against a Seahaven that has moved on. Seahaven has no internal reward, so
this document is the only thing an episode's success is judged on.

Three consequences drive everything below:

- **The document is state, not reason.** It records what the episode left behind in the database,
  and enough provenance to interpret it. By default it does not record the tool calls that
  produced it: that is the harness's trace. A harness whose trace spans two worlds, or mixes real
  and synthetic tools, cannot line that trace up with this document after the fact, so one format
  (§4.2) carries the calls as well. Results never; the harness has those verbatim.
- **Nothing derivable, nothing the consumer already has.** No net diff beside the log, no counts,
  no whole rows, no whole database, no schema. Every one of those is a lookup or a fold away.
- **Versioned and frozen.** A document says which format it is in; a published format never
  changes meaning; a world pins the format it produces so that upgrading Seahaven never changes
  what a running eval saves.

## 2. Concepts

- **State document.** A JSON object, the whole answer to `state()`: a framework-owned
  **envelope** of provenance fields, plus one key, `state`, holding the **formatter's output**.
- **Format.** A name of the form `<family>/<major>`, such as `seahaven.state/1`. It names the
  shape of `state`, not of the envelope. Three are built in (§3, §4). A world may register more
  (§6). A format, once published in a Seahaven release, never changes in a way a reader could
  observe (§10).
- **Change log.** The ordered record of every row the instance changed after startup, one record
  per row per call, across every node of the instance. It is the only database content in the
  document, and the only record the framework keeps: the net diff is a fold of it (§3.6), defined
  by this spec and computed by nobody in production. `inst.changes()`, today's cumulative net diff,
  is removed (§8, §11).
- **Call log.** The ordered record of every call dispatched to the instance: the tool's name as
  the caller gave it, the arguments, and the error if it raised. Kept always; published only by
  `seahaven.state+calls/1` (§4.2).
- **Call ordinal `i`.** The position of a call among every call dispatched to the instance, from 0
  (§7). Log records carry it; it groups a call's records together and indexes the call log. It is
  not a join key to anything outside the document.
- **Node and path.** An instance is a tree of nodes, one store each; the root's path is `main`
  and every other node is named by its canonical path (composition FS §1, §2). A world that adds
  nothing is one node, `main`.

## 3. The document, and the format `seahaven.state/1`

### 3.1 Root: the envelope and `state`

```jsonc
{
  "format": "seahaven.state/1",
  "seahaven_version": "0.0.1",
  "world": {"name": "projecttracker", "version": "1.0.0"},     // the root world
  "composition": {                                             // every node, keyed by path
    "main":     {"world": "projecttracker", "world_version": "1.0.0", "scope": null,
                 "aliases": [], "schema_hash": "…", "frozen_world_version": null},
    "payments": {"world": "payments",       "world_version": "0.3.0", "scope": null,
                 "aliases": [], "schema_hash": "…", "frozen_world_version": null}
  },
  "fixture": {                                                 // null for a blank instance
    "id": "small_startup",
    "nodes": {"main": {"file_sha256": "…"}, "payments": {"file_sha256": "…"}}
  },
  "episode_id": "…",
  "seed": 7,                                                   // as given to reset; null if none
  "now": "2026-03-04T09:00:00Z",
  "startup": {"user_id": "u_12"},                              // {} when none were given
  "call_count": 3,
  "state": {"db": {"log": [ … ]}}                              // the formatter's output
}
```

Everything above `state` is the **envelope**: written by the framework, identical for every
format, built-in or custom. `state` is the formatter's output and the only thing `format`
describes. For `seahaven.state/1`, `state` is `{"db": {"log": [...]}}`.

| Field | Meaning |
|---|---|
| `format` | The name of the format that produced `state`, always first. A reader checks it before reading `state`. |
| `seahaven_version` | `seahaven.__version__` of the producing process. Informational; a reader keys on `format`, never on this. |
| `world.name`, `world.version` | The root world's `World.name` and `World.version`. Kept beside `composition` so that the root's identity is two typed, non-nullable fields rather than a lookup. `world.version` is the world's identity: the docs say a schema, tool or state-format change bumps it. |
| `composition` | One entry per node of the instance, keyed by canonical path, root first: `world`, `world_version`, `scope`, `aliases`, `schema_hash` and `frozen_world_version`, which are `NodeReport`'s fields (composition FS §6.1) less `path`, the key. A world that adds nothing has the one entry `main`. `null` when there is no instance (§3.5). |
| `fixture` | `{id, nodes}` from the fixture's sidecar, or `null` for an instance built from the DDL. `nodes` is keyed by path and holds each node's `file_sha256`; a world that adds nothing has the one entry `main`. With `composition`, this is the lookup for the starting state. |
| `episode_id` | Over OpenEnv, the session's episode id (the one given to `reset`, or the generated one). In-process, the instance id. |
| `seed` | The `seed=` the caller gave: an integer, or `null`. The API narrows to `int \| None` (§8). |
| `now` | The instance clock as an ISO-8601 instant, the same string `inst.clock.iso()` answers. |
| `startup` | The reset keywords beyond `fixture`, `seed`, `now` and `state_format`, rendered as JSON at instance creation. A startup hook receives the caller's own value; this carries that value's JSON rendering, so a `datetime` or a model is text or an object here. An object; empty when there were none. |
| `call_count` | How many calls have been dispatched to the instance so far (§7). Not OpenEnv's `step_count`, which also counts tool listings. |
| `state` | The formatter's output. Under `seahaven.state/1`: `{"db": {"log": [...]}}`, the change log (§3.2). |

No other root fields. A reader must ignore envelope fields it does not know (§10), so a later
addition does not break it; `state`'s contents are exactly what `format` defines. `path` is the
join key across the document: `composition[p]` describes a node, `fixture.nodes[p]` its starting
file, and a log record's `world` names one.

### 3.2 The change log

`state.db.log` is one flat list across every node. Each record is one row changed by one call:

```jsonc
{"i": 1, "world": "main", "table": "issues", "op": "update",
 "key": {"id": "iss_3"},
 "before": {"status": "open", "updated_at": "2026-03-01T14:22:10Z"},
 "after":  {"status": "done", "updated_at": "2026-03-04T09:00:00Z"}}
```

| Field | Meaning |
|---|---|
| `i` | The call ordinal (§7) of the call that made the change, or `null` for a write made with no call in flight (`inst.bulk()`). |
| `world` | The canonical path of the node the row belongs to: `main` for the root, the added node's path otherwise. Exactly `Change.world` as composition FS §6.2 defines it; adopted, not redefined. A world that adds nothing writes `main` on every record. |
| `table` | The table name within that node. |
| `op` | `"insert"`, `"update"` or `"delete"`. |
| `key` | The row's primary key, as `{column: value}` in primary-key column order. |
| `before` | `null` for an insert. For a delete, the whole row as it was. For an update, exactly the non-key columns the call changed, with their old values. |
| `after` | `null` for a delete. For an insert, the whole row. For an update, exactly the non-key columns the call changed, with their new values. |

Rules:

- **Net per call.** A call is one transaction per node it touches. Its records are the net
  difference between the instance before the call and after it, per row: a row inserted and
  deleted in the same call leaves no record, a row updated back to its original values leaves
  none, an insert followed by an update in one call is one insert with the final values. That is
  SQLite's changeset semantics applied to one call.
- **Not net across calls.** A row touched in two calls appears twice, once per call. The docs say
  so in the two places it bites: counting rows straight off the log overcounts, so fold or
  deduplicate by key first; and two episodes with the same end state can have different logs, so
  "same end state" compares folds (§3.6), never logs.
- **Every node, one list.** A call that writes to the root and to an added node produces records
  for both, under the same `i`, distinguished by `world`. A nested call made by a tool through
  `ctx.worlds.<name>.call(...)` is part of the call that made it (§7): its rows carry the outer
  call's `i`.
- **The key is never repeated** inside `before` or `after` on an update. On an insert and a
  delete, the whole row includes the key columns, because that is the row.
- **A primary-key rewrite is a delete plus an insert**, never an update. Documented beside `op`.
- **Empty is empty.** A call that changed nothing has no records. A call that raised and rolled
  back has none. A read-only tool has none.
- **What is tracked** is what today's per-node changeset tracks: every table of every node except
  those in that node's world's `World(untracked_tables=...)`, FTS5 shadow tables, and virtual
  tables. A table with no explicit primary key is refused at instance creation, as today. A row
  with `NULL` in any primary-key column is never recorded, which is SQLite's rule; it cannot arise
  in a linted world, because a STRICT table refuses `NULL` in a primary-key column and SH101
  requires STRICT (measured 2026-09-17 on SQLite 3.45.1).
- **Startup is not in the log.** Startup hooks run at instance creation, before any call, and no
  session records them. Every write after that is in the log, including `inst.bulk()` writes,
  which carry `i: null`.
- **A call made inside `bulk()` belongs to the block.** `inst.call(...)` inside an `inst.bulk()`
  block is supported, and the rows it writes go into the block's transactions: they commit or
  roll back with the block, not with the call. They are therefore the block's writes and carry
  `i: null` like the rest of them, once each; a block that raised leaves none of them. The call
  still takes its ordinal (§7) and its call-log entry, so `call_count` counts it either way.
- **No cap.** The log is as long as the episode made it. A consumer that wants less filters after
  the fact or registers a custom formatter (§6); the framework truncates nothing.

### 3.3 Order

Records are ordered by call: every record of call 0 before every record of call 1. Records with
`i: null` are ordered by when their transaction committed relative to the calls around them.
Within one call, records are sorted by `world`, then `table`, then the key's values in primary-key
column order, ascending, all as plain comparisons of the published values: `world` and `table`
are strings, and a key value is compared by SQLite's cross-type rank over the rendered value (§3.4)
so a blob key sorts by its base64 text, which is the only form a reader holds. `main` therefore
sorts alphabetically among child paths, not first; a reader that wants the root first has the key
to do it. Every tracked table is STRICT, so a key column has one type and the comparison is well
defined. Two identical episodes produce byte-identical logs.

### 3.4 Values

Every value is a JSON value, mapped from SQLite's storage classes:

| SQLite | JSON |
|---|---|
| INTEGER | number. SQLite integers are 64-bit; a JavaScript reader loses precision above 2^53. The docs say so; the format does not work around it. |
| REAL | number. An infinity renders as `null`: JSON carries neither infinities nor NaN, and SQLite itself stores NaN as NULL, so this is SQLite's own coercion one step further. Less precise, not wrong; a reader cannot tell a NULL column from an infinite one. The conversion carries a one-line comment stating that trade. |
| TEXT | string |
| BLOB | string, base64 |
| NULL | `null` |

### 3.5 The document before any call, and before `reset`

- After `reset` and before any call: the full envelope, `call_count: 0`, `state.db.log: []`.
- Over OpenEnv, before the first `reset`: there is no instance, and the **world's pinned**
  formatter runs with no instance (§6). The envelope is the framework's regardless of format:
  `format`, `seahaven_version` and `world` answered; `composition`, `fixture`, `episode_id`,
  `seed`, `now` and `startup` `null`; `call_count` 0. For the built-ins `state` is
  `{"db": {"log": []}}` (plus `"calls": []` under §4.2). A blank instance is distinguishable from
  no instance because a blank instance has a `now` and a `composition`. After `reset`, the
  instance's formatter runs. Never a default.

### 3.6 The fold, defined but not shipped

The net diff of an episode is the fold of its log, and the format defines the fold so that any
reader, in any language, computes the same one:

1. Group records by `(world, table, key)`; fold each group in log order, starting from
   "untouched".
2. Composition. Untouched + any record is that record, whatever its `op`: a row the fixture
   already held opens its group with an update or a delete, not an insert. Then: insert + update =
   insert with the update's `after` overlaid; insert + delete = untouched; update + update = update
   with `before` from the first and `after` from the last, dropping any column whose folded
   `after` equals its folded `before`; update + delete = delete, with `before` being the delete's
   row with the update's `before` values overlaid; delete + insert = update if the rows differ
   (`before` the deleted row, `after` the inserted row, reduced to the differing non-key columns),
   else untouched.
3. Drop groups that folded to untouched. Sort by `(world, table, key)` as §3.3.

This is SQLite's changeset semantics over the whole episode, and SQLite is the oracle: a test
opens a long-lived session of its own on every node's connection for the whole episode, renders
each changeset with the node's path, and asserts that the fold of the log equals the union, on
every episode shape the suite exercises, single-node and composite (§13). Both the fold and the
oracle are test code, not public API, in this release.

## 4. The other built-in formats

Both share §3's envelope. Neither combines with the other: a caller that wants two variations at
once registers a formatter that reads two built-ins and merges them (§6).

### 4.1 `seahaven.state+last_step/1`

The same `state` shape as `seahaven.state/1`, with one difference: `state.db.log` holds only the
records whose `i` equals `call_count - 1`, the most recent call. Records with `i: null` are never
in it.

It is idempotent: the scope is the call counter, not when `state()` was last read, so two reads
between calls answer the same document. It exists for a caller that reads `state()` after every
step, such as OpenEnv's rollout harness, which embeds the state in each step's record: under
`seahaven.state/1` that is the whole log per step, quadratic over an episode. Under this format
the per-step documents concatenate into the full log; a skipped read shows as a jump in
`call_count`; before any call the log is empty; and a harness that saves only the last document
as `final_state` has only the last call's changes, which is the trade the caller chose.

### 4.2 `seahaven.state+calls/1`

`seahaven.state/1`'s `state` plus one key, `calls`: the call log, one entry per dispatched call,
in dispatch order, so that a log record's `i` is its index.

```jsonc
"state": {
  "db": {"log": [ … ]},
  "calls": [
    {"tool": "create_issue", "arguments": {"title": "Ship it"}, "error": null},
    {"tool": "close_issue",  "arguments": {"id": "iss_9"},      "error": "no issue 'iss_9'"}
  ]
}
```

| Field | Meaning |
|---|---|
| `tool` | The name the caller used: under composition, the name on the root's tool surface, prefix included, not the owning world's own name for it. |
| `arguments` | The arguments exactly as the call carried them, never re-serialised: over OpenEnv, the JSON that arrived in the `CallToolAction`; in process, the values the caller passed. Copied at capture, so a tool that alters what it was given does not alter the record. A document is JSON-able exactly when its calls' arguments were; over the wire they always are. |
| `error` | `null` if the call returned, else the message of what it raised: a `ToolError`'s message, or any other exception's. |

No result: the harness has it verbatim, and a result can be a whole listing. No counts, per tool
or otherwise: every count is a pass over `calls`. Only dispatched calls (§7): a name the world
refused never reached it and is the harness's to record; a control tool and a tool listing are
not calls. The call log is kept for every instance whether or not this format is ever read, so
`inst.state(format="seahaven.state+calls/1")` answers on any instance; arguments are small and
the cost is bounded by the episode.

It exists for a consumer that cannot line the harness's trace up with this document -- a trace
that spans two worlds, or interleaves real and synthetic tools -- and needs the document to carry
its own account of what was asked. Most consumers do not; it is not the scaffold's pin.

## 5. Choosing a format

- `World(state_format="seahaven.state/1")` is a **required** constructor argument. There is no
  default. A `World` constructed without it raises at construction, and the message names the
  current built-in formats. Every `World(...)` in the repo gains the argument, including the six
  composite test worlds under `tests/worlds/` and the helpers that build worlds (`build_world`,
  `composable_world`, `make_world`); accepted as a breaking change, since the framework is private
  and pre-alpha and every world is ours. The scaffold (`seahaven new`) writes
  `state_format="seahaven.state/1"` into the generated `world.py`, so a pin is chosen once, at the
  world's creation.
- **The root decides.** An instance is made from one root world, and the root's pin is the
  instance's format; an added world's pin is not consulted, and neither are its registrations
  (§6). A world's pin is what it produces when it is the root -- of its own instances, or of a
  tree -- and every `World` may be a root, which is why every one carries a pin.
- `reset(state_format=...)` over OpenEnv and `world.instance(state_format=...)` in-process
  override the root's pin for that instance. `state_format` joins `fixture`, `seed` and `now` as a
  reserved reset keyword: a startup hook that names a parameter `state_format` fails at
  registration, as one naming `fixture` does today.
- An unknown format name is an error before any instance is created. A `seahaven.` name that is
  not a built-in fails at `World(...)`. A custom name can only be checked at instance creation,
  because a world registers its formatters after its `World(...)` line runs; so `World(state_format=
  "acme.state/1")` is accepted at construction and fails at the first `instance`/`reset` if nothing
  registered it on the root by then. Over OpenEnv a refused reset surfaces as `EXECUTION_ERROR` and
  the session stays open, as any refused reset does.
- The format is fixed for the life of the instance. `inst.state()` answers it; `inst.state(format=
  "...")` answers another format registered on the root for the same instance, in-process only.
  Over OpenEnv the state message carries no arguments, so the instance's format is the only one
  reachable.
- The docs recommend a world change its pin only with a `World.version` bump. Not enforced.

## 6. Custom formatters

A world may register formats of its own, for a caller whose needs differ from the built-ins:

```python
@world.state_format("acme.state/1")
def acme_state(world: seahaven.World, instance: seahaven.Instance | None) -> dict[str, Any]: ...
```

- The name must contain exactly one `/` followed by a positive integer, and must not begin with
  `seahaven.`, which is reserved for built-ins.
- The function receives the world and the instance, or `None` for the instance before the first
  `reset` over OpenEnv (§3.5, §9), and returns a JSON-serialisable dict: **the value of `state`,
  and nothing else.** The framework writes the envelope around it, so a custom format can neither
  omit nor misspell provenance. With an instance it reads `instance.change_log()`,
  `instance.call_log()` and `instance.call_count` (§8), and anything else public on the instance;
  with `None` it defines its own "no instance yet" value, and one that raises on `None` makes
  pre-reset `state` a `WorldBug`, which is the formatter author's contract to keep. A formatter
  that wants a variation of a built-in reads `instance.state(format="seahaven.state/1")["state"]`
  and edits it; one that wants two built-ins at once reads both and merges them. The document a
  formatter is handed is a copy: editing it in place edits nothing the instance holds.
- Registration is open for the life of the world, like tools and middleware, and an extension
  may register one. Registering a name twice, or a built-in name, fails. Registering a format does
  not invalidate a sealed composition: formats are not part of a seal (composition FS §2), and an
  added world's registrations are never consulted.
- Formats resolve against the root world, and only the root's. A formatter registered on the root
  sees the whole log, every node's rows included, and formats all of it. A world's own
  registrations serve it when it is the root and are not inherited by a root that adds it: a
  formatter reads other nodes' rows, and one written for a world alone has no business being
  resolvable from a tree it did not know about. Every formatter, the built-ins included, must
  accept a log whose `world` values name more than one node.
- Formatters run under the instance lock and never in a transaction; they read, they do not
  write. One that writes raises `WorldBug`.

## 7. The call ordinal

`i` counts every call dispatched to the instance, from 0, in dispatch order: every `inst.call`
in process, by name or by function, and every `CallToolAction` over OpenEnv, including a call that
raised a `ToolError`. It excludes what never reached the world: a name refused as `UnknownTool`, a
function `call()` cannot resolve (a `WorldBug`), control tools, and tool listing (`ListToolsAction`,
`inst.tools()`). A nested call -- a tool reaching another node through `ctx.worlds.<name>.call(...)`
-- takes no ordinal of its own: the caller made one call, and everything it did, on every node,
belongs to that call's `i`.

`i` groups: a call's records share it, and it indexes `calls` under §4.2. It is not a join key
into the harness's trace, and the document does not claim to line up with one; a harness that
needs its trace and this document reconciled uses §4.2, where both live in one place.

`call_count` is the number of calls dispatched so far, so the last call's ordinal is
`call_count - 1`.

## 8. The in-process API

```python
inst.state()                       # the document (envelope + state), in the instance's format, as a dict
inst.state(format="seahaven.state+last_step/1")
inst.change_log()                  # list[LogRecord], the §3.2 records, in §3.3 order
inst.call_log()                    # list[CallRecord], the §4.2 entries, in dispatch order
inst.call_count                    # int
inst.composition()                 # tuple[NodeReport, ...], as today (composition FS §6.1)
world.instance("agency", seed=7, state_format="seahaven.state/1", user_id="u_12")
```

- `inst.state()` returns a plain dict, JSON-serialisable with the standard library, so a caller
  saves it with `json.dump` and nothing else. It costs serialisation only: the log is kept in
  memory and appended to as each call commits, and `state()` does no database work. The document
  is the caller's, all the way down: editing it edits nothing the instance holds. It is refused
  inside a transaction -- `bulk()`, or a tool call -- where the rows written are not committed and
  no document could describe them; read it after the block or the call returns.
- `inst.change_log()` returns the records as frozen dataclasses with a `to_dict()` of the §3.2
  shape. The records' `key`, `before` and `after` are the instance's own dicts, handed out rather
  than copied, and the docstring says so; `to_dict()` copies.
- `inst.call_log()` returns the call records as frozen dataclasses with a `to_dict()` of the §4.2
  shape; `arguments` is a copy of what the call carried, never re-serialised, so an in-process
  caller who passed something that is not JSON gets it back as it was.
- **`inst.changes()` and the `Change` class are removed** (decided 2026-09-17, reaffirmed on the
  restart). They were a second API and a second recording mechanism for data the log already
  carries, and composition's `Change.world` is exactly the log's `world`. A caller that wants the
  net diff folds the log (§3.6); for a single-call test the log is the net diff. Accepted as a
  breaking change on the same grounds as §5's. §11 gives the migration rule for every caller.
- **`seed` is `int | None` everywhere.** `world.instance(seed=)`, `InstanceManager.create`, the
  pytest marker and the seed derivation drop `bytes`, which nothing needed: the caller's value is
  hashed with the fixture id into the derived instance seed either way, and OpenEnv's
  `reset(seed=)` is already `int | None`. A `bytes` seed raises a `WorldBug`, as any other wrong
  type does today. The per-node derivation (`node_seed`) works on the derived bytes below the
  caller's value and is untouched.

## 9. Over OpenEnv

- The `state` message answers the document. `SeahavenState` is the document plus OpenEnv's
  `step_count`: every envelope field of §3.1 is a typed field on the model (`world` a `WorldRef`;
  `composition` a `dict[str, NodeRef]`; `fixture` a `FixtureRef` whose `nodes` is a
  `dict[str, FileRef]`), `state` is `dict[str, Any]` because its shape is the format's, and the
  base class's `extra="allow"` is kept so a newer server can talk to an older client.
  `step_count` is OpenEnv's and not part of the document; a reader that wants the number of calls
  uses `call_count`. The model's existing `world: str`, `fixture: str | None`, `now: str | None`
  and `composition: list[dict]` fields are replaced by the envelope's, which is a breaking change
  to `SeahavenState` and to `SeahavenClient.state()`'s return type. Accepted: no consumer outside
  this repo exists. `SeahavenClient.state().state` is the formatter's output;
  `.model_dump(exclude={"step_count"})` is the document, byte for byte what `inst.state()` answers
  in process.
- `reset(state_format=...)` is passed through like `fixture`, `seed` and `now` and never reaches a
  startup hook. `reset` mints the episode id before the instance and hands it over, so every
  document the episode produces reports the id the harness gave.
- Before the first `reset`, the document is §3.5's. `close` and a second `reset` discard the log
  with the instance.
- **Confirmation step.** The plan carries a test, against a real server over the WebSocket, that
  a client receives the whole document from the `state` message, every field of §3.1 included,
  and that the stock `GenericEnvClient` sees the identical dict. Subclass fields travel over the
  WebSocket state message while the HTTP `GET /state` route strips them (huggingface/OpenEnv#1155,
  cited from `env.py`); nothing here works unless that holds, so it is tested rather than assumed.
  The first attempt proved this on the old base and the transport did not change; it is re-proven
  on this one all the same.
- `SeahavenClient.state()` returns the `SeahavenState` model.

## 10. The compatibility contract

For every format Seahaven publishes:

- The document has two contracts. The **envelope** is the framework's: a field is only ever
  added, never removed, renamed, retyped or rebuilt, so a reader keys on field presence and on
  `seahaven_version` only for information. **`state`** is the format's: `format` is the only thing
  a reader keys on for it.
- Within a format: no field of `state` is removed or renamed; none changes type; and the
  construction of an existing field does not change, even where the type would not, so a reader
  written against `seahaven.state/1` on the day it shipped reads every `seahaven.state/1`
  document ever produced.
- A field may be added within a format only if a reader that ignores it loses nothing. Readers
  must ignore unknown fields, in the envelope and in `state`. Anything else is a new major,
  `seahaven.state/2`, a new formatter, and the old one stays callable and frozen.
- Removing a published format is a breaking Seahaven release and is not planned.
- `world.version` is the root world's contract, and `composition[p].world_version` each node's: a
  judge assumes that two documents with the same `composition` were produced by the same schemas
  and tools.

## 11. Control tools, and the removal of `changes()`

`controller_changes` is **removed** with `changes()`, and its name leaves the reserved
control-tool names. `controller_run_sql` is unchanged in behaviour, remains behind
`--include-control-tools` over the wire and always reachable in-process, and is marked deprecated:
a `DeprecationWarning` attributed to the caller's own line, once per call site under Python's
deduplication, and a docstring that names `state()` as the replacement. It leaves the docs but for
`reference/cli.md`'s one line (§12). Its removal is not scheduled in this project.

`Instance.changes()`, `changes.Change`, `changes.render()` and the per-node long-lived session
that fed them are removed. `main` extended all four for composition after the first attempt, so
the removal has more callers to move than it did; the rule for every one is fixed here so no
coding phase decides it:

| Caller | Becomes |
|---|---|
| A test asserting on `inst.changes()`'s records (op, table, key, sides, `world`) | `inst.change_log()`: `change.world` is `record.world`, the other fields keep their names. A single-call test reads the log as the net diff. |
| A test asserting on the *net* of several calls | `fold(inst.change_log())` from the test-owned fold (§3.6, §13); nothing in production computes a net. |
| A test asserting on `controller_changes` over the wire | The `state` message; in process, `inst.state()`. |
| `bench/`'s settle-order test, which reads `change.world` | `record.world` on the log. |
| A docs example | `inst.state()`, or `inst.state()["state"]["db"]["log"]` where the example is about rows. |
| A docstring or comment in `sandbox.py`, `db.py`, `control.py` that explains the session | Reworded for a session per node per call; the SQLite session extension keeps its own vocabulary (`changeset`) where the text is about SQLite. |

`bench/results/latest.md` is a dated record of a measurement and is not rewritten.

## 12. Documentation and lint

The bundled docs follow `AGENTS.md`'s "Docs style": a table of contents on a long page, the
banned-phrase list, prose at 100 columns, every command and name checked against the code, every
example executed by the docs test. The page set is `tests/test_docs.py`'s `PAGES` plus one.

- **`state()` is the primary surface in every doc that grades or inspects an episode.**
  `serving_and_openenv.md` gains the state-over-the-wire section: the `state` message answers the
  document, `SeahavenState` is the document plus `step_count`, `reset(state_format=...)`,
  `seahaven.state+last_step/1` for a per-step harness; its control-tool text goes.
  `composition.md` and `db_schema_and_fixtures.md` show the log with `world` where they showed
  `changes()`. `testing.md`'s changeset example becomes a state example. `concepts.md`'s changeset
  concept becomes the change log and the document, and links the two traps. The README's
  `changes()` example becomes `state()`. `reference/api.md` gains `Instance.state`, `change_log`,
  `call_log`, `call_count`, `LogRecord`, `CallRecord`, `World.state_format`,
  `World.pinned_state_format`, `World.resolve_state_format` and the OpenEnv models, and loses
  `Instance.changes`, `Change` and `controller_changes`. `reference/cli.md` keeps
  `--include-control-tools` with one line saying the tool is deprecated and how to see the
  warning.
- **A new `state.md`**: the document (the envelope, `composition` and `fixture.nodes` keyed by
  path, and `state`), the three built-in formats and custom registration, presented as cases in
  this order, each with its reason: `final_state` read once at the end uses `seahaven.state/1`; a
  per-step reader like OpenEnv's episode harness uses `seahaven.state+last_step/1`; a harness that
  cannot reconcile its own trace uses `seahaven.state+calls/1`; a caller whose needs differ
  registers its own. Then: the root decides the format of a tree; `state()` is read between calls,
  never inside one; the fold and the two traps (overcounting, comparing logs); the value mapping;
  the lookup for the starting state (`composition` and `fixture.nodes` by path; for a blank
  instance the DDL plus what the startup hooks wrote, which the format does not promise to make
  reproducible); the compatibility contract; and why nothing derivable is in the document. What
  the recording costs is stated only once measured (§13), as approximate, with the command.
- **`index.md`** lists `state.md`; the scaffold's `AGENTS.md.tmpl` reading list names it.
  `authoring.md` documents `World(state_format=...)` as required, that the root's pin governs a
  tree, and the reserved reset keyword.
- **Two guards** in `tests/test_docs.py`: no page contains `controller_` except `reference/cli.md`
  on exactly one line, and no page contains `changes()`.
- **Deferred work** found while building goes to `specs/projects/state_v2/backlog.md`, opened when
  first needed and closed or dismissed in a final phase before merge, as
  `specs/projects/world_composition/backlog.md` was. There is no root `BACKLOG.md`.
- **Lint:** no new rule. SH101's entry in `reference/lints.md` gains one sentence: STRICT is also
  what keeps every row visible to the change log, because a STRICT table refuses `NULL` in a
  primary-key column and such a row would otherwise never be recorded.

## 13. Test plan

Composite cases run on the committed test worlds under `tests/worlds/`: `emporium` (four nodes
from three worlds, `payments` twice under two stores) is the tree, `payments` and `shop` the
leaves. Single-node cases run on ProjectTracker and the suite's own small worlds. Test support
modules are flat files under `tests/`, as the suite's are.

- **Log shape.** For each of insert, update (one column, several columns, a column set to
  `NULL`), delete, composite key, blob, integer key: the record matches §3.2 exactly. Update
  `before`/`after` never contain key columns. An insert and a delete carry whole rows. `world` is
  `main` on a one-node world and the node's path on `emporium`.
- **Net per call, not across calls.** Insert then update in one call is one insert; in two calls
  it is an insert record then an update record. Update back to the original in one call is no
  record; across two calls it is two update records. Rolled-back call: no records. Read-only call:
  no records. The same on an added node.
- **Every node.** On `emporium`, one call that writes to the root and to `payments` produces
  records for both under one `i`; a tool's nested call into another node carries the outer `i`;
  the two `payments` stores are told apart by `world`; a `bulk()` block that writes to two nodes
  carries `i: null` on both.
- **Order.** Two calls' records are in call order; within a call, sorted per §3.3 across nodes;
  a blob key sorts by its base64; two identical episodes give byte-identical `json.dumps` output.
- **`i` and `call_count`.** A `ToolError` consumes an ordinal; a refused name, a function the
  typed `call()` cannot resolve, tool listing and control tools do not. `bulk()` writes carry
  `i: null`. `call_count` matches, and `len(call_log())` equals it.
- **The call log.** An entry per dispatched call with the name as called (the prefixed surface
  name on `emporium`), the arguments exactly as carried (a wire call's JSON round-trips through
  the document unchanged; a tool that mutates its arguments does not alter the record), `error`
  null on success and the message on a `ToolError`; no entry for a refused name or a control
  tool; nested calls add no entry.
- **The fold.** The test's own long-lived session per node over the whole episode is the oracle:
  `fold(log)` equals the union of the rendered changesets on every case above, plus a primary-key
  rewrite (delete plus insert in both), single-node and on `emporium`; and a non-vacuity check that
  the oracle saw rows.
- **Provenance.** Every envelope field against a fixture instance and a blank one, one-node and
  composite: `composition` keyed by path with `NodeReport`'s fields, `fixture.nodes` keyed by path
  with the sidecar's hashes, `world` the root; `startup` carries the keywords given and is `{}`
  otherwise; `fixture` is `null` for blank; the envelope is identical under a custom format;
  editing a document edits nothing the instance holds, envelope and records alike.
- **Formats.** `seahaven.state+last_step/1` holds only the last call's records; the concatenation
  over an episode equals the `seahaven.state/1` log; before any call it is empty.
  `seahaven.state+calls/1` carries the call log with `calls[i]` matching each record's `i`. A
  custom formatter registers, is selectable by `instance(state_format=)` and
  `reset(state_format=)`, and a name beginning with `seahaven.`, a duplicate, or a non-dict output
  is refused; its output lands under `state` with the envelope untouched; the built-in documents
  validate against `tests/state_v1.schema.json` with `additionalProperties: false`.
- **Choosing.** `World(...)` without `state_format` raises with the built-in names in the message;
  an unknown `seahaven.` name raises at `World`, an unregistered custom name at `instance` and
  `reset`, both before any instance exists; a custom pin registered after `World(...)` works; a
  hook parameter named `state_format` is refused at registration; the scaffold writes the pin and
  the scaffolded world passes `seahaven check`. On `emporium`: the root's pin governs, a leaf's
  different pin is ignored, a formatter registered on `payments` alone is not resolvable from
  `emporium`, and `payments.instance()` uses its own pin.
- **Guards.** A formatter that calls `inst.call` or `inst.bulk` is refused; a call from another
  thread while a formatter runs waits rather than being refused; `state()` inside `bulk()` or a
  tool call is refused; `state()` after `destroy()` is refused.
- **OpenEnv.** In process: `state` before `reset` is §3.5, the envelope from the framework and
  `state` from the world's pinned formatter called with `None`; after `reset` it is the document; a
  second `reset` starts a new log. Against a real server over the WebSocket: the whole document
  arrives, `SeahavenClient.state().model_dump(exclude={"step_count"})` round-trips it, the stock
  `GenericEnvClient` sees the same dict, and `reset(state_format=...)` selects the format.
- **Performance.** `state()` on an instance with 1,000 log records does no database work
  (asserted through the authorizer or a statement counter). A call's cost with recording on is
  measured, not asserted: the probe runs on ProjectTracker and on `emporium`, and the numbers go
  in the phase plan and the docs as approximate, with the command that produced them.
- **Deprecation and removal.** `controller_run_sql` warns against the caller's own line, observed
  in process; `controller_changes` is `UnknownTool`, and a world may now register a tool of that
  name; every caller of `changes()` that `main` has is moved per §11 and the suites stay green.
- **Docs.** Every new example executes; the two guards of §12 hold.

## 14. Open questions

Numbered so they can be answered by number. 1-7 were settled in the first attempt and stand; 8-15
are the restart's, settled 2026-09-17 against composition.

1. `seed` is `int | None`; `bytes` is dropped from the API (§8).
2. Infinities render as `null` (§3.4).
3. `episode_id` is the OpenEnv episode id over the wire and the instance id in process (§3.1);
   no separate `instance_id` field.
4. `inst.change_log()` (§8).
5. Formats are registered on the world and resolved against the root (§6).
6. The document is a framework-owned envelope plus `state`, the formatter's output, one level
   deep (§2, §3.1, §6, §9, §10). Custom formatters produce `state` only. Non-JSON-able startup
   keywords are refused at `reset`. The control tools' deprecation warning is tested in process.
7. `inst.changes()`, `Change` and `controller_changes` are removed (§8, §11); one API and one
   recording mechanism; the fold's oracle is a test-owned session (§3.6).
8. *Recording:* one session per node per call; a call's records on every node share its `i`
   (§3.2, §7).
9. *`world`:* the log record's node field is `world`, a string, the canonical path, `main` for
   the root -- composition FS §6.2's definition, adopted (§3.2). Sort and group on
   `(world, table, key)`.
10. *Envelope:* `composition` and `fixture.nodes`, both keyed by path; `world` kept for the root
    (§3.1).
11. *The pin:* required at `World(...)`; the root's pin governs an instance; the root's registry
    resolves (§5, §6).
12. *Removal:* `changes()`, `Change`, `render()`, `controller_changes` go; the migration rule for
    `main`'s callers is §11's table.
13. *Ordinals:* only dispatched calls consume one; `i` groups and indexes, it does not join to a
    trace; nested calls belong to their caller (§7).
14. *Calls:* `seahaven.state+calls/1`, opt-in, an array indexed by `i`, `{tool, arguments,
    error}`, dispatched calls only, arguments as the call carried them and never re-serialised,
    no results, no counts (§4.2). `composition` is `null` before the first `reset` (§3.5).
15. *Housekeeping:* `controller_run_sql` stays deprecated; `state.md` is its own page under the
    new docs style; test support is flat under `tests/`; registering a format does not `bump()`;
    the cost is measured last, as a second priority; `check_format_name` is its own rule, since a
    format name is never a path.

## 15. Non-goals

A net diff, a summary or counts in the document; tool results in the document; an ignore list; an
`indirect` flag; a whole-database or whole-row format; a schema block or fingerprint; filtering
options on `state()` or a cap on the log; a combined `+last_step+calls` built-in; a judge language,
a DSL, or a loader/helper shipped by Seahaven; removing `controller_run_sql`; changing composition
itself (this spec adopts its vocabulary and adds nothing to it).
