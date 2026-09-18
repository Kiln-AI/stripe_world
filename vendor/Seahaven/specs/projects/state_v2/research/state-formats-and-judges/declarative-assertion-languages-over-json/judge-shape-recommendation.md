# Is `expression + expected value + comparator` a good judge shape?

Short answer: **yes, as the 80% path — but only with four amendments, and only if it is layered
on a closed catalogue of named judge types with a code escape hatch underneath.** The naive
triple, used alone, is wrong in three specific ways that show up immediately at a scale of
hundreds of judges.

The evidence and the alternatives are in
[eval-tooling-assertion-shapes.md](./eval-tooling-assertion-shapes.md) and
[language-comparison.md](./language-comparison.md); this file is the argument.

---

## Why the triple is right

**Every serious tool converges on it, independently.**

- OpenAI's current Graders API defines `StringCheckGrader` as exactly
  `input` (expression, templatable) + `reference` (expected, templatable) +
  `operation ∈ {eq, ne, like, ilike}` (comparator).
- promptfoo's assertion record is `transform` (expression) + `value` (expected) + `type`
  (comparator, ~50 named members), with `not-` negation on all of them.
- Great Expectations spells the triple into ~60 class names of the form
  `Expect<Subject><Metric>To<Comparator><Expected>` — and has been doing so since 2018 across
  thousands of user-authored suites.
- Kubernetes evaluates `{expression, message}` records with `self`/`oldSelf` in scope across
  every CRD in the ecosystem.

That is four independent designs, two of them at very large scale, landing on the same shape. It
is not a coincidence: the shape is what makes assertions **reviewable, diffable, machine-
generable and machine-checkable** — the four properties that matter when there are hundreds of
them and they outlive the code that produced the state.

**It also survives the "authored by an LLM" test better than code does.** A judge that is a
record with a closed-vocabulary comparator can be validated structurally before it is ever run:
does this comparator exist, is the expected value the right type for it, does the expression
parse and type-check against the schema. A judge that is a Python function can only be validated
by running it.

---

## Amendment 1: three outcomes, not two

Pass/fail is not enough. Inspect AI routes sample-ending failures to score / `raise` /
`Score.unscored()` explicitly *"so metrics reflect the model, not the run machinery"*, and that
distinction is load-bearing here for a different reason: **many judges are conditional on the
episode having done something.**

"Every closed ticket got an event row" is *vacuously true* if no ticket was closed — and the
languages disagree about what that means. Verified: `json-logic-js` returns **false** for
`{"all":[[], …]}` (its source comments *"All of an empty set is false"*), while CEL returns
**true** for `[].all(x, x > 0)`. Neither is what you want reported. You want
**`not_applicable`**, so that a rollout that never touched tickets neither scores nor penalises
on ticket judges.

Minimum outcome set: `pass` / `fail` / `not_applicable` / `error` (judge itself blew up or
timed out). Collapsing `error` into `fail` silently converts judge bugs into model failures.

## Amendment 2: the comparator vocabulary must include ranges and sets, not just equality

Both promptfoo and Great Expectations arrived at this independently. GX has more `ToBeBetween`
expectations than `ToEqual` ones; promptfoo's `trace-span-count` takes `{min, max}` and the docs
argue explicitly that an exact count *"would reject a correctly-batched run"*.

For a DB diff the same pressure exists everywhere an agent has latitude: it may write one audit
row or three, may or may not touch `updated_at`, may retry an insert. A minimum comparator set:

`eq`, `ne`, `lt`, `lte`, `gt`, `gte`, `between(min,max)`, `in_set`, `contains`, `matches_regex`,
`is_null`/`is_not_null`, `subset_of`, `set_eq`, `approx(value, tol)` — each with a `not_` form.

## Amendment 3: the expected value must be allowed to be a structure, and to come from the task

Two moves from the survey:

- **promptfoo's `is-json`** lets `value` be a *JSON Schema*, not a scalar. One comparator,
  arbitrary structural expectations.
- **OpenAI's graders** say of both `input` and `reference`: *"This may include template strings."*
  The expected value is interpolated from the task row, so one judge definition serves many task
  instances.

For Seahaven both matter: a judge like "the created ticket row matches this shape" wants a schema
or a partial-object match as the expected value, and "the agent closed ticket `{{task.ticket_id}}`"
wants the expected value to come from the task, not be baked into the judge.

## Amendment 4: the judge record must carry its own explanation, and the failure must name rows

