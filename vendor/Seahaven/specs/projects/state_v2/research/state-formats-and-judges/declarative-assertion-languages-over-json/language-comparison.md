# Declarative assertion languages over JSON — candidate-by-candidate

Scope: languages you could hand to a judge author (human or LLM) to write *hundreds* of small
boolean/numeric assertions over one JSON document — specifically a Seahaven-shaped
`{schema_version, episode_id, changes: [{table, op, key, before, after}, ...]}` state blob.

Everything marked **[verified]** was run locally in this session against a real fixture; the
runnable transcript, exact expressions and timings are in
[hands-on-trials.md](./hands-on-trials.md). Everything else is cited to a primary source.

---

## The five things a DB-diff judge has to be able to say

I used these as the test battery throughout (details and code in
[hands-on-trials.md](./hands-on-trials.md)):

| # | Judge | Why it's the bar |
|---|-------|------------------|
| J1 | count of rows in table `tickets` where `before.status == "open"` and `after.status == "closed"` is 2 | filter + count over a list of heterogeneous records |
| J2 | no `delete` in table `audit_log` | negative / "agent must not have done X" |
| J3 | some insert into `emails` whose `after.to` contains `bob@` | substring / regex on a field |
| J4 | every ticket that went open→closed has a matching `ticket_events` insert with `after.ticket_id == key.id` | **cross-list join** — the one that separates the languages |
| J5 | the set of changed tables is exactly `{emails, ticket_events, tickets}` | set / distinct aggregate |

J4 is the discriminator. A DB diff is a flat list of rows from *many* tables; almost every
interesting judge about referential consequences ("closing a ticket also wrote an event",
"the refund row references the order that was cancelled") is a join between two subsets of that
one list. **Any expression language whose iteration construct rebinds the current element and
gives you no way to reach the outer scope cannot express J4 at all.**

---

## 1. CEL (Common Expression Language)

