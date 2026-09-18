# Declarative Assertion Languages over JSON

## Bottom Line

I ran the same five DB-diff judges through nine candidate languages against a Seahaven-shaped
`{changes: [{table, op, key, before, after}]}` fixture. The discriminating judge is the
**cross-list join** ("every ticket that went open→closed has a matching `ticket_events` insert"),
and it eliminates three popular candidates outright: **JSONPath (RFC 9535), base JMESPath and
JSONLogic cannot express it** — JSONLogic doesn't even fail loudly, it returns `false` for a true
proposition. Only CEL, jq, SQL-over-JSON, JMESPath-Community (`let … in`), simpleeval and Jinja
can. Of those, **only SQL can enumerate *which columns changed* without hardcoding column names**
(SQLite `json_tree`), which is the capability that makes "ignore `updated_at` churn" and "changed
`status` and nothing else" judges possible at all. On the safety axis the ordering inverts: jq
and SQL have no sandbox (a jq expression read this session's environment variables, verified);
CEL is the only join-capable language with a production cost-budget model (Kubernetes evaluates
untrusted CEL in the API server). Performance is a real constraint nobody advertises: **one CEL
judge over a 2,000-row diff takes 1.28 s in pure-Python cel-python** vs 36 ms in jq's C binding
and 1.2 ms in SQLite against a materialised table.

On judge shape: **`expression + expected value + comparator` is the right 80% path** — OpenAI's
Graders API, promptfoo, Great Expectations and Kubernetes admission policy all converged on it
independently — but it needs four amendments: a **third outcome** (`not_applicable`, because
"every X satisfies Y" over zero X is where CEL and JSONLogic actively disagree), **range and set
comparators** rather than just equality, an **expected value that may be a schema or a task
template**, and a result that **names the offending rows** rather than returning a bare boolean.

## Key Findings

- **The cross-list join is the capability that matters, and half the field lacks it.** Verified:
  JSONPath RFC 9535 has no variables or outer-scope reference in its grammar; base JMESPath
  rejects `let $x = … in …` with `LexerError: Unknown token $`; json-logic-js's `filter`/`all`/
  `some` call `jsonLogic.apply(scopedLogic, datum)`, *replacing* the data object, so there is no
  `../` or root escape. [language-comparison.md](./language-comparison.md),
  [hands-on-trials.md](./hands-on-trials.md)

- **Only SQL can do generic changed-column detection.** `json_each(changes)` joined to
  `json_tree(c.value,'$.after')`, comparing each leaf against the same key under `$.before`,
  returned `[(1,'status'), (1,'updated_at'), (2,'status'), …]` with no column names in the query
  (SQLite 3.45.1, verified). Every other candidate requires the judge to enumerate columns, which
  rots when the fixture schema changes. [hands-on-trials.md](./hands-on-trials.md)

- **Performance spans three orders of magnitude, and the slowest option is the "safe" one.**
  50 evaluations of one count-judge over 2,000 changes: SQLite materialised 61 ms; SQLite
  `json_each` 164 ms; simpleeval 1,043 ms; jq (libjq binding) 1,807 ms; cel-rust binding 2,585 ms;
  **cel-python 64,091 ms (1.28 s per judge)**. Every candidate is O(rows) per judge, so indexing
  the diff once is the only structural lever. [hands-on-trials.md](./hands-on-trials.md)

- **jq is not safe to accept from anywhere.** Verified: `SECRET_TOKEN=hunter2 jq '$ENV'` returns
  the environment, including through the Python `jq` 1.12.0 binding; `[range(1e12)]` had to be
  killed by an external `timeout` (no internal cost budget). The manual also documents `import`/
  `include` module loading, `input`/`inputs`, `input_filename`, `halt_error`.
  [language-comparison.md](./language-comparison.md)

- **CEL is the only join-capable language with a real untrusted-input story**, backed by
  Kubernetes' cost-unit model: *"All CEL expressions evaluated by Kubernetes are constrained by a
  runtime cost budget… execution of the expressions will be halted"*, plus a static estimated-cost
  limit that rejects expressions at write time. Its gaps: **no `sum`, `distinct`, `group_by` in
  base CEL**; the spec warns macros *"can lead to exponential behavior when nested or chained"*;
  and cel-python vs cel-js already diverge on edge cases (verified: `[].all(x, x > 0)` is `true`
  in one, an overload error in the other). [language-comparison.md](./language-comparison.md)

- **Empty-set semantics diverge between languages and will silently corrupt judge results.**
  Verified: `{"all":[[], {"==":[1,1]}]}` → `false` in json-logic-js (its source says *"All of an
  empty set is false"*), while CEL's `[].all(x, x > 0)` → `true`. A judge "every refunded order
  has a credit note" therefore *fails* when nothing was refunded under one and *passes* under the
  other. This is the concrete argument for a `not_applicable` outcome.
  [judge-shape-recommendation.md](./judge-shape-recommendation.md)

- **The triple is the industry's convergent answer.** OpenAI's `StringCheckGrader` is literally
  `input` (templatable expression) + `reference` (templatable expected) +
  `operation ∈ {eq, ne, like, ilike}`; promptfoo's assertion is `transform` + `value` + `type`
  across ~50 named types with universal `not-` negation; Great Expectations spells it into ~60
  class names of the form `Expect<Subject><Metric>To<Comparator><Expected>`; Kubernetes evaluates
  `{expression, message}` with `self`/`oldSelf` (structurally identical to `before`/`after`).
  [eval-tooling-assertion-shapes.md](./eval-tooling-assertion-shapes.md)

- **Ranges beat exact values for anything counted.** Great Expectations has more `ToBeBetween`
  expectations than `ToEqual`; promptfoo's `trace-span-count` takes `{min, max}` and its docs
  argue an exact count *"would reject a correctly-batched run"*. Agents have latitude; count
  judges should too. [eval-tooling-assertion-shapes.md](./eval-tooling-assertion-shapes.md)

- **The best escape hatch returns rows, not booleans.** Great Expectations'
  `UnexpectedRowsExpectation`: *"This Expectation will fail validation if the query returns one or
  more rows. The WHERE clause defines the fail criteria."* The result set is the failure
  explanation — far better than a bare `false` when triaging 200 judges.
  [eval-tooling-assertion-shapes.md](./eval-tooling-assertion-shapes.md)

- **Inspect AI has no declarative assertion surface at all** — scorers are Python functions — but
  it solves the "judge long after the run" problem a different way: `--no-score` at run time and
  `inspect score` to re-score a persisted log later, plus an explicit scoring policy distinguishing
  *scored* / *raise* / *unscored*. [eval-tooling-assertion-shapes.md](./eval-tooling-assertion-shapes.md)

- **Library health is a real selection criterion here.** npm `jmespath` last published 2022-01-19;
  the JMESPath-Community fork that *can* do joins ships a Python release from 2023-12-26 **that
  installs under the module name `jmespath`**, colliding with boto3's dependency. `jsonpath-plus`
  has had two RCEs (CVE-2024-21534, CVE-2025-1302) from evaluating JSONPath through `vm`/`eval`,
  which RFC 9535's own Security Considerations warn against by name. Jinja's sandbox was escaped
  twice in 15 months (CVE-2024-56201 → 3.1.5, CVE-2025-27516 → 3.1.6) and its docs say *"The
  sandbox alone is not a solution for perfect security."* [language-comparison.md](./language-comparison.md)

## Details

- [language-comparison.md](./language-comparison.md) — candidate-by-candidate deep dive on all
  nine languages (CEL, jq, JSONPath RFC 9535, JMESPath ±Community, JSONLogic, SQLite/DuckDB SQL,
  Jinja2 sandbox, simpleeval, asteval) across expressiveness, safety, Python/TS availability,
  readability and LLM-writability, ending in a head-to-head matrix. Read this when choosing.
- [hands-on-trials.md](./hands-on-trials.md) — the executed transcript: the fixture, the exact
  expression for each of the five judges in each language, verbatim failure messages, the safety
  probes (jq `$ENV`, simpleeval/Jinja escape attempts), the DuckDB `->>` operator bug, and the
  performance table. Read this to verify a claim or to lift a working expression.
- [eval-tooling-assertion-shapes.md](./eval-tooling-assertion-shapes.md) — how promptfoo, OpenAI
  Evals (both generations), Inspect AI, Braintrust autoevals, DeepEval, LangSmith, Great
  Expectations and Kubernetes admission policy express assertions, with the cross-cutting patterns
  table. Read this when designing the judge record.
- [judge-shape-recommendation.md](./judge-shape-recommendation.md) — the argument for/against the
  triple, the four amendments, the concrete judge record shape, and the tiered list of what the
  expression language must be able to do for a DB diff to be judgeable at scale. Read this first
  if you only read one.

## Open Questions / Gaps

- **No fetchable benchmark on LLM accuracy at writing jq / JSONPath / JMESPath / CEL.** I searched
  for one; the nearest evidence is text-to-SQL benchmarking (mature) and a text-to-JQL benchmark
  ([Jackal](https://arxiv.org/pdf/2509.23579), execution-accuracy-based). My LLM-writability
  rankings in [language-comparison.md](./language-comparison.md) are reasoned from training-data
  prevalence and from the failure modes I observed while writing the trial expressions — they are
  **judgement, not measurement**, and are labelled as such.
- **Egress policy blocked several canonical doc sites** from this session: `rfc-editor.org`,
  `ietf.org`, `datatracker.ietf.org`, `cel.dev`, `promptfoo.dev`, `sqlite.org`, `duckdb.org`,
  `jmespath.org`, `jsonlogic.com`, `jinja.palletsprojects.com`, `inspect.aisi.org.uk`,
  `docs.langchain.com`, `braintrust.dev`, `deepeval.com`, `platform.openai.com`. I routed around
  this by reading each project's own repository (`raw.githubusercontent.com`) — so RFC 9535 is
  cited from the IETF working-group source it was published from rather than the RFC page, and
  promptfoo/Inspect/LangSmith docs from `site/docs/` in their repos. Content should be identical
  but **section numbering and the final published RFC text were not diffed against the draft**.
- **DuckDB, JSONata and OPA/Rego were not researched in depth.** DuckDB was benchmarked only
  enough to confirm the JSON path works and to find the `->>` overload bug; JSONata and Rego got a
  paragraph each. If CEL's missing aggregation turns out to be the blocker, JSONata deserves a
  proper look (it has variable binding, `$sum`/`$count`/`$reduce`, and live Python + TS
  implementations).
- **I did not measure how many of a realistic Seahaven judge suite actually need the join.** My
  claim that it is "the modal judge" is reasoned from the multi-table structure of a tool surface,
  not from a corpus. The benchmarks subtopic is better placed to answer that from real task suites.
- **CEL extension-function ergonomics untested.** Supplying `sum`/`distinct`/`group_by` as custom
  functions is the obvious fix for CEL's aggregation gap, but I did not verify that cel-python,
  the cel-rust binding and cel-js can be given *the same* extension set with the same semantics —
  and given they already diverge on `[].all()`, that is worth checking before committing.

## Sources

Primary, fetched in this session (2026-09-14):

- [cel-spec `README.md`](https://raw.githubusercontent.com/google/cel-spec/master/README.md) and
  [`doc/langdef.md`](https://raw.githubusercontent.com/google/cel-spec/master/doc/langdef.md) —
  authoritative for CEL's design goals, safety guarantees, macro list, JSON↔CEL type mapping, and
  the nested-macro exponential-behaviour warning.
- [Kubernetes CEL reference](https://raw.githubusercontent.com/kubernetes/website/main/content/en/docs/reference/using-api/cel.md)
  — authoritative for CEL resource constraints (cost units, runtime cost budget, estimated cost
  limits) and the `self`/`oldSelf` transition-rule pattern. `min-kubernetes-server-version: 1.25`.
- [draft-ietf-jsonpath-base.md (the source RFC 9535 was published from)](https://raw.githubusercontent.com/ietf-wg-jsonpath/draft-ietf-jsonpath-base/main/draft-ietf-jsonpath-base.md)
  — authoritative for the filter grammar (comparison operators only, no arithmetic), the
  ValueType/LogicalType/NodesType system, the five registered function extensions, and Security
  Considerations (the `eval()` injection warning and exponential-CPU warning).
- [jq manual (development version)](https://raw.githubusercontent.com/jqlang/jq/master/docs/content/manual/dev/manual.yml)
  — authoritative for jq builtins, SQL-style `INDEX`/`JOIN`/`IN`, and `$ENV`/`env`/`import`/`input`.
- [JMESPath specification](https://raw.githubusercontent.com/jmespath/jmespath.site/master/docs/specification.rst)
  — authoritative for the base function list and the "ordering operators are only valid for
  numbers" rule. [JEP-18 lexical scoping](https://raw.githubusercontent.com/jmespath/jmespath.jep/main/proposals/0018-lexical-scope.md)
  (2023-03-21) for `let … in`.
- [json-logic-js `README.md`](https://raw.githubusercontent.com/jwadhams/json-logic-js/master/README.md)
  and [`logic.js`](https://raw.githubusercontent.com/jwadhams/json-logic-js/master/logic.js) —
  authoritative for the operator table, the scope-replacing iteration semantics, and
  "All of an empty set is false".
- [Jinja sandbox docs](https://raw.githubusercontent.com/pallets/jinja/main/docs/sandbox.rst) —
  authoritative for "The sandbox alone is not a solution for perfect security" and the
  resource-exhaustion caveat. Jinja2 3.1.6 (2025-03-05).
- [simpleeval `README.rst`](https://raw.githubusercontent.com/danthedeckie/simpleeval/master/README.rst)
  (v1.0.8, 2026-09-12) and [asteval `README.rst`](https://raw.githubusercontent.com/lmfit/asteval/master/README.rst)
  (last updated 17-Dec-2025) — authoritative for their safety limits and their own caveats.
- [promptfoo assertion docs](https://raw.githubusercontent.com/promptfoo/promptfoo/main/site/docs/configuration/expected-outputs/index.md)
  (+ `deterministic.md`, `javascript.md`, `python.md`, `guide.md`) — authoritative for the full
  assertion-type catalogue, assertion record fields, `not-` negation, weights/thresholds/
  assert-sets, and the inline JS/Python expression form.
- [openai-python `src/openai/types/graders/*.py`](https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/graders/string_check_grader.py)
  — generated from OpenAI's OpenAPI spec; authoritative for `StringCheckGrader`,
  `TextSimilarityGrader`, `PythonGrader`, `ScoreModelGrader`, `LabelModelGrader`, `MultiGrader`.
- [openai/evals `docs/eval-templates.md`](https://raw.githubusercontent.com/openai/evals/main/docs/eval-templates.md),
  [`docs/build-eval.md`](https://raw.githubusercontent.com/openai/evals/main/docs/build-eval.md),
  [`evals/registry/evals/test-basic.yaml`](https://raw.githubusercontent.com/openai/evals/main/evals/registry/evals/test-basic.yaml)
  — authoritative for `match`/`includes`/`fuzzy_match`/`json_match` semantics, model-graded
  parameters, and the registry YAML shape. README now points to the Dashboard/API as the current path.
- [Inspect AI `docs/scorers.qmd`](https://raw.githubusercontent.com/UKGovernmentBEIS/inspect_ai/main/docs/scorers.qmd),
  [`_builtin-scorers.md`](https://raw.githubusercontent.com/UKGovernmentBEIS/inspect_ai/main/docs/_builtin-scorers.md),
  [`custom-scorers.qmd`](https://raw.githubusercontent.com/UKGovernmentBEIS/inspect_ai/main/docs/custom-scorers.qmd),
  [`scoring.qmd`](https://raw.githubusercontent.com/UKGovernmentBEIS/inspect_ai/main/docs/scoring.qmd)
  — authoritative for the built-in scorer list, the `Score` type, and deferred/offline scoring.
- [LangSmith evaluation concepts](https://raw.githubusercontent.com/langchain-ai/langsmith-docs/main/docs/evaluation/concepts/index.mdx)
  — authoritative for the `{key, score|value, comment}` evaluator result contract.
- [Braintrust autoevals `README.md`](https://raw.githubusercontent.com/braintrustdata/autoevals/main/README.md)
  — authoritative for the scorer catalogue including `JSONDiff`/`ExactMatch`.
- [DeepEval `README.md`](https://raw.githubusercontent.com/confident-ai/deepeval/main/README.md)
  — authoritative for the `threshold` + 0–1 score contract and `GEval`.
- [Great Expectations `expectations/core/__init__.py`](https://raw.githubusercontent.com/great-expectations/great_expectations/develop/great_expectations/expectations/core/__init__.py)
  and [`unexpected_rows_expectation.py`](https://raw.githubusercontent.com/great-expectations/great_expectations/develop/great_expectations/expectations/core/unexpected_rows_expectation.py)
  — authoritative for the ~60-expectation naming taxonomy and the "fail if the query returns rows"
  escape hatch.
- Package registries for version/date evidence: [npm registry](https://registry.npmjs.org/) and
  [PyPI JSON API](https://pypi.org/pypi/cel-python/json) — used for every "last released" claim.

Search-surfaced, **not individually fetched** (egress-blocked); treat as corroborating, not primary:

- [GHSA-cpwx-vrp4-4pq7 / CVE-2025-27516](https://github.com/advisories/GHSA-cpwx-vrp4-4pq7) and
  [IBM bulletin on CVE-2024-56201](https://www.ibm.com/support/pages/security-bulletin-jinja-template-sandbox-escape-indirect-strformat-execution-prior-315) — Jinja sandbox escapes.
- [GitLab advisory, CVE-2025-1302](https://advisories.gitlab.com/npm/jsonpath-plus/CVE-2025-1302/),
  [Snyk SNYK-JS-JSONPATHPLUS-8719585](https://security.snyk.io/vuln/SNYK-JS-JSONPATHPLUS-8719585) — jsonpath-plus RCEs.
- [Google Open Source Blog, 2026-08, formal verification for CEL](https://opensource.googleblog.com/2026/08/securing-the-agentic-era-introducing-formal-verification-for-cel.html).
- [OpenAI Graders guide](https://developers.openai.com/api/docs/guides/graders) — the SDK types
  above are the primary source I actually read; this is the prose version.
- [Jackal: text-to-JQL execution benchmark](https://arxiv.org/pdf/2509.23579),
  [Evaluating LLMs for Text-to-SQL with Complex SQL Workload](https://arxiv.org/pdf/2407.19517).