Inspect's `Score` carries `value` + `answer` + `explanation`; promptfoo's `GradingResult` is
`{pass, score, reason, componentResults}`; LangSmith's is `{key, score|value, comment}`. At
N=hundreds, a bare `false` is unusable — you cannot triage 200 failing judges without knowing
*which rows* failed.

Great Expectations' escape hatch is the model to copy: `UnexpectedRowsExpectation` is documented
as *"This Expectation will fail validation if the query returns one or more rows. The WHERE clause
defines the fail criteria."* The result set **is** the explanation. A diff judge whose
"expression" evaluates to *the offending changes* rather than to a boolean gets a free, precise
failure message, and the comparator becomes `count == 0` / `count between …`.

This suggests the expression half of the triple should evaluate to **a nodelist/rowset by
default**, with scalar comparators applied to its `count` or to `value()` of a singular result —
which is, not coincidentally, exactly RFC 9535 JSONPath's design (queries return nodelists;
`count()` and `value()` bridge to scalars).

---

## The shape that actually survives contact

```
judge := {
  id, version,                       # versioned, because judges outlive episodes
  description,                       # the human sentence the judge encodes
  applies_when: <predicate>?,        # → not_applicable instead of a wrong pass/fail
  select:     <expression>,          # evaluates to a rowset/nodelist over the diff
  metric:     count | sum(path) | distinct(path) | value(path) | exists,
  comparator: eq|between|in_set|subset_of|matches|…  (+ not_)
  expected:   <scalar | list | schema | template>,
  weight, severity, tags
}
```

with **two escape hatches**, not one:

1. a **`sql` judge** whose query returns offending rows (GX's `UnexpectedRowsExpectation`), and
2. a **code judge** (Python function) for the handful that neither covers.

The 80/15/5 split — named types / expression judges / code judges — is what every surveyed tool
ended up with. Don't design for the 5% and make the 80% pay for it.

### What the tooling should reject before anything runs

Because the shape is data, all of this is checkable at authoring time, which is the main reason
to prefer it over "write a function":

- comparator exists; expected value's type matches the comparator's arity/type
- the expression parses **and type-checks against the diff schema** (does `tickets.status` exist
  in this fixture's schema? is it a string?) — this is where a schema'd state format pays off
- the expression's static cost is under budget (Kubernetes rejects over-budget CEL at write time,
  not evaluation time; see [language-comparison.md](./language-comparison.md))
- the judge names at least one table that the fixture actually has — a judge referencing a table
  that no longer exists should fail loudly at load, not silently pass forever

---

# What the expression language must be able to do

Derived from the five-judge battery (run against all nine candidates in
[hands-on-trials.md](./hands-on-trials.md)). In rough order of how often each capability is
needed and how many languages fail it.

### Tier 1 — table stakes (every candidate has these)

1. **Path access into nested objects**, including through `before`/`after` and a composite `key`.
2. **Filter a list by a conjunction of equality predicates** — `table == 'tickets' && op == 'update'`.
3. **Count the filtered result** and compare the count to a number.
4. **Substring / regex on a field value** (JSONPath: `match`/`search`; CEL: `.contains()`;
   JMESPath: `contains()`; Jinja core: **missing**, needs a custom test — verified).
5. **Null-safety.** `before` is `null` for an insert and `after` is `null` for a delete. The
   language must not throw when a predicate reaches through a null; JMESPath silently yields
   `null` (excluded from results), CEL errors unless guarded with `has()`. Whichever it does, the
   judge author must be able to predict it.

### Tier 2 — the capabilities that eliminate candidates

6. **Cross-list join / outer-scope reference.** "Every closed ticket has a matching event row."
   **Verified impossible in JSONPath (RFC 9535), base JMESPath, and stock JSONLogic** — JSONLogic
   doesn't even fail loudly, it returned `false` for a true proposition. Available in CEL (nested
   comprehension), jq (`-` on arrays, `INDEX`/`JOIN`/`IN`), SQL (`JOIN`/`ANTI JOIN`),
   JMESPath-Community (`let … in`), simpleeval (nested comprehension), Jinja (`reject('in', …)`).

7. **Aggregation beyond count:** `sum`, `min`/`max`, `avg` over a projected field, and
   **`distinct`/set equality**. jq, SQL and JMESPath have these; **base CEL has none of them** —
   no `sum`, no `distinct`, no `group_by`. This is CEL's single biggest gap for diff judging and
   the thing most likely to force custom extension functions.