**What it is.** Google's embedded expression language. From the spec README (fetched
2026-09-14): *"CEL evaluates in linear time, is mutation free, and not Turing-complete. This
limitation is a feature of the language design, which allows the implementation to evaluate
orders of magnitude faster than equivalently sandboxed JavaScript."*
([cel-spec README](https://raw.githubusercontent.com/google/cel-spec/master/README.md))

The language definition states CEL programs are *"memory-safe," "side-effect-free,"
"terminating,"* and *"strongly-typed"*, and that CEL programs *"cannot loop forever"*
([cel-spec langdef.md](https://raw.githubusercontent.com/google/cel-spec/master/doc/langdef.md)).

**Expressiveness.** Macros `has()`, `all()`, `exists()`, `exists_one()`, `map()`, `filter()`
(langdef.md). JSON maps directly onto CEL types (null→null, Boolean→bool, Number→double,
String→string, Array→list, Object→map).

- J1: `size(state.changes.filter(c, c.table=='tickets' && c.op=='update' && c.before.status=='open' && c.after.status=='closed'))` **[verified → 2]**
- J4 (the join): `state.changes.filter(t, ...).all(t, state.changes.exists(e, e.table=='ticket_events' && e.after.ticket_id == t.key.id))` **[verified → true]** — nested comprehensions close over the outer binding, so joins work naturally.
- Weakness: **no `sum`/`avg`/`group_by`/`distinct` in the base language.** You get `size()`,
  `filter`, `map`, `all`, `exists`, `exists_one`. Summing requires an extension function or a
  `map()` + host-side fold. Set-equality (J5) needs sorting/dedup that base CEL lacks.

**Safety.** This is CEL's whole pitch, and it is backed by production hardening, not just claims.
Kubernetes evaluates untrusted CEL in the API server and documents a *cost unit* model: *"All CEL
expressions evaluated by Kubernetes are constrained by a runtime cost budget... If the CEL
interpreter executes too many instructions, the runtime cost budget will be exceeded, execution
of the expressions will be halted, and an error will result."* Plus a static *estimated cost
limit* that rejects expressions at write time
([k8s CEL reference](https://raw.githubusercontent.com/kubernetes/website/main/content/en/docs/reference/using-api/cel.md)).

The known sharp edge is in the spec itself: *"Macros can take considerably more time and space
than other constructs, and can lead to exponential behavior when nested or chained."*
(langdef.md). A nested `filter(...).all(..., exists(...))` join is exactly O(n²) — fine at
hundreds of rows, not free at 10⁵.

As of 2026-08 Google published a formal-verification framework for CEL (Z3-backed `assume`/
`assert`, satisfiability, always-true checks, counterexample generation) —
[Google Open Source Blog, 2026-08](https://opensource.googleblog.com/2026/08/securing-the-agentic-era-introducing-formal-verification-for-cel.html)
(search result; blog page itself was not fetchable from this session).

**Cross-language.** Official-ish implementations in Go (cel-go), Java (cel-java), C++ (cel-cpp),
Rust (cel-rust). Python and TS are **third-party**:
- Python: `cel-python` 0.5.0, released 2026-01-31, maintained by Cloud Custodian
  ([PyPI](https://pypi.org/pypi/cel-python/json)). Pure Python. **[verified]** correct on all
  five judges.
- Python (Rust-backed): `common-expression-language` 0.9.0, released 2026-09-08
  ([PyPI](https://pypi.org/pypi/common-expression-language/json)). **[verified]** correct, ~25×
  faster than cel-python.
- TS/JS: `@marcbachmann/cel-js` 8.0.0 (2026-07-07) and `cel-js` 0.8.2 (2025-07-11)
  ([npm registry](https://registry.npmjs.org/)). **[verified]** `@marcbachmann/cel-js` solved the
  J4 join, but diverged from cel-python on `[].all(x, x > 0)`, which it rejected with
  *"Operator overload 'int > int: bool' overlaps with 'double > int: bool'"* while cel-python
  returned `true`. **Cross-implementation divergence on edge cases is real** — do not assume a
  judge that passes in Python behaves identically in TS.

**Performance.** The unflattering number: **cel-python took ~1.28 s to evaluate one J1-shaped
judge over a 2,000-row change list** — 200 judges over that document would be ~4 minutes of pure
CPU. The Rust binding got it to ~52 ms/judge; jq's C binding to ~36 ms; SQLite over a
materialized table to ~1.2 ms. **[verified — table in hands-on-trials.md]**

**Readability / LLM-writability.** C-family infix syntax; a non-programmer can read
`c.before.status == 'open' && c.after.status == 'closed'`. LLMs write CEL well because it looks
like every other expression language; the failure modes are calling functions that don't exist in
base CEL (`sum`, `contains` on lists vs strings, `size` vs `length`) — which a type-check at
authoring time catches loudly.

**Verdict.** Best safety story of any candidate, expressive enough for J1–J4, weakest on
aggregation, and the Python runtime is slow enough that it would need the Rust binding or a
pre-materialized index for hundreds of judges.

---

## 2. jq

**What it is.** A stream-oriented JSON processing language (C implementation; manual fetched from
[jqlang/jq master docs](https://raw.githubusercontent.com/jqlang/jq/master/docs/content/manual/dev/manual.yml)).

**Expressiveness — the strongest of the field.** Built-ins include `select`, `map`, `any`, `all`,
`group_by`, `unique_by`, `min_by`/`max_by`, `sort_by`, `add`, `reduce`, `foreach`, `paths`,
`getpath`, `to_entries`, `walk`, regex (`test`/`match`/`capture`/`scan`), and an explicit
**SQL-style operator** set:

> `INDEX(stream; index_expression)` … `JOIN($idx; stream; idx_expr; join_expr)` … `IN(s)` …
> `IN(source; s)` — jq manual, "SQL-Style Operators"

**[verified]** all five judges, including J4 by set-subtraction
(`($closed - $evts) | length == 0`) and via `INDEX(...)`. jq is the only candidate where J5
(distinct table set) is a one-liner: `[.changes[].table] | unique == ["emails","ticket_events","tickets"]`.

**Safety — the worst of the field for untrusted input.** jq is not a sandbox and does not claim
to be:
- **[verified]** `SECRET_TOKEN=hunter2 jq '$ENV | {SECRET_TOKEN, HOME}'` returns
  `{"SECRET_TOKEN":"hunter2","HOME":"/root"}`. The same works through the Python binding
  (`jq` 1.12.0): `jq.compile('$ENV.SECRET_TOKEN')` returned `hunter2`, and `$ENV|keys|length`
  returned 146. **An LLM-written or user-submitted jq judge can exfiltrate the process
  environment into its own output/reason string.**
- The manual documents `import`/`include` (module loading from the filesystem), `input`/`inputs`
  (reads further inputs), `input_filename`, `$__loc__`, `debug`, `stderr`, `halt`,
  `halt_error(exit_code)`.
- **[verified]** no built-in resource limit: `jq '[range(1e12)] | length'` had to be killed by an
  external `timeout 3` (exit 124). There is no cost budget, no step cap, no memory cap.

Mitigations exist (build/limit the binding, strip `$ENV` from the environment, run in a
subprocess with rlimits), but they are your job, not the language's.

**Cross-language.** C (`jq`), Python binding `jq` 1.12.0 (2026-07-10,
[PyPI](https://pypi.org/pypi/jq/json)) — libjq via cffi, fast. **JS/TS is the weak spot**: there
is no first-class native jq for Node; options are Emscripten builds (`jq-web`), pure-JS
re-implementations of uncertain fidelity, or shelling out to the binary. Treating jq as your
cross-language contract means accepting that the TS side is an emulation.

**Readability / LLM-writability.** jq is dense and punctuation-heavy; a non-programmer will not
read `[.changes[] | select(.table=="tickets")] | length` comfortably, and pipeline/stream
semantics (a filter can emit 0, 1 or many values) is the classic jq foot-gun. LLMs write *simple*
jq very reliably and *stream-subtle* jq unreliably — the failure is usually silent (wrong
cardinality) rather than a syntax error.

**Verdict.** Maximum expressiveness, zero safety story, weak TS parity. Great as an internal
implementation detail; risky as the authored surface for untrusted or LLM-written judges.

---

## 3. JSONPath (RFC 9535)

**What it is.** Standardised February 2024 as RFC 9535. I could not fetch rfc-editor.org or
ietf.org from this session (blocked by egress policy), so I read the working-group source the RFC
was published from:
[ietf-wg-jsonpath/draft-ietf-jsonpath-base, `draft-ietf-jsonpath-base.md`](https://raw.githubusercontent.com/ietf-wg-jsonpath/draft-ietf-jsonpath-base/main/draft-ietf-jsonpath-base.md).
Quotes below are from that source.

**Expressiveness — deliberately limited, and the limits bite.**

- A JSONPath query returns a **nodelist**, not a boolean or a number. The comparator has to live
  in the host language.
- The filter grammar has **comparison operators only**:
  `comparison-op = "==" / "!=" / "<=" / ">=" / "<" / ">"`. There is **no arithmetic**.
  **[verified]** both `jsonpath-rfc9535` (Python) and `json-p3` (TS) reject
  `$.changes[?@.key.id + 1 == 2]` with a syntax error on `+`.
- Registered function extensions are exactly five (IANA table in the draft):
  `length` (string/array/object length), `count` (size of a nodelist), `match` (regex full
  match), `search` (regex substring), `value` (NodesType→ValueType).
- Typing rules block the obvious workarounds: *"`$[?match(@.timezone, 'Europe/.*') == true]`
  — not well-typed as `LogicalType` may not be used in comparisons"*; *"`$[?value(@..color)]` —
  not well-typed as `ValueType` may not be used in a test expression"*.
- No variables, no outer-scope reference. **J4 is not expressible.**
- No `sum`, `min`, `max`, `distinct`, `group_by`.

**[verified]** J1 works as a *selection* (`$.changes[?@.table=='tickets' && @.op=='update' &&
@.before.status=='open' && @.after.status=='closed']` → 2 nodes) but the "== 2" has to be applied
by the host. J3 works via `match(@.after.to, '.*bob@.*')`.

One semantic trap worth knowing: `count()` inside a filter counts within the filter's own
iteration scope, and `$[?count($.changes[?...]) == 3]` applies the filter to *every child of the
root*, returning all of them — **[verified]**, and correct per spec, but not what a naive author
expects.

**Safety.** The spec's own Security Considerations are blunt about implementation quality:

> "Historically, JSONPath has often been implemented by feeding parts of the query to an
> underlying programming language engine, e.g., JavaScript's `eval()` function. This approach is
> well known to lead to injection attacks… Instead, JSONPath implementations need to implement
> the entire syntax of the query without relying on the parsers of programming language engines."

> "…an attacker can choose to submit specially crafted JSONPath queries or query arguments that
> trigger surprisingly high, possibly exponential, CPU usage or, for example via a naive
> recursive implementation of the descendant segment, stack overflow."

That warning is not theoretical. The most-used JS JSONPath library, `jsonpath-plus`, has had two
RCEs from exactly this pattern: **CVE-2024-21534** (vm-based evaluation of JSONPath expressions,
incomplete fixes through 10.1.0) and **CVE-2025-1302** (bypass of the `eval:'safe'` mode, fixed
in 10.3.0) — see
[GitLab advisory for CVE-2025-1302](https://advisories.gitlab.com/npm/jsonpath-plus/CVE-2025-1302/)
and [Snyk SNYK-JS-JSONPATHPLUS-8719585](https://security.snyk.io/vuln/SNYK-JS-JSONPATHPLUS-8719585)
(search-surfaced; pages not individually fetched). **If you pick JSONPath, pick an RFC-9535
conformant, non-eval implementation** — `jsonpath-rfc9535` / `python-jsonpath` in Python,
`json-p3` in TS (same author, deliberately spec-conformant).

**Cross-language.** Good and improving: `jsonpath-rfc9535` 1.0.0 (2025-11-30), `python-jsonpath`
2.2.1 (2026-07-07), `json-p3` 2.3.0 (2026-09-01), `jsonpath-plus` 10.4.0 (2026-02-16)
([PyPI](https://pypi.org/) / [npm](https://registry.npmjs.org/)). Note `jsonpath-ng` (1.8.0) is
the popular Python library but predates and does not implement RFC 9535 filter semantics.

**Readability / LLM-writability.** `$.changes[?@.table=='tickets']` reads well for path-shaped
selections and badly for anything else. LLMs over-generate JSONPath that isn't RFC 9535 — the
pre-RFC dialects (Goessner-era `$..book[(@.length-1)]`, script expressions, `@.length`,
`$..*[?(@.price)]` with `?()` parens) are all over the training data. Expect frequent
"well-formed but wrong dialect" output.

**Verdict.** Excellent as a *path/selector* sublanguage (which is exactly what a diff judge needs
for "which field"), useless as a complete assertion language. Best used as the `path` half of a
`path + comparator + expected` triple, with counting/aggregation done by the host.

---

## 4. JMESPath

**What it is.** A JSON query language with a written spec
([jmespath.site specification.rst](https://raw.githubusercontent.com/jmespath/jmespath.site/master/docs/specification.rst)).

**Expressiveness (base spec).** Filters `[?expr]`, projections, multiselect, pipes, and ~25
built-in functions: `abs avg ceil contains ends_with floor join keys length map max max_by merge
min min_by not_null reverse sort sort_by starts_with sum to_array to_number to_string type
values`. Comparators are `< <= == >= > !=` only, and ordering comparators are *"**only** valid
for numbers"* — anything else "will yield a `null` value, which will result in the element being
excluded from the result list", i.e. **type errors silently become "no match"**. There is **no
arithmetic** in the base spec.

**[verified]** with `jmespath` 1.1.0 (Python):
- J1 `length(changes[?table=='tickets' && op=='update' && before.status=='open' && after.status=='closed'])` → 2 ✓
- J2, J3 ✓ (`contains(after.to, 'bob@')` works as a substring test)
- **J4 fails.** Base JMESPath has no variables and no outer-scope access; `let $x = … in …`
  raises `LexerError: Unknown token $`. **[verified]**
- J5 partial: `sort(changes[].table)` gives duplicates; there is no `distinct`/`unique`.

**The fork situation matters.** The upstream `jmespath` org is effectively dormant on the JS side
(npm `jmespath` last published 2022-01-19, `time.modified` 2022-06-19 —
[npm registry](https://registry.npmjs.org/jmespath)), and a **JMESPath Community** org maintains
active forks with the features base JMESPath lacks: `let … in …` lexical scoping
([JEP-18](https://raw.githubusercontent.com/jmespath/jmespath.jep/main/proposals/0018-lexical-scope.md),
2023-03-21, *"This enables queries that can refer to elements defined outside of their current
element, which is not currently possible"*), `group_by`, root reference `$`, arithmetic.

**[verified]** with the community forks:
- TS `@jmespath-community/jmespath` 1.3.0 (2025-07-16): J4 solved with
  `let $evts = changes[?table=='ticket_events'].after.ticket_id in length(changes[?table=='tickets' && before.status=='open' && after.status=='closed' && !contains($evts, key.id)])` → 0 ✓;
  `group_by(changes, &table) | keys(@)` ✓; `length($.changes)` ✓.
- Python `jmespath-community` 1.1.3 (2023-12-26): same three all work ✓. **But it installs under
  the module name `jmespath`**, colliding with the original package that boto3 depends on. In a
  venv with both, import order decides which you get — a real operational hazard.

So JMESPath's honest status is: **the version that can express DB-diff joins is a community fork
whose Python release is ~2¾ years old and whose package name collides with a near-universal
transitive dependency.**

**Safety.** JMESPath is a pure query language: no functions with side effects, no host escape, no
environment access. It is safe to run untrusted in the same sense JSONPath is — the residual risk
is resource exhaustion (deep projections over big documents), and neither the spec nor the
implementations define a cost budget.

**Readability / LLM-writability.** Cleaner than jq, denser than CEL. The filter syntax
`[?a=='b' && c=='d']` is readable; projections (`a[].b[].c`, `[*]` vs `[]`) are the part that
confuses both humans and models. LLMs frequently emit JMESPath-Community syntax (`let`, `$`,
arithmetic) against a base-spec runtime and get a lexer error — noisy failure, which is at least
loud.

**Verdict.** Middle of the field. Clean, safe, decent for J1–J3, and needs a fork with a stale
Python release to do J4.

---

## 5. JSONLogic

**What it is.** Rules expressed *as JSON*: `{"op": [args]}`, one operator key per node. Designed
so rules can be stored in a database and shared between front-end and back-end
([json-logic-js README](https://raw.githubusercontent.com/jwadhams/json-logic-js/master/README.md)).

**Operators** (read directly from
[logic.js](https://raw.githubusercontent.com/jwadhams/json-logic-js/master/logic.js)):
`== === != !== > >= < <= ! !! % log in cat substr + * - / min max merge var missing missing_some`,
plus special forms `if`/`?:`, `and`, `or`, `filter`, `map`, `reduce`, `all`, `none`, `some`.

**[verified]** with `json-logic-js` 2.0.5 (2024-07-09):
- J1 ✓ but ugly — there is no `length`/`count` operator, so counting is
  `{"reduce":[{"filter":[…]}, {"+":[{"var":"accumulator"},1]}, 0]}`.
- J2 ✓ (`none`), J3 ✓ (`{"in":["bob@", {"var":"after.to"}]}` as substring).
- **J4 fails, and fails *silently and wrongly*** — returned `false` where the truth is `true`.
  The cause is structural, confirmed by reading the implementation: `filter`, `map`, `all`,
  `some`, `none` all call `jsonLogic.apply(scopedLogic, datum)` — the inner scope **replaces** the
  data object entirely. There is no parent/root escape (`../`, `$`, `@root`). Cross-list joins
  are not expressible in stock JSONLogic.
- **`all` over an empty list returns `false`.** **[verified]**: `{"all":[[], {"==":[1,1]}]}` →
  `false`; the source comments it explicitly (*"All of an empty set is false"*). This contradicts
  CEL (`[].all(...)` → `true`, **[verified]**), SQL, and mathematical convention, and it is a
  silent correctness trap for judges of the form "every X satisfies Y" when no X changed.

**Safety.** Excellent by construction: it's data, not code. No attribute access, no host escape,
no eval. The parser is the JSON parser you already trust. Resource use is bounded by the rule
size and the data size.

**Cross-language.** JS `json-logic-js` 2.0.5 (2024-07-09), PHP `json-logic-php`. **Python is the
problem**: the commonly referenced `json-logic-py` family is stale (`json_logic_qubit` last
released 2018-08-15 — [PyPI](https://pypi.org/pypi/json-logic-qubit/json)). A maintained TS
re-implementation exists (`json-logic-engine` 5.0.7, 2026-04-01) with extra features, but it is a
*different* engine with different semantics, so "JSONLogic" is not one contract.

**Readability.** Worst of the field for humans. `{"and":[{"==":[{"var":"before.status"},"open"]},
{"==":[{"var":"after.status"},"closed"]}]}` is four lines of brackets for one comparison. It is
readable by *machines and form builders*, which is its actual niche — JSONLogic exists so a UI
can round-trip a rule, not so a person can read it.

**LLM-writability.** LLMs produce structurally valid JSONLogic but get the argument-order and
unary-sugar rules wrong at a meaningful rate, and there is no type checker to catch it. The
reduce-to-count idiom is not something a model reliably invents.

**Verdict.** Safest thing on the list and the least capable. Fine for `field == value` rules from
a GUI; unable to express a DB-diff join; carries two silent-wrong-answer traps
(empty-`all`, no outer scope).

---

## 6. SQL over JSON — SQLite `json_each`/`json_tree`, DuckDB

**What it is.** Load the diff into a relational engine and write the judge in SQL. SQLite's JSON
functions (`json_extract`, `json_each`, `json_tree`) let you treat the `changes` array as a table
without materialising anything.

**Expressiveness — total, and uniquely so on two axes.** **[verified]** on SQLite 3.45.1:

- J1: `SELECT count(*) FROM json_each(?, '$.changes') c WHERE json_extract(c.value,'$.table')='tickets' AND …` → 2 ✓
- J4 (join): a `WITH` + `LEFT JOIN … WHERE … IS NULL` anti-join → 0 unmatched ✓
- Grouped aggregates in one query: `SELECT json_extract(value,'$.table'), json_extract(value,'$.op'), count(*) … GROUP BY 1,2` →
  `[('emails','insert',1), ('ticket_events','insert',2), ('tickets','update',3)]` ✓
- **`json_tree` gives you *changed-column detection* generically**, which no other candidate can
  do without enumerating column names. Joining `json_each(changes)` to `json_tree(c.value,'$.after')`
  and comparing each leaf against the same key under `$.before` produced
  `[(1,'status'), (1,'updated_at'), (2,'status'), (2,'updated_at'), (3,'status'), (3,'updated_at')]`
  — i.e. "which columns actually changed, per row", computed without knowing the schema. **[verified]**

This matters a lot for Seahaven: "the agent changed `status` and nothing else" and "ignore
`updated_at` churn" are both one predicate away once you can enumerate changed leaf paths.

**Performance — the best by an order of magnitude.** **[verified]** 50 evaluations of J1 over a
2,000-row diff: 164 ms re-parsing the JSON each time; **61 ms (1.2 ms/judge) against a table
materialised once** with `CREATE TABLE ch AS SELECT value FROM json_each(?, '$.changes')`. That
is the only measured option that scales to hundreds of judges × thousands of rows without
thinking about it.

**Safety — the worst, and it's not close.** An arbitrary SQL string can `ATTACH` other databases,
read `sqlite_master`, use `readfile()`/`writefile()` in the CLI build, and in a Python
`sqlite3` connection can run DDL/DML against whatever the connection can see. You would have to
(a) run judges on a throwaway in-memory connection seeded only with the diff, (b) reject anything
that isn't a single `SELECT`, and (c) set `authorizer` callbacks / `SQLITE_DBCONFIG_DEFENSIVE`.
Doable — SQLite has `sqlite3_set_authorizer` and Python exposes `set_authorizer` — but it is a
sandbox you build, not one you inherit.

**DuckDB.** **[verified]** on DuckDB 1.5.5: `unnest(json_extract(doc,'$.changes[*]'))` +
`json_extract_string(c,'$.table')` works and `ANTI JOIN` solved J4. **Gotcha found:** the
shorthand arrow operator misbehaves when chained —
`WHERE c->>'$.table'='tickets' AND c->>'$.op'='update'` raised
`Conversion Error: Failed to cast value to numerical: {"table":"emails",…}`, while the same
predicate written with `json_extract_string()` worked, and a single `->>` in the same position
worked. Prefer the function form. DuckDB also brings a much heavier dependency (a full analytical
engine) than SQLite, which is already in Seahaven's process.

**Cross-language.** SQLite is everywhere (`sqlite3` in the Python stdlib; `better-sqlite3` /
`node:sqlite` in Node ≥22). SQL is the single most portable answer to "will this run in Python
and TypeScript".

**Readability.** A non-programmer can read
`SELECT count(*) FROM changes WHERE table='tickets' AND before_status='open' AND after_status='closed'`
more easily than any other candidate here — SQL is the one query language non-engineers are
routinely taught.

**LLM-writability.** The best of the field, by a wide margin, for two reasons: the most training
data of any query language, and the strongest external evidence base (text-to-SQL is the most
benchmarked NL-to-query task there is — e.g.
[Evaluating LLMs for Text-to-SQL Generation With Complex SQL Workload](https://arxiv.org/pdf/2407.19517)).
The caveat is that models write SQL for *relational* schemas fluently and `json_extract('$.a.b')`
plumbing less fluently — which argues for flattening the diff into real columns
(`table, op, key_json, before_json, after_json`) before handing it to a judge author.

**Verdict.** Most expressive, fastest, most LLM-friendly, most readable — and the only one where
you must build the sandbox yourself. If judges are authored by trusted humans/agents and reviewed,
this is the strongest option. If judges are untrusted, it's the most dangerous.

---

## 7. Jinja2 expressions (and sandboxed Jinja)

**What it is.** Using `Environment.compile_expression()` / `{{ … }}` as an expression language,
with `jinja2.sandbox.SandboxedEnvironment` or `ImmutableSandboxedEnvironment` for untrusted
templates.

**Expressiveness.** **[verified]** on Jinja 3.1.6 with `ImmutableSandboxedEnvironment`:
- J1 ✓ — `selectattr` accepts **dotted attribute paths**:
  `state.changes | selectattr('table','equalto','tickets') | selectattr('before.status','equalto','open') | … | list | length` → 2
- J2 ✓, J4 ✓ via `map(attribute='key.id') | reject('in', <other list>) | list | length == 0`
- **J3 fails out of the box**: `TemplateRuntimeError: No test named 'search'.` Core Jinja has no
  regex or substring *test*; `search`/`match` come from Ansible, not Jinja. Substring matching on
  a field inside a filter chain needs a custom test or filter.
- No comprehensions in expressions, no `group_by` in core (`groupby` filter exists and does work),
  no aggregation beyond `sum`/`min`/`max` filters.

**Safety — do not treat the sandbox as a boundary.** Jinja's own documentation says so:

> "The sandbox alone is not a solution for perfect security."
> "It is possible to construct a relatively small template that renders to a very large amount of
> output, which could correspond to a high use of CPU or memory. You should run your application
> with limits on resources such as CPU and memory to mitigate this."
> — [Jinja sandbox docs](https://raw.githubusercontent.com/pallets/jinja/main/docs/sandbox.rst)

And the sandbox has been broken twice recently: **CVE-2024-56201** (store a reference to a
malicious string's `format` method and pass it to a filter that calls it; fixed 3.1.5) and
**CVE-2025-27516** (`|attr` filter retrieves the plain `str.format`, bypassing the sandbox's
`str.format` interception; fixed 3.1.6) —
[GHSA-cpwx-vrp4-4pq7](https://github.com/advisories/GHSA-cpwx-vrp4-4pq7),
[IBM security bulletin on CVE-2024-56201](https://www.ibm.com/support/pages/security-bulletin-jinja-template-sandbox-escape-indirect-strformat-execution-prior-315).
Both are full Python RCE from a template string.

**[verified]** on 3.1.6 the direct attacks are blocked: `state.__class__.__mro__` →
`SecurityError: access to attribute '__class__' of 'dict' object is unsafe`, and
`state | attr('__class__')` → `Undefined`. But the pattern "patched twice in 15 months" is the
fact that should drive the decision.

**Cross-language.** Python-only in practice. `nunjucks` (JS) is a different language with a
different filter set; treating Jinja as your judge language means the TS consumer cannot evaluate
judges.

**Readability.** Filter chains read left-to-right and are approachable
(`| selectattr(…) | list | length`), but `selectattr('x','equalto',y)` is a clunky way to write
`x == y`, and the string-named tests are easy to get wrong (as J3 demonstrates).

**LLM-writability.** Models write Jinja confidently and frequently invent filters/tests that
exist in Ansible or Django but not in core Jinja (`search`, `regex_search`, `json_query`,
`selectattr('x','contains',…)`). That's a loud failure, but a frequent one.

**Verdict.** No. Python-only, weak on the exact operation J3 needs, and a sandbox with a
demonstrated recent escape history. Jinja is a fine *templating* layer for judge **messages**;
it is the wrong choice for judge **predicates**.

---

## 8. Restricted Python evaluators — `simpleeval`, `asteval`

### simpleeval

`simpleeval` 1.0.8 (2026-09-12, [PyPI](https://pypi.org/pypi/simpleeval/json)). Parses with
Python's `ast` and walks the tree with an explicit operator/function allowlist.

**Expressiveness.** With `EvalWithCompoundTypes` you get list comprehensions, which means full
J1–J5. **[verified]**:
- J1 `len([c for c in state['changes'] if c['table']=='tickets' and …])` → 2
- **J4 solved with a nested comprehension** →
  `all([any([e['table']=='ticket_events' and e['after']['ticket_id']==t['key']['id'] for e in state['changes']]) for t in state['changes'] if …])` → `True`
- Dict access via attribute sugar (`foo.bar` for `foo['bar']`) is on by default.

**Safety.** Honest and reasonably strong, with documented limits:
- `MAX_POWER` (default 4,000,000) caps `**`; `MAX_STRING_LENGTH` (100,000) caps string
  construction; `MAX_COMPREHENSION_LENGTH` caps comprehensions *including nested ones*.
- Attributes starting with `_` or `func_` are blocked; `DISALLOW_FUNCTIONS` blocks `type`, `open`,
  etc.; module attribute access is disallowed; `allowed_attrs=BASIC_ALLOWED_ATTRS` turns attribute
  access into an allowlist and *"will be the default for 2.x"*.
- **[verified]** `state.__class__` → `FeatureNotAvailable`; `__import__('os').listdir('.')` →
  `FunctionNotDefined`.
- The README's own caveat: *"A lot of very clever people think the whole idea of trying to sandbox
  CPython is impossible. Read the code yourself, and use it at your own risk."* and *"The only
  issue I know to be aware of is that you can create an expression which takes a long time to
  evaluate, or which evaluating requires an awful lot of memory, which leaves the potential for
  DOS attacks."*
  ([simpleeval README](https://raw.githubusercontent.com/danthedeckie/simpleeval/master/README.rst))

**Performance.** **[verified]** 50 J1 evaluations over 2,000 rows: 1,043 ms (≈21 ms/judge) —
faster than cel-python, slower than jq and SQLite.

**Cross-language.** Python only. There is no TypeScript simpleeval; the TS analogue would be a
different sandbox with different semantics.

**Readability.** A Python comprehension is readable to anyone who has seen Python, and
*unreadable* to anyone who hasn't — more so than SQL, less so than JSONLogic.

**LLM-writability.** The best of the whole field: this is just Python, the language LLMs write
most reliably. The failure mode is models reaching for `collections.Counter`, `re`, `sum(… for …)`
generator expressions, or f-strings that the restricted evaluator doesn't expose — loud
`FunctionNotDefined` errors, easy to validate at authoring time.

### asteval

`asteval` 1.0.10 (2026-08-21, [PyPI](https://pypi.org/pypi/asteval/json)). More capable than
simpleeval (if/while/for, try/except, function definition, f-strings, NumPy) and correspondingly
a bigger attack surface. Its own README calls it *"a **safe(ish)** evaluator"* that
*"can handle user input **more safely than** Python's `eval()`"* — a deliberately weaker claim
than simpleeval's. It blocks class creation, most dunder access, `eval`/`exec`/`yield`/`async`/
decorators/generators, and `getattr`/`setattr`; `open` and `**` are replaced with safer versions;
imports are off by default *"though it can be enabled"*
([asteval README](https://raw.githubusercontent.com/lmfit/asteval/master/README.rst), last
updated 17-Dec-2025).

asteval is a *macro language for scientific apps*, not a judge sandbox: it evaluates statements,
not just expressions, and its own framing is "safer than eval", not "safe". For a judge you want
the smaller surface.

**Verdict on this family.** simpleeval is the pragmatic Python-only answer: maximal
expressiveness, maximal LLM reliability, honest-but-imperfect sandboxing, no TS story. Use it
only if you accept "trusted-ish authors + review" as your threat model, and prefer it over asteval.

---

## 9. Also considered, briefly

- **JSONata** (`jsonata` 2.2.2, 2026-07-16 npm; `jsonata-python` 0.7.0, 2026-07-05 PyPI). Very
  expressive (joins via variable binding `$x`, aggregations, `$sum`, `$count`, `$reduce`), decent
  Python+TS parity, but it has user-defined functions and a `$eval` and is not designed as a
  sandbox. Not researched in depth here; worth a look if CEL's lack of aggregation is the blocker.
- **OPA/Rego.** The other "policy language at scale" answer. Go-first; the Python and TS stories
  are subprocess/WASM. CEL covers the same ground with far less machinery for this use case.

---

## Head-to-head

Legend: ✓ verified working here · ✗ verified not expressible · ~ possible but awkward.

| | filter+count (J1) | negative (J2) | substring/regex (J3) | **cross-list join (J4)** | distinct/set (J5) | untrusted-safe | Python | TypeScript | non-programmer readable | LLM reliability |
|---|---|---|---|---|---|---|---|---|---|---|
| **CEL** | ✓ | ✓ | ✓ | **✓** | ✗ (no distinct) | **Strong** (cost budget, non-Turing-complete) | 3rd-party (slow pure-Py; fast Rust binding) | 3rd-party, semantics diverge | Good | Good |
| **jq** | ✓ | ✓ | ✓ | **✓** (`-` / `INDEX`/`JOIN`) | ✓ (`unique`) | **None** (`$ENV` leak verified, no limits) | libjq binding, fast | emulation only | Poor | Mixed (silent stream bugs) |
| **JSONPath 9535** | ~ (host counts) | ~ | ✓ (`match`) | **✗** | ✗ | Good *if* non-eval impl (two RCEs in `jsonpath-plus`) | good | good | Good for paths | Poor (dialect confusion) |
| **JMESPath (base)** | ✓ | ✓ | ✓ | **✗** | ✗ | Good | 1.1.0 | npm dormant since 2022 | Good | Mixed |
| **JMESPath Community** | ✓ | ✓ | ✓ | **✓** (`let … in`) | ✓ (`group_by`) | Good | 1.1.3 (2023), **module-name collision with boto3's `jmespath`** | 1.3.0 (2025) | Good | Mixed |
| **JSONLogic** | ~ (reduce-to-count) | ✓ | ✓ | **✗ (silently wrong)** | ✗ | **Strongest** (data, not code) | stale (2018) | 2.0.5 / fork | Poor | Mixed |
| **SQLite JSON SQL** | ✓ | ✓ | ✓ | **✓** | ✓ | **None by default** (needs authorizer + SELECT-only) | stdlib | node:sqlite / better-sqlite3 | **Best** | **Best** |
| **DuckDB** | ✓ | ✓ | ✓ | **✓** (`ANTI JOIN`) | ✓ | None by default | ✓ | ✓ | Best | Best |
| **Jinja2 sandbox** | ✓ | ✓ | **✗** (no `search` test) | ✓ (via `reject('in',…)`) | ~ | Weak; 2 sandbox-escape CVEs in 15 months | ✓ | ✗ | Moderate | Mixed (invents Ansible filters) |
| **simpleeval** | ✓ | ✓ | ✓ | **✓** (nested comprehension) | ✓ | Moderate + honest, with DoS caps | ✓ | ✗ | Moderate | **Best** |
| **asteval** | ✓ | ✓ | ✓ | ✓ | ✓ | Weaker ("safe(ish)"); statements, not expressions | ✓ | ✗ | Moderate | Best |

---

## What this implies for a diff-judging language (the short version)

1. **The join requirement eliminates half the field.** JSONPath, base JMESPath and JSONLogic
   cannot express "every closed ticket got an event row" at all. That is not an exotic judge; it
   is the modal judge for a multi-table tool surface.
2. **The safety axis and the expressiveness axis are almost perfectly anti-correlated** — except
   for CEL, which is the only candidate that is both expressive enough for joins and designed for
   untrusted input with a real cost model.
3. **Per-judge full scans are the scaling problem, not the language.** Every candidate is O(rows)
   per judge; the constants differ by 1,000×. If hundreds of judges are the goal, the diff should
   be indexed once (a SQLite table, or a pre-grouped `{table → rows}` map handed to the
   expression) rather than re-scanned per judge.
4. **Nobody can express "which columns changed" generically except SQL** (via `json_tree`), and
   that capability is what makes "ignore `updated_at`" and "changed *only* `status`" judges
   possible without hardcoding column lists.
