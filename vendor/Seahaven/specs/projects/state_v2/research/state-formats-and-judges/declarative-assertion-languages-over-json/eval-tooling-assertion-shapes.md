# How eval tooling actually expresses assertions today

Surveyed 2026-09-14 from primary sources (repo docs and SDK type definitions — several vendor doc
sites were unreachable from this session's egress policy, so I read the same content from the
projects' own repositories).

The question behind the survey: **does the industry converge on a declarative
`expression + expected value + comparator` shape, or on "write a function"?** Answer: it converges
on a *catalogue of named, parameterised assertion types* with an escape hatch to code — and the
single most common named type is exactly `expression + reference + operation`.

---

## 1. promptfoo — the largest declarative assertion catalogue

Source:
[`site/docs/configuration/expected-outputs/index.md`](https://raw.githubusercontent.com/promptfoo/promptfoo/main/site/docs/configuration/expected-outputs/index.md),
[`.../deterministic.md`](https://raw.githubusercontent.com/promptfoo/promptfoo/main/site/docs/configuration/expected-outputs/deterministic.md),
[`.../javascript.md`](https://raw.githubusercontent.com/promptfoo/promptfoo/main/site/docs/configuration/expected-outputs/javascript.md),
[`.../python.md`](https://raw.githubusercontent.com/promptfoo/promptfoo/main/site/docs/configuration/expected-outputs/python.md).

### The assertion record

```yaml
tests:
  - description: 'Test if output is equal to the expected value'
    assert:
      - type: equals          # required — the assertion type
        value: 'Hello, World!'  # the expected value, if applicable
        threshold: 0.8        # for similar/cost/javascript/python/ruby
        weight: 2             # relative importance, default 1.0
        metric: accuracy      # named metric surfaced in the UI
        transform: ...        # JS expression run on the output *before* the assertion
        provider: ...         # for model-graded types
```

The documented properties are exactly: `type` (required), `value`, `threshold`, `weight`,
`provider`, `rubricPrompt`, `config`, `transform`, `metric`, `contextTransform`.

**That is the `expression + expected + comparator` triple with the comparator promoted to the
top-level discriminator**: `transform` is the expression, `value` is the expected, `type` is the
comparator. And `type` is a closed vocabulary with **~50 deterministic members**:

`equals, contains, icontains, regex, starts-with, contains-any, contains-all, icontains-any,
icontains-all, is-json, contains-json, contains-html, is-html, is-sql, contains-sql, is-xml,
contains-xml, is-refusal, javascript, python, ruby, webhook, rouge-n, bleu, gleu, levenshtein,
latency, meteor, perplexity, perplexity-score, cost, word-count, is-valid-function-call,
is-valid-openai-function-call, is-valid-openai-tools-call, tool-call-f1, trace-span-count,
trace-span-duration, trace-error-spans, skill-used, trajectory:tool-used,
trajectory:tool-args-match, trajectory:tool-sequence, trajectory:step-count, guardrails`

plus model-assisted types (`similar, classifier, moderation, llm-rubric, g-eval,
answer-relevance, context-faithfulness, context-recall, context-relevance,
conversation-relevance, trajectory:goal-success, factuality, model-graded-closedqa, pi,
select-best, max-score`).

### Five design moves worth stealing

1. **Universal negation.** *"Every test type can be negated by prepending `not-`. For example,
   `not-equals` or `not-regex`."* One naming rule replaces 50 more types. For a DB-diff judge
   catalogue this is the difference between "agent closed the ticket" and "agent must not have
   touched the audit log" being one type or two.

2. **`is-json` doubles as schema validation.** *"You may optionally set a `value` as a JSON
   schema. If set, the output will be validated against this schema."* The expected value is
   allowed to be a *schema*, not just a scalar — a large expressiveness win for one extra rule.

3. **Count assertions take a range, not a number.** `trace-span-count` and
   `trajectory:step-count` take `value: {type: command, max: 3}` — `min`/`max` rather than an
   exact integer. The docs argue for this explicitly: an exact-count assertion *"would reject a
   correctly-batched run"*, so a tolerance band is the default, not the exception.

4. **Weights, thresholds and assert-sets give partial credit declaratively.** The test's score is
   *"the weighted average of the scores of all assertions"*; a test-level `threshold` decides
   pass/fail from that score; `assert-set` groups assertions with their own threshold
   (*"if one of two assertions need to pass or 50%… `threshold: 0.5`"*); `weight: 0` makes an
   assertion auto-pass. A custom `assertScoringFunction` (JS or Python file) can replace weighted
   averaging entirely, receiving `namedScores: Record<string, number>`.

5. **The escape hatch is an inline expression, not a file.** The `javascript` and `python`
   assertion types take the code *as the `value`*:

   ```yaml
   assert:
     - type: javascript
       value: "output.includes('Hello, World!')"
     - type: javascript
       value: Math.log(output.length) * 10
       threshold: 0.5           # numeric return + threshold = comparator
     - type: python
       value: output[5:10] == 'Hello'
   ```

   Returning a boolean is pass/fail; returning a **number** is a score compared against
   `threshold`; returning a `GradingResult` (`{pass, score, reason, componentResults}`) gives full
   control. *"If the LLM outputs a JSON object (such as in the case of tool/function calls), then
   `output` will already be parsed as an object"* — so the expression operates on parsed JSON,
   exactly the Seahaven situation.

**Safety note:** promptfoo's `javascript`/`python` assertions are plain `eval`/`exec` in the
harness process. promptfoo is a developer tool run on your own configs; it makes no attempt to
sandbox them. That is a legitimate posture *if* judges are trusted artefacts under review.

---

## 2. OpenAI — two generations, and the newer one is the cleanest evidence for the triple

### `openai/evals` (the 2023 OSS repo — effectively legacy)

Source: [`docs/eval-templates.md`](https://raw.githubusercontent.com/openai/evals/main/docs/eval-templates.md),
[`docs/build-eval.md`](https://raw.githubusercontent.com/openai/evals/main/docs/build-eval.md),
[`evals/registry/evals/*.yaml`](https://raw.githubusercontent.com/openai/evals/main/evals/registry/evals/test-basic.yaml).
The README now leads with *"You can now configure and run Evals directly in the OpenAI
Dashboard"* — the repo is the older path.

The YAML is **not** an assertion language. It picks a Python template class and points at data:

```yaml
test-includes:
  id: test-includes.s1.simple-v0
  description: Example eval that uses fuzzy matching to score completions.
  metrics: [accuracy]
test-includes.s1.simple-v0:
  class: evals.elsuite.basic.includes:Includes
  args:
    samples_jsonl: test_fuzzy_match/samples.jsonl
    ignore_case: true
```

The comparators are three one-liners, documented verbatim, over a completion `a` and a reference
list `B`:

- `basic/match.py:Match` — `any([a.startswith(b) for b in B])`
- `basic/includes.py:Includes` — `any([(b in a) for b in B])`
- `basic/fuzzy_match.py:FuzzyMatch` — `any([(a in b or b in a) for b in B])`
- `basic/json_match.py:JsonMatch` — *"yields a match if `a` is identical to at least one answer
  from `B`. Two JSON objects are identical if they have the same set of keys and the values for
  each key are identical. Key order is not significant… Invalid JSON never matches."*

The expected value lives **per-sample in the JSONL**, not in the YAML. The model-graded template
(`ModelBasedClassify`) is parameterised by `prompt`, `input_outputs`, `choice_strings`,
`choice_scores`, `eval_type` (`cot_classify` / `classify_cot` / `classify`) — i.e. a
*constrained-label* judge whose output is mapped to a numeric score by `choice_scores`, with
anything unrecognised parsed into `"__invalid__"`.

Two transferable ideas: **versioned eval ids** (`<eval_name>.<split>.<version>`, and *"when you
change your eval, you should bump the version"*), and **constrained grader output** (a fixed
label set with an explicit invalid bucket) rather than free-text judging.

### OpenAI Graders API (current)

Read verbatim from the generated SDK types
([openai-python `src/openai/types/graders/`](https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/graders/string_check_grader.py)).
This is the clearest industry statement of the triple:

```python
class StringCheckGrader(BaseModel):
    """A StringCheckGrader object that performs a string comparison between input
       and reference using a specified operation."""
    input: str        # "The input text. This may include template strings."
    name: str
    operation: Literal["eq", "ne", "like", "ilike"]
    reference: str    # "The reference text. This may include template strings."
    type: Literal["string_check"]
```

- `input` = **the expression** (a template over the sample/output, e.g. `{{item.x}}`)
- `reference` = **the expected value** (also templatable, so it can come from the dataset row)
- `operation` = **the comparator**, a closed four-member enum

The sibling graders fill out the taxonomy:

| Grader | Shape |
|---|---|
| `text_similarity` | `input`, `reference`, `evaluation_metric ∈ {cosine, fuzzy_match, bleu, gleu, meteor, rouge_1..5, rouge_l}` — same triple, fuzzy comparator |
| `python` | `source: str` (script), `image_tag` — the escape hatch, run in a container |
| `score_model` | LLM judge: `input` messages (templatable), `model`, `range` (default `[0,1]`), `sampling_params` (incl. `seed`) |
| `label_model` | LLM judge constrained to `labels: List[str]` with `passing_labels: List[str]` ("must be a subset of labels") |
| `multi` | `graders: …` + **`calculate_output: str` — "A formula to calculate the output based on grader results"** |

`multi.calculate_output` is the aggregation answer: individual graders produce named scores, and a
*formula string* combines them. That is the same move as promptfoo's `assertScoringFunction`, but
declarative.

---

## 3. Inspect AI — no declarative assertions at all

Sources:
[`docs/scorers.qmd`](https://raw.githubusercontent.com/UKGovernmentBEIS/inspect_ai/main/docs/scorers.qmd),
[`docs/_builtin-scorers.md`](https://raw.githubusercontent.com/UKGovernmentBEIS/inspect_ai/main/docs/_builtin-scorers.md),
[`docs/custom-scorers.qmd`](https://raw.githubusercontent.com/UKGovernmentBEIS/inspect_ai/main/docs/custom-scorers.qmd).

Inspect's scorers are **Python functions**, full stop:

```python
@scorer(metrics=[accuracy(), stderr()])
def close_enough(rel_tol: float = 0.01):
    async def score(state: TaskState, target: Target) -> Score:
        ...
        return Score(value=CORRECT if correct else INCORRECT,
                     answer=answer, explanation=state.output.completion)
    return score
```

The built-in scorers are the usual text comparators — `includes()`, `match()` (with
`location ∈ {begin, end, any, exact}` and `numeric=True`), `pattern()` (regex with capture
groups and `match_all`), `answer()`, `exact()`, `f1()`, `choice()`, `math()`,
`model_graded_qa()`, `model_graded_fact()`, `perplexity()` — but they are **parameterised Python
callables**, not config records, and there is no YAML/JSON assertion surface.

Three details worth copying regardless:

1. **A `Score` carries `value` + `answer` + `explanation`** — every judge result explains itself.
   `pattern()` returns `INCORRECT` with `reason="invalid_response_format"` when the pattern
   doesn't match at all, distinguishing *wrong answer* from *unparseable*.
2. **Scoring is deferrable.** *"Defer scoring with `--no-score`, re-score logs with
   `inspect score`"* — the log holds enough state to re-run every scorer later. This is exactly
   the Seahaven requirement ("possibly long after the episode ran"), and Inspect solves it by
   persisting the transcript and making scoring a separate pass, not by making judges declarative.
3. **Scoring policy is explicit** — a sample-ending failure must be routed to "score it", `raise`,
   or `Score.unscored()`, *"so metrics reflect the model, not the run machinery"*. A judge system
   needs a third outcome beyond pass/fail: **inapplicable / errored**.

`math()` also documents an interesting safety stance for judge-side parsing: *"Mathematical
answers are treated as data: parsing and comparison run in a time-bounded worker thread and never
evaluate answer text as Python."*

---

## 4. Braintrust `autoevals` — a library of named scorers

Source: [`README.md`](https://raw.githubusercontent.com/braintrustdata/autoevals/main/README.md).

Scorers are functions in Python **and** TypeScript (`pip install autoevals` /
`npm install autoevals`), returning a score in `[0,1]` plus the grader's raw output. The catalogue:

- LLM-as-judge: Battle, ClosedQA, Humor, Factuality, Moderation, Security, Summarization, SQL,
  Translation, fine-tuned binary classifiers
- RAG: context precision/relevancy/recall/entity-recall, faithfulness, answer
  relevancy/similarity/correctness
- **Composite: "Semantic list contains", "JSON validity"**
- Embedding: embedding similarity
- **Heuristic: Levenshtein distance, Exact match, Numeric difference, JSON diff**

`JSONDiff` and `ExactMatch` are the relevant ones: the industry's off-the-shelf answer to
"compare two JSON structures" is *a similarity score over a structural diff*, not a query
language. Custom evaluators are built by supplying a prompt and a `choice_scores` mapping
(the OpenAI `ModelBasedClassify` pattern, reused).

---

## 5. DeepEval — threshold-per-metric, plus deterministic decision trees

Source: [`README.md`](https://raw.githubusercontent.com/confident-ai/deepeval/main/README.md).

```python
correctness_metric = GEval(
    name="Correctness",
    criteria="...",
    evaluation_params=[...],
    threshold=0.5,
)
metric.measure(test_case); metric.score
```

*"All metric scores range from 0 - 1, which the `threshold=0.5` ultimately determines if your
test has passed or not."* So the shape is **continuous score + threshold comparator**, uniformly,
with metrics as pytest-integrated objects. The roadmap lists **"DAG custom metrics"** — a
deterministic decision-tree metric that chains judgements; that is the "make LLM judging
reproducible by decomposing it into a fixed graph of small decisions" idea, which is the same
instinct as "hundreds of small judges".

---

## 6. LangSmith — evaluators are functions returning `{key, score|value, comment}`

Source:
[`docs/evaluation/concepts/index.mdx`](https://raw.githubusercontent.com/langchain-ai/langsmith-docs/main/docs/evaluation/concepts/index.mdx).

> "An evaluator returns one or more metrics. These should be returned as a dictionary or list of
> dictionaries of the form: `key`: The name of the metric. `score` | `value`: The value of the
> metric. Use `score` if it's a numerical metric and `value` if it's categorical.
> `comment` (optional): The reasoning or additional string information justifying the score."

Definition is by *"**Custom code**: Define custom evaluators as Python or TypeScript functions"*
or *"**Built-in evaluators**… configure and run via the UI"*. Techniques are classified as
Human / **Heuristic** (*"deterministic, rule-based functions"*) / LLM-as-judge / Pairwise.

The transferable bit is the **result record**: `key` + (`score` xor `value`) + `comment`. Note
`score`-vs-`value` is a deliberate split between numeric and categorical outcomes — worth copying
rather than forcing everything into a float.

---

## 7. The strongest prior art for "hundreds of small judges": data-quality tools

### Great Expectations

The naming convention *is* the triple, applied ~60 times. Class names read from
[`great_expectations/expectations/core/__init__.py`](https://raw.githubusercontent.com/great-expectations/great_expectations/develop/great_expectations/expectations/core/__init__.py):

```
Expect<Subject><Metric>To<Comparator><Expected>
```

`ExpectTableRowCountToEqual`, `ExpectTableRowCountToBeBetween`,
`ExpectTableRowCountToEqualOtherTable`, `ExpectColumnValuesToBeInSet`,
`ExpectColumnValuesToNotBeNull`, `ExpectColumnValuesToMatchRegex`,
`ExpectColumnDistinctValuesToEqualSet`, `ExpectColumnSumToBeBetween`,
`ExpectColumnPairValuesAToBeGreaterThanB`, `ExpectMulticolumnSumToEqual`,
`ExpectColumnValuesToMatchJsonSchema`, `ExpectCompoundColumnsToBeUnique`,
`ExpectQueryResultsToMatchComparison`, …

Three things GX proves at scale:

1. **A closed catalogue of `metric × comparator` covers the overwhelming majority of assertions.**
   The comparators that recur: `ToEqual`, `ToBeBetween`, `ToBeInSet`, `ToContainSet`,
   `ToEqualSet`, `ToMatchRegex`, `ToBeGreaterThan`, `ToBeLessThan`, `ToNotBe…`. Roughly nine
   comparators generate sixty expectations.
2. **`ToBeBetween` outnumbers `ToEqual`.** Ranges are the default for anything counted or
   aggregated — same conclusion promptfoo reached independently for span counts.
3. **The escape hatch returns offending rows, not a boolean.** `UnexpectedRowsExpectation`:
   *"This Expectation will fail validation if the query returns one or more rows. The WHERE clause
   defines the fail criteria."* — a SQL query whose *result set is the explanation*. For a DB
   diff, this is a much better escape hatch than "run arbitrary code and return a bool", because
   the failure message writes itself.

GX also has a `mostly` parameter (fraction of rows that must satisfy the expectation) — declarative
partial credit at the assertion level rather than the suite level.

### Kubernetes admission policy — the same shape, with CEL as the expression

`ValidatingAdmissionPolicy` and CRD `x-kubernetes-validations` are literally
`{expression, message}` records evaluated per object, with `self` and `oldSelf` in scope:

> "in the `x-kubernetes-validations[i].rules` field of CustomResourceDefinitions, the `self` and
> `oldSelf` variables are available and refer to the previous and current state of the custom
> resource data to be validated"
> — [k8s CEL reference](https://raw.githubusercontent.com/kubernetes/website/main/content/en/docs/reference/using-api/cel.md)

`self` / `oldSelf` is **structurally the same as `after` / `before` in a row diff**, and
Kubernetes calls the rules that use both "transition rules". That's the closest production
analogue to a Seahaven judge: thousands of small declarative expressions over a before/after pair,
authored by many people, evaluated in a hot path, with a documented cost budget and a per-rule
`message`/`messageExpression`.

---

## Cross-cutting patterns worth adopting

| Pattern | Who does it | Why it matters for a diff judge |
|---|---|---|
| Closed vocabulary of `type` + free-form `value` | promptfoo, GX, OpenAI graders | Most judges never need an expression at all; a named type is reviewable and LLM-writable |
| `not-` prefix negation | promptfoo | Halves the catalogue; "must not have touched X" is a first-class judge |
| Ranges (`min`/`max`, `ToBeBetween`) as the default for counts | promptfoo, GX | Exact counts over-constrain and produce false failures |
| Templatable expected value (`reference` may include template strings) | OpenAI graders | Expected values come from the task row, not the judge |
| Numeric return + `threshold` as a generic comparator | promptfoo, DeepEval | One mechanism for partial credit across all judge types |
| `weight` + suite `threshold` + optional scoring function | promptfoo | Declarative partial credit *and* an escape hatch when the weighting is non-linear |
| Result = `{pass, score, reason}` (+ named key) | promptfoo `GradingResult`, Inspect `Score`, LangSmith | A judge that can't explain itself is unusable at N=hundreds |
| Distinguish *failed* from *unscorable* | Inspect `Score.unscored()` / scoring policy | Judges that don't apply to an episode must not count as failures |
| Escape hatch returns offending rows | GX `UnexpectedRowsExpectation` | Failure message writes itself from the data |
| Judge on before/after pair as first-class variables | k8s `self`/`oldSelf` | Exactly the `before`/`after` of a row change |
| Versioned judge/eval ids, bump on change | OpenAI Evals (`<name>.<split>.<version>`) | Required if judges outlive the episodes they grade |
| Scoring deferred and re-runnable from a persisted log | Inspect `--no-score` / `inspect score` | Required if judging happens long after the run |
