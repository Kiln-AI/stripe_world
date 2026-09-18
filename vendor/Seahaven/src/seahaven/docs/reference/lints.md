# Lint reference

`seahaven check` runs every rule below over the world found by the convention (the project's
package, attribute `world`; `--world module:attr` overrides it), prints each finding as one line,
and exits 1 if any finding is an `error`. Warnings alone exit 0.

```
SH101 error src/pkg/schema/001_items.sql:8  table 'items' is not STRICT  fix: append STRICT to the CREATE TABLE items
```

A finding has five fields: the code, the severity, where, what, and the edit to make. **Every
finding names the edit** rather than describing the problem, because every rule here exists for a
mistake that is otherwise made silently.

There is no autofix, no configuration file and no suppression comment. The rule set is deliberately
small, because a rule an author has to argue with is a rule an author turns off.

Codes are stable, and the gaps in the numbering are deliberate. A retired rule's number is never
reused, so a fix written against `SH203` in a world's history always means the same rule.

## The codes

| Code | Severity | Rule |
|---|---|---|
| SH101 | error | [a table that is not `STRICT`](#sh101--a-table-that-is-not-strict) |
| SH102 | error | [a table with no explicit primary key](#sh102--a-table-with-no-explicit-primary-key) |
| SH103 | error | [a wall-clock expression anywhere in the schema](#sh103--a-wall-clock-expression-anywhere-in-the-schema) |
| SH104 | error | [a schema that does not execute](#sh104--a-schema-that-does-not-execute) |
| SH201 | warning | [a wall-clock call in world code, outside `middleware/`](#sh201--a-wall-clock-call-in-world-code-outside-middleware) |
| SH203 | warning | [`random` or `uuid.uuid4()` in world code](#sh203--random-or-uuiduuid4-in-world-code) |
| SH205 | warning | [a tool with an empty description](#sh205--a-tool-with-an-empty-description) |
| SH206 | warning | [a prefixed world's description names a sibling tool the agent cannot call](#sh206--a-prefixed-worlds-description-names-a-sibling-tool-the-agent-cannot-call) |
| SH207 | warning | [one tool of a shared node contributed under two names](#sh207--one-tool-of-a-shared-node-contributed-under-two-names) |
| SH208 | error | [`.instance(` in a module under `tools/` or `middleware/`](#sh208--instance-in-a-module-under-tools-or-middleware) |
| SH209 | error | [`ctx.worlds` naming something that is not a registered child](#sh209--ctxworlds-naming-something-that-is-not-a-registered-child) |
| SH301 | error | [a module under `tools/` or `middleware/` that is never imported](#sh301--a-module-under-tools-or-middleware-that-is-never-imported) |
| SH401 | error | [a fixture sidecar that does not validate](#sh401--a-fixture-sidecar-that-does-not-validate) |
| SH402 | error | [a fixture's `file_sha256` does not match its state file](#sh402--a-fixtures-file_sha256-does-not-match-its-state-file) |
| SH403 | error | [a fixture's `schema_hash` does not match the world](#sh403--a-fixtures-schema_hash-does-not-match-the-world) |
| SH404 | error | [a fixture's `now` is not canonical](#sh404--a-fixtures-now-is-not-canonical) |
| SH405 | error | [a fixture's state file has `-wal` or `-shm` companions](#sh405--a-fixtures-state-file-has--wal-or--shm-companions) |
| SH406 | error | [a composite sidecar's `nodes` disagrees with the world's composition](#sh406--a-composite-sidecars-nodes-disagrees-with-the-worlds-composition) |
| SH501 | error | [the package does not export a `World` named `world`](#sh501--the-package-does-not-export-a-world-named-world) |
| SH502 | error | [a `Worlds` subclass annotates a name no `add_world` registered](#sh502--a-worlds-subclass-annotates-a-name-no-add_world-registered) |
| SH503 | warning | [a registered child name no declared `Worlds` subclass annotates](#sh503--a-registered-child-name-no-declared-worlds-subclass-annotates) |
| SH504 | error | [the world's composition does not seal](#sh504--the-worlds-composition-does-not-seal) |

The schema rules ask a real database rather than reading the text. The schema is built in memory and
interrogated with `PRAGMA table_list`, `PRAGMA table_info` and `sqlite_master`, so the engine
answers a `STRICT` it did not accept as one, or a primary key spelled in a way nobody thought of.
Line numbers work the other way round. SQLite keeps a normalised copy of the schema and knows
nothing about files, so the `*.sql` files are searched for the `CREATE ...` that names the object. A
world whose schema is not on disk still gets every finding, with the package directory and no line.

---

## SH101 — a table that is not `STRICT`

**Rule.** Every ordinary table in the world's schema is declared `STRICT`. FTS5 virtual tables and
their shadow tables are exempt; a virtual table cannot be `STRICT`.

**Why.** Without `STRICT`, SQLite stores whatever it is handed: a string in an `INTEGER` column, a
float in a `TEXT` one. A world is a mock of a real product's data, and an eval that grades on a
column's value needs the column to hold what the schema says. The failure without this rule is
silent and arrives weeks later as a comparison that does not match.

`STRICT` is also what keeps every row visible to the change log: a `STRICT` table refuses `NULL` in
a primary-key column, and a row with `NULL` in one is never recorded, which is SQLite's own rule.

**Fix.** `append STRICT to the CREATE TABLE <name>`. A `STRICT` table's columns must each be
declared with one of the types SQLite allows there: `INT`, `INTEGER`, `REAL`, `TEXT`, `BLOB` or
`ANY`. So a column written `VARCHAR(64)` or `BOOLEAN` has to be spelled `TEXT` or `INTEGER`.

## SH102 — a table with no explicit primary key

**Rule.** Every ordinary table declares a `PRIMARY KEY`, column-level or table-level. SQLite's
implicit `rowid` does not count.

**Why.** There are two reasons, and the second is the hard one. A row with no key cannot be
identified in a change-log record, so **a table without a primary key cannot be tracked**.
Seahaven refuses to attach one, and every write to that table is invisible to the eval grading the
run. The other reason is that a list ordered without a unique tiebreak is not deterministic, which
is the other thing this framework is for.

**Fix.** `give <name> a PRIMARY KEY; SQLite's implicit rowid is not one`. A join table takes a
composite key: `PRIMARY KEY (issue_id, label_id)`.

## SH103 — a wall-clock expression anywhere in the schema

**Rule.** No `CURRENT_TIMESTAMP`, `CURRENT_DATE`, `CURRENT_TIME`, `datetime('now')` or any relative
of them, in a column default, a trigger body, a view, a generated column or a partial index. The
whole schema is searched through `sqlite_master`, with comments and string literals excluded.

**Why.** The problem is the text rather than the instant. An instance's clock overrides make a
default in the schema read the frozen instant anyway, but what SQLite *writes* is `2026-06-01
09:00:00`, and every other door of a world writes `2026-06-01T09:00:00.000Z`. One format across
every door is the rule, and two formats in one column is a comparison that fails for a reason nobody
will find quickly.

**Fix.** `write the timestamp from world code with ctx.clock.iso(), so every door of the world uses
one format`. Make the column `NOT NULL` with no default and pass the value in.

One known false positive: `'now'` is matched as text, so a `CHECK` constraint that allows the
literal string `'now'` is reported. `'now'` is a time value to every date function SQLite has, and
there is no way to tell the two apart in `sqlite_master.sql`. A world with a status called `now` is
rarer than a world with `datetime('now')` in a trigger.

## SH104 — a schema that does not execute

**Rule.** The whole schema, applied in filename order to a blank database, runs. The message is
SQLite's own.

**Why.** A world whose schema does not execute cannot be constructed at all, because
`World.__init__` builds the schema in memory to compute the schema hash. This rule exists so that
`seahaven check` answers with a line and a fix rather than a traceback out of an import. A line and
a fix is what an authoring agent can act on.

**Fix.** `fix the statement SQLite names; every *.sql file under schema/ is run in filename order,
against a blank database`. Remember the order is *filename* order, so a table referenced by a
foreign key in `001_` and created in `002_` is a common cause.

## SH201 — a wall-clock call in world code, outside `middleware/`

**Rule.** `datetime.now`, `datetime.utcnow`, `date.today`, `time.time`, `time.monotonic` and
`time.perf_counter`, anywhere under the world's package except `middleware/`. The call's dotted name
is resolved through the module's own imports, so `datetime.now()`, `datetime.datetime.now()` and
`from datetime import datetime as dt; dt.now()` are one rule and not three.

**Why.** An instance's time is its fixture's, everywhere. A timestamp from the machine's clock in
fixture-relative data is nearly always a mistake: the row is dated years after everything around it,
and the run does not replay.

**A warning and not an error**, because it is sometimes deliberate. `middleware/` is exempt
outright, since a real clock timing a real call in a logging layer is exactly right.

**Fix.** `take the instance's time from ctx.clock.iso() or ctx.clock.now()`.

## SH203 — `random` or `uuid.uuid4()` in world code

**Rule.** Importing the standard library's `random`, calling anything on it, or calling
`uuid.uuid4()` or `uuid.uuid1()`. Importing `uuid` on its own is fine: `uuid.UUID` is how
`ctx.ids.uuid()`'s own output is parsed.

**Why.** Both read a source of entropy that is not the instance's, so the same fixture and seed stop
replaying. Replay is the one guarantee a world gets for free, and the one an eval author relies on
when comparing two runs.

**Fix.** `draw from ctx.ids: ctx.ids.uuid() for an identifier, ctx.ids.random for anything else`.
`ctx.ids.random` is a real `random.Random` seeded per instance, so `choice`, `shuffle` and `randint`
all work.

## SH205 — a tool with an empty description

**Rule.** A registered tool whose description is empty or whitespace. The rule asks the `World`
rather than the source, because a description may come from a docstring, from a `description=`, or
from whatever a factory put on the `Tool`, and only the registry knows which. Seahaven's two control
tools are exempt, because they are never listed and never reach an agent.

**Why.** The description is the whole of what an agent reads to decide whether to call the tool. A
tool without one is a tool that will not be called, or will be called wrongly.

**Fix.** `give the function a docstring, which becomes the whole description, or pass description=
to @world.tool`.

**A warning**, because a world under development has tools that do not have one yet.

## SH206 — a prefixed world's description names a sibling tool the agent cannot call

**Rule.** When an added world is contributed under a `tool_prefix`, no contributed tool's
description names another of that node's tools by its unprefixed name.

**Why.** A prefix renames the tool and never the text. Take an added world whose descriptions
cross-reference each other, with one saying "call `create_customer` first". That description keeps
saying `create_customer` on a surface where the agent can only call `stripe_create_customer`. The
agent reads the description, calls the name in it, and gets `unknown_tool`.

**Fix.** `drop the tool_prefix on that add_world, or accept the mention: check never rewrites a
description`. Rewriting the text is deliberately not an option. The description is the added world's
statement about its own product, and a host that edits it is no longer serving that world.

The finding is reported against the host's own `world.py`, where the `add_world` is, and names the
added world's file in the message. For a world installed from PyPI that file is inside
`site-packages`, which the host's author cannot edit and should not.

**A tool named after a common word will produce noise.** The match is the tool's registered name on
a word boundary and nothing more. A vendor world with a tool called `list`, `close`, `search` or
`note` therefore earns a warning for every description that uses the word in a sentence, because
"returns a list of notes" names the tool `list`. There is no way to tell the two apart from the
text, and inventing one would make the rule guess. That is why it is a warning: accept the mention
and move on.

## SH207 — one tool of a shared node contributed under two names

**Rule.** No node contributes one of its tools under more than one name on the composite surface.

**Why.** Two routes to one shared store each carry their own prefix and lists, which is two
declarations rather than a collision. The result is an agent that sees `stripe_create_charge` and
`billing_create_charge`, both writing to one account, with nothing in either listing saying so.
Sometimes that is the client's real surface, which is why it is a warning.

**Fix.** `give every route but one tool_allow_list=[] or a tool_block_list naming it, or accept that
the agent sees one account twice`.

## SH208 — `.instance(` in a module under `tools/` or `middleware/`

**Rule.** No call of an attribute named `instance` in a module under either registering directory.

**Why.** Creating an instance from inside a call is a `WorldBug` the moment the line runs: the
dispatcher sets a per-thread flag and `World.instance(...)` refuses. A world reaches another world's
store through `ctx.worlds.<name>`, which Seahaven resolves once per instance. That per-instance
resolution is what makes sharing by identity work, and a handler holding a store of its own defeats
it.

**Fix.** `reach an added world with ctx.worlds.<name>.call(...); world.instance(...) belongs in an
eval or a test, never in a call`.

## SH209 — `ctx.worlds` naming something that is not a registered child

**Rule.** Every literal `ctx.worlds.<name>` and `ctx.worlds["<name>"]` in the world's package names
a `name=` some `add_world` on this world registered.

**Why.** The container answers any attribute, for a type checker's sake, and refuses an unregistered
one at run time. So a misspelt child shows up as a `WorldBug` in an eval rather than as a
`NameError` in development.

**Fix.** `name one of the worlds it adds (<names>), or add the one it means`. Only the literal
spelling is checked. A name computed at run time is outside what a lint can see, and
`ctx.worlds.<child>.worlds.<grandchild>` names a child's registrations rather than this world's, so
it is left alone.

## SH301 — a module under `tools/` or `middleware/` that is never imported

**Rule.** Every module under those two directories is in `sys.modules` after the world's package has
been imported. The rule covers those two directories and not the whole package, because a world may
put helpers anywhere and these are the two the layout gives a meaning to.

**Why.** Registration happens at import. A tool module nobody imports registers nothing, and the
world silently has one tool fewer than its author believes. The symptom is an `unknown_tool` from an
eval weeks later, which says nothing about the missing import line. That is why this is an error.

**Fix.** `import it from <package>/__init__.py, or from <package>.tools`. A module that is genuinely
shared and registers nothing belongs beside the tool modules with a leading underscore *and* an
import from the module that uses it, which is what puts it in `sys.modules`.

## SH401 — a fixture sidecar that does not validate

**Rule.** `fixture.yaml` parses as YAML, carries `format_version: 1` or `2`, and validates against
the sidecar model. At version 2 that includes every entry of `nodes`: no two may share a `path`, and
no two may name one state file, the root's `state.sqlite` included. This rule is checked first and
alone, because the other fixture rules read fields a broken sidecar does not have. A fixture that
fails this one is reported once and left.

**Why.** The sidecar is what says where the state came from. Without it, nothing can say whether the
file matches the world.

**Fix.** `regenerate it with seahaven fixture freeze or seahaven fixture fork`. Never hand-edit a
sidecar. Everything in it is derived from the instance that was frozen.

## SH402 — a fixture's `file_sha256` does not match its state file

**Rule.** `state.sqlite` exists, is a real file rather than a symbolic link, and hashes to what the
sidecar says.

**Why.** A fixture is immutable, and this rule enforces that against the file rather than against a
convention. A mismatch means the file was changed after it was frozen, usually by opening it and
writing to it, so every eval that used it since started from state nobody meant.

A link is the same defect by another route. Everything that reads a fixture would otherwise follow
the link, the hash included, so a link out of the directory with the target's digest in the sidecar
would agree with itself while the instance ran on a database the fixture does not contain.

Seahaven makes the same checks when it copies a fixture. This rule finds the problem before a commit
rather than in a run.

**Fix.** `fixtures are immutable: fork it, change the fork, and freeze that`. If the file is missing
or is a link, regenerate it instead.

**Per node.** A composite fixture holds one state file per store, and each is checked against its
own `file_sha256` with the node's path in the message.

## SH403 — a fixture's `schema_hash` does not match the world

**Rule.** The hash of the normalised schema in the sidecar equals the loaded world's.

**Why.** The fixture was frozen from a different schema, so a tool of the current world may query a
column it does not have. Instance creation refuses such a fixture outright, and this is the same
check, run before a commit.

**Fix.** `regenerate it with seahaven fixture freeze or seahaven fixture fork`. Regenerate **every**
fixture of the world, in parent order, because they all conform to one schema. This is the cost a
schema change carries, and it is why the generator script is committed.

**Per node.** Each store of a composite fixture is checked against the hash of *its own* world's
schema, with the node's path in the message, so a dependency whose schema moved is reported as that
node and not as the host's. That half of the rule needs the tree, and it is the only part of the
rule that says nothing when the composition does not seal.

## SH404 — a fixture's `now` is not canonical

**Rule.** `now` round-trips through the clock's own formatter unchanged: UTC, milliseconds and a
trailing `Z`, as in `2026-06-01T09:00:00.000Z`.

**Why.** Every instance of the fixture takes its clock from this string, and every timestamp
comparison in the world is a text comparison. A value in another format sorts wrongly against every
row in the fixture.

**Fix.** `canonical is 2026-06-01T09:00:00.000Z: UTC, milliseconds, trailing Z`. In practice: pass
`--now` in that format to `seahaven fixture freeze`.

This rule stays whole-sidecar even for a composite fixture, unlike the three rules around it,
because there is one clock per instance and no store has a `now` of its own.

## SH405 — a fixture's state file has `-wal` or `-shm` companions

**Rule.** No `state.sqlite-wal` or `state.sqlite-shm` beside the state file, and none beside any of
the per-node state files a composite fixture holds.

**Why.** A fixture is checkpointed and vacuumed before it is sealed, so either file means the
database was opened for writing after it was frozen. Whatever the fixture's hash covers, it does not
cover what is in those two files.

**Fix.** `regenerate it with seahaven fixture freeze or seahaven fixture fork`.

**Not the file mode.** `freeze` does seal `state.sqlite` at `0444`, and this rule does not check
that. Git records only the executable bit, so every committed fixture comes back from a clone at
`0644`, and a mode check would fire on every correct world after every clone. What the seal guards
against is the file changing, and SH402 catches that over a hash version control does preserve.
Sealing the file is still what stops a live instance writing a fixture in place, so `freeze` still
does it. Only the *check* cannot ask.

## SH406 — a composite sidecar's `nodes` disagrees with the world's composition

**Rule.** The sidecar describes the tree the world resolves to now: the same added nodes, each in
the same scope, reached by the same alias edges, and a version-2 sidecar for a composite world and a
version-1 one for a world with a single store.

**Why.** A fixture holds one file per node, and a node added, removed or moved into another scope
makes the set of files it holds the wrong set. Instance creation refuses such a fixture with this
same sentence. This rule says it before a commit rather than in a run.

**Fix.** `regenerate it with seahaven fixture freeze or seahaven fixture fork`. A node's *schema*
drifting is SH403 instead, per node, because that is one world changing rather than the tree.

## SH501 — the package does not export a `World` named `world`

**Rule.** The project's package imports, and has an attribute `world` that is a `seahaven.World`.
The package is the normalised `[project] name` from the nearest `pyproject.toml`. This is the one
finding discovery raises rather than a rule module, because there is no world to lint.

**Why.** Every part of the tooling relies on that convention: the CLI, the pytest plugin and `serve`
all find a world this way. A package that does not follow it works in process and nowhere else.

**Fix.** `give the package a world, or point at the one it has`. The message names the module and
the attribute it looked for, and `--world module:attr` is the override for a layout the convention
misses.

An import that fails for another reason is reported here too, with the exception's own last line.
`check` never answers with a traceback.

## SH502 — a `Worlds` subclass annotates a name no `add_world` registered

**Rule.** Every attribute annotated on a `seahaven.Worlds` subclass declared in the world's package
is the `name=` of an `add_world` on that world.

**Why.** The subclass is never instantiated. It exists so that a type checker knows which children
`ctx.worlds` has, and `Worlds.__getattr__` satisfies any name at all. Nothing but this rule binds
the declaration to the registrations, so an attribute that outlived the `add_world` it was written
for type-checks perfectly and then fails in an eval.

**Fix.** `annotate one of the names it does add (<names>), or drop the attribute`.

Names beginning with an underscore are not declarations and are never reported. No child can be
spelled that way, and `Worlds.__getattr__` lets such a name fall to ordinary attribute lookup, so
that a subclass may carry `__slots__` or a private field. An ordinary name *is* reported whatever
its annotation says, so a `ClassVar[str]` on a `Worlds` subclass is flagged like any other. The
class exists to declare handles and nothing else.

## SH503 — a registered child name no declared `Worlds` subclass annotates

**Rule.** When the world declares a `Worlds` subclass, every registered child name is annotated on
one.

**Why.** A half-declared class is worse than none. The children it lists are checked and the one it
forgot is not, silently, in exactly the world whose author asked for the checking. Declaring the
class stays optional, which is why nothing is said about a world that declares none.

**Fix.** ``add `<name>: seahaven.WorldHandle` to <class>``.

## SH504 — the world's composition does not seal

**Rule.** The world resolves to a tree: no list naming a tool the added world does not contribute,
no two routes producing one tool name, no contributed name that is reserved or is not a valid tool
name, no `startup` keyword bound to two values, and no more added stores than SQLite can attach.

**Why.** A composition is sealed lazily, at the first use of the tree. It has to be, because a
host's own tools are registered by imports that run after its `add_world` lines, and a world can
never be told what added it. So these are registration errors that raise from `world.instance(...)`,
`inst.tools()`, a call, a freeze, or from `check` itself. `check` seals as its first act, which
turns each of them into a line with the offending `add_world` on it rather than a traceback out of
the first run.

**Fix.** `correct the add_world the message names; nothing can use this world until it seals`. The
message is the seal's own, and names the path and the declaration that caused it. A world that does
not seal still gets every rule that needs no tree. Only SH206, SH207, SH406 and the per-node half of
SH403 ask for one, so the schema, coverage and declaration rules, the rest of the code rules and the
rest of the fixture rules all report in the same run.
