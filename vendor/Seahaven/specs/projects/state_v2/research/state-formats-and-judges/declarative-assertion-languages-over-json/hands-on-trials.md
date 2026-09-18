# Hands-on trials: the same five judges in nine languages

Everything here was **executed in this session** (2026-09-14) against real library versions, not
recalled. Where a language failed, the failure text is verbatim.

## Environment

| Component | Version | Source of version |
|---|---|---|
| jq (CLI) | 1.7 | `jq --version` |
| jq (Python binding) | 1.12.0 (2026-07-10) | [PyPI](https://pypi.org/pypi/jq/json) |
| SQLite | 3.45.1 | `sqlite3.sqlite_version` |
| DuckDB | 1.5.5 (2026-07-22) | [PyPI](https://pypi.org/pypi/duckdb/json) |
| `jmespath` (Python, original) | 1.1.0 (2026-01-22) | [PyPI](https://pypi.org/pypi/jmespath/json) |
| `jmespath-community` (Python) | 1.1.3 (2023-12-26) | [PyPI](https://pypi.org/pypi/jmespath-community/json) |
| `@jmespath-community/jmespath` (TS) | 1.3.0 (2025-07-16) | [npm](https://registry.npmjs.org/@jmespath-community/jmespath) |
| `jsonpath-rfc9535` (Python) | 1.0.0 (2025-11-30) | [PyPI](https://pypi.org/pypi/jsonpath-rfc9535/json) |
| `json-p3` (TS) | 2.3.0 (2026-09-01) | [npm](https://registry.npmjs.org/json-p3) |
| `cel-python` | 0.5.0 (2026-01-31) | [PyPI](https://pypi.org/pypi/cel-python/json) |
| `common-expression-language` (cel-rust binding) | 0.9.0 (2026-09-08) | [PyPI](https://pypi.org/pypi/common-expression-language/json) |
| `@marcbachmann/cel-js` (TS) | 8.0.0 (2026-07-07) | [npm](https://registry.npmjs.org/@marcbachmann/cel-js) |
| `json-logic-js` | 2.0.5 (2024-07-09) | [npm](https://registry.npmjs.org/json-logic-js) |
| `simpleeval` | 1.0.8 (2026-09-12) | [PyPI](https://pypi.org/pypi/simpleeval/json) |
| Jinja2 | 3.1.6 (2025-03-05) | [PyPI](https://pypi.org/pypi/Jinja2/json) |
| Node | v22.22.2 | `node --version` |

## The fixture

A Seahaven-shaped state document — `changes` is the net row-level diff, in the shape
`inst.changes()` already produces.

```json
{
  "schema_version": "1",
  "episode_id": "ep_001",
  "changes": [
    {"table":"tickets","op":"update","key":{"id":1},
     "before":{"id":1,"status":"open","assignee":"alice","updated_at":"2026-01-01T00:00:00Z"},
     "after":{"id":1,"status":"closed","assignee":"alice","updated_at":"2026-01-02T00:00:00Z"}},
    {"table":"tickets","op":"update","key":{"id":2}, "before":{...,"status":"open","assignee":"bob"}, "after":{...,"status":"closed"}},
    {"table":"tickets","op":"update","key":{"id":3}, "before":{...,"status":"open","assignee":"carol"}, "after":{...,"status":"pending"}},
    {"table":"ticket_events","op":"insert","key":{"id":90},"before":null,"after":{"id":90,"ticket_id":1,"kind":"closed"}},
    {"table":"ticket_events","op":"insert","key":{"id":91},"before":null,"after":{"id":91,"ticket_id":2,"kind":"closed"}},
    {"table":"emails","op":"insert","key":{"id":7},"before":null,"after":{"id":7,"to":"bob@example.com","subject":"Your ticket was closed"}}
  ]
}
```

Ground truth: J1 = 2, J2 = true, J3 = true, J4 = true (both closed tickets have an event),
J5 = `{emails, ticket_events, tickets}`.

---

## jq 1.7 — 5/5

```jq
# J1
[.changes[] | select(.table=="tickets" and .op=="update"
                     and .before.status=="open" and .after.status=="closed")] | length
# → 2

# J2
[.changes[] | select(.table=="audit_log" and .op=="delete")] | length == 0
# → true

# J3
any(.changes[]; .table=="emails" and .op=="insert" and (.after.to | test("bob@")))
# → true

# J4 — cross-list join by set difference
  ([.changes[] | select(.table=="tickets" and .before.status=="open" and .after.status=="closed") | .key.id]) as $closed
| ([.changes[] | select(.table=="ticket_events" and .op=="insert") | .after.ticket_id]) as $evts
| ($closed - $evts) | length == 0
# → true

# J5
[.changes[].table] | unique == ["emails","ticket_events","tickets"]
# → true

# J4 alternative using the documented SQL-style operators
INDEX(.changes[] | select(.table=="ticket_events"); .after.ticket_id) | keys
# → ["1","2"]
```

### jq safety probes (all verified)

```console
$ echo '{}' | SECRET_TOKEN=hunter2 jq -c '$ENV | {SECRET_TOKEN, HOME}'
{"SECRET_TOKEN":"hunter2","HOME":"/root"}

$ SECRET_TOKEN=hunter2 python -c "import jq; print(jq.compile('\$ENV.SECRET_TOKEN').input_value({}).first())"
hunter2
# and jq.compile('$ENV|keys|length') → 146

$ timeout 3 bash -c 'echo null | jq "[range(1e12)] | length"'; echo "exit=$?"
exit=124        # no internal limit; killed externally
```

The jq manual documents `$ENV`/`env`, `input`/`inputs`, `input_filename`, `import`/`include`
(module loading), `debug`, `stderr`, `halt`, `halt_error(exit_code)` — a jq "expression" is a
program with I/O, not a pure query.

---

## CEL — 5/5 in Python, join verified in TS

```python
# cel-python 0.5.0, variable `state` bound to the document
size(state.changes.filter(c, c.table=='tickets' && c.op=='update'
                             && c.before.status=='open' && c.after.status=='closed'))
# → IntType(2)

state.changes.all(c, !(c.table=='audit_log' && c.op=='delete'))            # → true
state.changes.exists(c, c.table=='emails' && c.op=='insert' && c.after.to.contains('bob@'))  # → true

# J4 — nested comprehension closes over the outer binding `t`
state.changes.filter(t, t.table=='tickets' && t.before.status=='open' && t.after.status=='closed')
  .all(t, state.changes.exists(e, e.table=='ticket_events' && e.op=='insert'
                                  && e.after.ticket_id == t.key.id))
# → true
```

J5 (exact set of changed tables) has **no base-CEL formulation** — no `distinct`, no `sort` on
lists. You'd need a custom extension function or three `exists` + one `all` spelled by hand.

### Cross-implementation divergence (verified)

| Expression | cel-python 0.5.0 | @marcbachmann/cel-js 8.0.0 |
|---|---|---|
| J4 nested join | `true` | `true` |
| `[].all(x, x > 0)` | `true` | **error**: `Operator overload 'int > int: bool' overlaps with 'double > int: bool'` |
| `size(...)` | `IntType(2)` | returns a JS `BigInt` (not JSON-serialisable without a replacer) |

---

## JSONPath (RFC 9535) — 2/5, and the count lives in the host

```python
# jsonpath_rfc9535 1.0.0
len(jp.find("$.changes[?@.table=='tickets' && @.op=='update' "
            "&& @.before.status=='open' && @.after.status=='closed']", doc))
# → 2      (the "== 2" is Python's, not JSONPath's)

len(jp.find("$.changes[?match(@.after.to, '.*bob@.*')]", doc))   # J3 → 1
```

Failures, verbatim:

```
$.changes[?@.key.id + 1 == 2]
  jsonpath_rfc9535  → JSONPathSyntaxError: unexpected filter selector token '+', line 1, column 20
  json-p3 (TS)      → JSONPathSyntaxError: unexpected filter selector token '+' ('.id + 1 =':20)
```

Semantic surprise (correct per spec, unintuitive in practice):

```python
jp.find("$[?count($.changes[?@.table=='tickets']) == 3]", doc)
# → ['1', 'ep_001', [ ...all six change records... ]]
```
The filter is applied to **each child of the root**, and since the condition is constant-true,
every root member is selected. JSONPath filters select nodes; they don't evaluate to a boolean
about the document.

J4: not expressible — no variables, no outer-scope reference in the grammar.

---

## JMESPath — 3/5 base, 5/5 community fork

```python
# jmespath 1.1.0 (original)
length(changes[?table=='tickets' && op=='update'
               && before.status=='open' && after.status=='closed'])          # → 2
length(changes[?table=='audit_log' && op=='delete']) == `0`                  # → True
length(changes[?table=='emails' && contains(after.to, 'bob@')])              # → 1
sort(changes[].table)
# → ['emails','ticket_events','ticket_events','tickets','tickets','tickets']  ← no `distinct`
```

J4 attempt against the original:

```
let $evts = changes[?table=='ticket_events'].after.ticket_id in length(changes[?...])
→ LexerError: Bad jmespath expression: Unknown token $:
```

Against the community forks — **both TS and Python accept it**:

```
# @jmespath-community/jmespath 1.3.0 (TS)  and  jmespath-community 1.1.3 (Python)
let $evts = changes[?table=='ticket_events'].after.ticket_id
in length(changes[?table=='tickets' && before.status=='open'
                  && after.status=='closed' && !contains($evts, key.id)])
# → 0   (zero unmatched closed tickets ⇒ J4 holds)

group_by(changes, &table) | keys(@)   # → ['tickets','ticket_events','emails']
length($.changes)                     # → 6   (root reference)
```

**Operational trap (verified):** the PyPI package `jmespath-community` installs its code under
the module name `jmespath`. In an environment that also has boto3's `jmespath` dependency, which
one wins depends on install order and `sys.path`.

---

## JSONLogic — 3/5, with two silent-wrong-answer traps

```js
// json-logic-js 2.0.5. There is no length/count operator; counting is a reduce.
{"reduce":[
   {"filter":[{"var":"changes"}, {"and":[
       {"==":[{"var":"table"},"tickets"]},
       {"==":[{"var":"op"},"update"]},
       {"==":[{"var":"before.status"},"open"]},
       {"==":[{"var":"after.status"},"closed"]}]}]},
   {"+":[{"var":"accumulator"},1]}, 0]}
// → 2

{"none":[{"var":"changes"}, {"and":[{"==":[{"var":"table"},"audit_log"]},
                                    {"==":[{"var":"op"},"delete"]}]}]}   // → true
{"some":[{"var":"changes"}, {"and":[{"==":[{"var":"table"},"emails"]},
                                    {"in":["bob@", {"var":"after.to"}]}]}]}  // → true
```

**J4 returns the wrong answer, silently:**

```js
{"all":[ {"filter":[{"var":"changes"}, {"and":[{"==":[{"var":"table"},"tickets"]},
                                               {"==":[{"var":"after.status"},"closed"]}]}]},
         {"some":[{"var":"../changes"}, {"==":[{"var":"after.ticket_id"},{"var":"../key.id"}]}]}]}
// → false        (ground truth: true)
```

Root cause read from
[logic.js](https://raw.githubusercontent.com/jwadhams/json-logic-js/master/logic.js): every
iteration operator calls `jsonLogic.apply(scopedLogic, datum)` — the inner data object *replaces*
the outer one. There is no `../`, `$`, or root escape in the operator table.

**Empty-set trap:**

```js
{"all":[[], {"==":[1,1]}]}   // → false     ← json-logic-js
{"none":[[], {"==":[1,1]}]}  // → true
```
CEL disagrees: `[].all(x, x > 0)` → `true` (verified, cel-python). A judge phrased "every
refunded order has a credit note" therefore **fails when nothing was refunded** under JSONLogic
and **passes** under CEL/SQL. That divergence has to be pinned down explicitly in any judge spec.

---

## SQLite `json_each` / `json_tree` — 5/5, plus one thing nobody else can do

```sql
-- J1
SELECT count(*) FROM json_each(:doc, '$.changes') c
WHERE json_extract(c.value,'$.table')='tickets'
  AND json_extract(c.value,'$.op')='update'
  AND json_extract(c.value,'$.before.status')='open'
  AND json_extract(c.value,'$.after.status')='closed';
-- → 2

-- J4 as an anti-join
WITH ch AS (SELECT value AS v FROM json_each(:doc, '$.changes')),
 closed AS (SELECT json_extract(v,'$.key.id') id FROM ch
   WHERE json_extract(v,'$.table')='tickets'
     AND json_extract(v,'$.before.status')='open'
     AND json_extract(v,'$.after.status')='closed'),
 evt AS (SELECT json_extract(v,'$.after.ticket_id') tid FROM ch
   WHERE json_extract(v,'$.table')='ticket_events' AND json_extract(v,'$.op')='insert')
SELECT count(*) FROM closed LEFT JOIN evt ON evt.tid=closed.id WHERE evt.tid IS NULL;
-- → 0   (no unmatched closed tickets)

-- Grouped aggregate in one statement
SELECT json_extract(value,'$.table'), json_extract(value,'$.op'), count(*)
FROM json_each(:doc, '$.changes') GROUP BY 1,2 ORDER BY 1,2;
-- → [('emails','insert',1), ('ticket_events','insert',2), ('tickets','update',3)]
```

### Generic changed-column detection with `json_tree` (unique to SQL)

```sql
SELECT json_extract(c.value,'$.key.id') AS id, t.key AS changed_column
FROM json_each(:doc, '$.changes') c, json_tree(c.value,'$.after') t
WHERE t.type NOT IN ('object','array')
  AND json_extract(c.value,'$.table')='tickets'
  AND json_quote(t.value) <> coalesce(json_quote(json_extract(c.value,'$.before.'||t.key)),'null');
-- → [(1,'status'), (1,'updated_at'), (2,'status'), (2,'updated_at'), (3,'status'), (3,'updated_at')]
```

No column names were hardcoded. That single capability makes two whole judge families possible:
"the agent changed only `status`" and "ignore `updated_at`/audit churn when comparing rows".

---

## DuckDB 1.5.5 — 5/5, with an operator gotcha

Working form:

```sql
WITH ch AS (SELECT unnest(json_extract(?::JSON,'$.changes[*]')) AS c)
SELECT count(*) FROM ch
WHERE json_extract_string(c,'$.table')='tickets'
  AND json_extract_string(c,'$.op')='update'
  AND json_extract_string(c,'$.before.status')='open'
  AND json_extract_string(c,'$.after.status')='closed';
-- → 2

-- J4
... SELECT count(*) FROM closed ANTI JOIN evt ON evt.tid=closed.id;   -- → 0
```

Two failures found along the way, both verbatim:

```
unnest(json_extract(?::JSON,'$.changes'))          -- without [*]
→ Binder Error: UNNEST() can only be applied to lists, structs and NULL, not JSON

WHERE c->>'$.table'='tickets' AND c->>'$.op'='update'
→ Conversion Error: Failed to cast value to numerical: {"table":"emails","op":"insert",...}
```

The second one is the sharp edge: a **single** `->>` in the same position works, and
`GROUP BY c->>'$.table', c->>'$.op'` works, but two `->>` ANDed in a `WHERE` triggers a bad
overload resolution in 1.5.5. `json_extract_string()` is the form to standardise on.

---

## Jinja2 3.1.6 (`ImmutableSandboxedEnvironment`) — 4/5

```jinja
{# J1 — selectattr accepts dotted paths #}
state.changes | selectattr('table','equalto','tickets')
              | selectattr('op','equalto','update')
              | selectattr('before.status','equalto','open')
              | selectattr('after.status','equalto','closed') | list | length
{# → 2 #}

{# J4 #}
(state.changes | selectattr('table','equalto','tickets')
               | selectattr('before.status','equalto','open')
               | selectattr('after.status','equalto','closed')
               | map(attribute='key.id') | list
   | reject('in', state.changes | selectattr('table','equalto','ticket_events')
                                | map(attribute='after.ticket_id') | list)
   | list | length) == 0
{# → True #}
```

J3 fails:

```
state.changes | selectattr('table','equalto','emails') | map(attribute='after.to')
              | select('search','bob@') | list | length > 0
→ TemplateRuntimeError: No test named 'search'.
```

`search`/`match`/`regex_search` are Ansible's, not core Jinja's. Core Jinja has no
regex/substring *test*, so this needs a custom test registered on the environment.

Sandbox probes on 3.1.6 (both blocked, i.e. the CVE-2025-27516 patch is in):

```
state.__class__.__mro__   → SecurityError: access to attribute '__class__' of 'dict' object is unsafe.
state | attr('__class__')  → Undefined
```

---

## simpleeval 1.0.8 — 5/5

```python
# J1
len([c for c in state['changes']
     if c['table']=='tickets' and c['op']=='update'
     and c['before']['status']=='open' and c['after']['status']=='closed'])
# → 2

# J4 — nested comprehension, outer binding visible
all([any([e['table']=='ticket_events' and e['after']['ticket_id']==t['key']['id']
          for e in state['changes']])
     for t in state['changes']
     if t['table']=='tickets' and t['after']['status']=='closed'])
# → True
```

Escape probes:

```
state.__class__                  → FeatureNotAvailable: Sorry, access to __attributes or func_ attributes is not available. (__class__)
__import__('os').listdir('.')    → FunctionNotDefined: Function '__import__' not defined
```

(`EvalWithCompoundTypes` was used, with `len/sum/any/all/sorted/set` explicitly injected; nothing
is available that you don't hand it.)

---

## Performance: one J1-shaped judge over a 2,000-row diff

50 evaluations each, same machine, same document. The document was compiled/parsed once per
engine where the API allows it.

| Engine | 50 evals | per judge | ×  vs SQLite |
|---|---|---|---|
| **SQLite, `changes` materialised once into a table** | **61 ms** | **1.2 ms** | 1× |
| SQLite `json_each`, re-parsing the JSON each time | 164 ms | 3.3 ms | 2.7× |
| jq 1.12 Python binding (libjq), pre-compiled program | 1,807 ms | 36 ms | 30× |
| simpleeval 1.0.8 | 1,043 ms | 21 ms | 17× |
| cel-rust binding (`common-expression-language` 0.9.0), compiled + reused `Context` | 2,585 ms | 52 ms | 43× |
| cel-rust binding, `cel.evaluate(expr, {...})` per call | 4,907 ms | 98 ms | 82× |
| **cel-python 0.5.0, pre-compiled program, pre-converted activation** | **64,091 ms** | **1,282 ms** | **1,070×** |

Read the last row carefully: **a single CEL judge over a 2,000-row diff takes 1.3 seconds in pure
Python.** Two hundred such judges is ~4 minutes of CPU per episode. cel-python is a pure-Python
tree interpreter; this is not a tuning problem.

The structural point behind the table: every one of these is **O(rows) per judge**, so the total
cost is `judges × rows × constant`. The only lever that changes the shape is indexing the diff
once — which is what the SQLite "materialised" row measures, and why it is 1,000× the pure-Python
CEL number.

---

## Reproduction

Scripts used (kept in the session scratchpad, not committed):
`state.json`, `big.json` (2,000 synthetic changes, `random.seed(0)`), `run_py.py`
(JMESPath/JSONPath/CEL/simpleeval), `run_sql.py` + `run_duck*.py` (SQLite/DuckDB),
`run_jinja.py`, `node/run.mjs` + `node/run2.mjs` (JSONLogic/json-p3/cel-js), `bench.py`.