8. **Group-by.** "How many rows changed per table" and "exactly one row changed in each of these
   three tables" are common judge shapes. jq `group_by`, SQL `GROUP BY`, JMESPath-Community
   `group_by`; CEL and JSONPath: no.

9. **Generic changed-column enumeration** — *"which columns of this row actually differ between
   `before` and `after`"*, computed without hardcoding column names. **Only SQL could do this**
   (SQLite `json_tree` over `$.after` joined against `$.before`, verified returning
   `[(1,'status'), (1,'updated_at'), …]`). Everything else requires the judge to name the columns.

   This is the most consequential finding for a DB-diff judge, because it is what makes these two
   judge families possible:
   - **noise suppression** — "ignore `updated_at`, `modified_by`, audit rows" applied *globally*,
     rather than re-stated in every judge
   - **exactness** — "the agent changed `status` **and nothing else** on this row", which is how
     you catch a model that closed the ticket *and* reassigned it

   Without it, "and nothing else" judges must enumerate every column of every table, which does
   not survive a schema change and is exactly the kind of thing that rots when judges outlive the
   fixture.

### Tier 3 — makes hundreds of judges practical rather than merely possible

10. **Bind a name to a sub-result and reuse it.** `let $closed = … in …` (JMESPath-Community),
    `… as $closed | …` (jq), CTEs (SQL). Without it, every join restates both halves of the
    predicate and judges become unreadable and unmaintainable.
11. **Ordering / positional access** for "the first status change was to `pending`" — only
    meaningful if the state format preserves order (a *net diff* like `inst.changes()` does not
    carry a sequence, which limits what any language can say about ordering; that constraint is
    the diff format's, not the language's).
12. **Evaluate against a pre-indexed structure, not the raw array.** The measured reality (see the
    benchmark table in [hands-on-trials.md](./hands-on-trials.md)) is that every candidate is
    **O(rows) per judge**, so cost is `judges × rows × constant` and the constants span 1,000×.
    A judge language that can be handed `changes_by_table['tickets']` instead of `changes` — or
    that runs against a materialised SQLite table — turns hundreds of judges from a minutes-long
    pass into a milliseconds-long one.
13. **Deterministic, documented empty-set semantics.** Verified divergence: `all([])` is `true` in
    CEL, `false` in json-logic-js. Whatever is chosen must be stated in the judge spec, because
    "no rows matched" is the single most common edge case in diff judging.
14. **Static type-checking against the fixture schema.** CEL and SQL can do this (CEL has a
    checker; SQL has a catalogue). jq, JSONPath, JMESPath and JSONLogic cannot tell you that
    `.after.staus` is a typo until a judge silently passes forever.

### What it explicitly does **not** need

- Turing completeness, loops, recursion, user-defined functions.
- Mutation of the state document.
- I/O of any kind. (jq has `$ENV`, `input`, `import`/`include` — **verified** that a jq expression
  reads the whole process environment, including through the Python binding. That is a
  disqualifier for untrusted judges and an unnecessary hazard even for trusted ones.)
- Arithmetic beyond what comparisons and simple ratios need. JSONPath has none at all
  (**verified**: `+` is a syntax error) and still covers a large share of useful judges.

---

## Where this leaves the language choice

No single candidate wins on every axis; the trade is between the join/aggregation capability that
diff judging actually requires and the safety story that untrusted or LLM-written judges require.

- **If judges are trusted artefacts under human/agent review** (the realistic case for a
  benchmark's own task suite): **SQL over a materialised diff table** is the strongest answer —
  most expressive, only option for generic changed-column detection, ~1,000× faster than the
  pure-Python alternatives, best LLM-writability, most readable to non-programmers, and Python +
  TypeScript both have SQLite in-process. The cost is that you must build the sandbox (single
  `SELECT`, authorizer callback, throwaway in-memory connection seeded only with the diff).
- **If judges must be safe to run untrusted:** **CEL** is the only candidate that is both
  join-capable and designed for hostile input with a real cost model — but you must budget for
  (a) supplying `sum`/`distinct`/`group_by` as extension functions, (b) not using the pure-Python
  implementation for anything large (1.28 s per judge over 2,000 rows, verified), and
  (c) pinning implementation-specific semantics because cel-python and cel-js already diverge.
- **jq** is excellent to *use* and unacceptable to *accept from elsewhere*.
- **JSONPath (RFC 9535)** is the right sublanguage for the "which field / which rows" half of a
  judge record, and should not be asked to be the whole judge.
- **JSONLogic** is safe, GUI-friendly, and cannot express the modal diff judge — with two silent
  wrong-answer traps on top.
