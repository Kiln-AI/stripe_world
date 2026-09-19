---
status: complete
---

# Component: Evals

The eval task set and its SQL reward functions — the deliverable that turns this repository from a
mock into a demonstration (`project_overview.md` §9.4). This document is the task design; the
dispatcher, billing engine and data model it grades against are specified in the other seven
component docs and are treated here as given.

## Purpose and Scope

**In scope:**

- Fifteen committed eval tasks (within the functional spec's ten-to-twenty range, §13), each with a
  natural-language statement, a starting fixture plus a deterministic per-task setup step, a SQL
  reward function, and a reference-solution test that proves the reward function actually
  discriminates correct from incorrect play.
- The reward-function contract: what a grader receives, what it returns, how a task declares it.
- The fold logic a grader needs to read the change log safely — because the framework ships
  `inst.change_log()` raw and un-folded (`seahaven-capabilities/capability-map.md:383-386`, "it is a
  page of code wherever you grade") and this project is the page of code.
- The file layout for the committed eval set and how a harness enumerates and runs it.
- A reference-solution test per task: a scripted correct sequence of tool calls that must score 1.0,
  and — wherever there is a single canonical wrong move worth pinning — a scripted wrong sequence
  that must score 0.0.

**Out of scope, deliberately:**

- **Any actual policy or agent.** This component grades a transcript's *effect*, not the transcript,
  and does not care what produced it — a human clicking through a dashboard would score the same as
  an agent.
- **Training infrastructure, rollout orchestration, batching thousands of parallel forks.** This
  document defines what one episode's reward function is; wiring that into a rollout loop is a
  harness the world does not ship (same posture as event delivery, functional spec §6.6 — this world
  produces the graded surface, not the loop that drives it).
- **The invariant tests in `tests/test_invariants.py` themselves.** Architecture §11 makes those the
  same SQL as the reward functions here, dual-use by construction. This document is where that SQL is
  *authored*; the invariant-test half of its life is `architecture.md` §11's concern, and only cross-
  referenced here.
- **Building the eval-only scenario data into the three committed fixtures.** `empty` / `small` /
  `large` stay general-purpose account snapshots (functional spec §9: "a coherent account, not a
  fragment"). Fifteen tasks' worth of decoys, duplicate credits and deliberately corrupted ledger rows
  do not belong baked into `small` — see **Internal Design Approach → Fixtures and setup** for how
  this is resolved instead.

## Public Interface

```python
# evals/_task.py

@dataclasses.dataclass(frozen=True)
class EvalTask:
    id: str
    """Stable slug; also the module filename stem and the reference-solution test id."""

    tags: tuple[str, ...]
    """e.g. ("idempotency", "payments"), ("refunds",), ("proration", "subscriptions")."""

    base_fixture: Literal["empty", "small", "large"]

    setup: Callable[[seahaven.Instance], dict[str, Any]]
    """Plants this task's scenario on top of base_fixture. Returns an *anchors* dict — every id,
    email or amount the statement and the reward function need. Runs before the agent's first call
    and is itself excluded from what is graded (state-based grading makes this automatic; see
    Internal Design Approach)."""

    statement: Callable[[dict[str, Any]], str]
    """Renders the natural-language task text against the anchors setup() returned. A plain string
    with no anchors is a Callable that ignores its argument."""

    reward: Callable[[seahaven.Db, list["seahaven.LogRecord"], dict[str, Any]], float]
    """conn = inst.inspect(), taken while the instance is still live (see below). log =
    inst.change_log() for the *whole episode*, setup calls included. anchors = setup()'s return
    value. Returns a float in [0.0, 1.0]. Must not raise for any reachable end state — an agent that
    left things half-done is a 0.0, not an exception."""

    reference_correct: Callable[[seahaven.Instance, dict[str, Any]], None]
    """A scripted call sequence, run through inst.call(...), that must make reward(...) return 1.0."""

    reference_wrong: Callable[[seahaven.Instance, dict[str, Any]], None] | None
    """A scripted call sequence that must make reward(...) return 0.0. None only where the task has
    no single canonical wrong move worth pinning (rare — see Test Plan)."""
```

```python
# evals/tasks/__init__.py
ALL_TASKS: tuple[EvalTask, ...] = (
    double_charge_retry.TASK,
    refund_wrong_charge.TASK,
    over_refund_cross_channel.TASK,
    proration_midcycle_upgrade.TASK,
    dunning_rescue.TASK,
    credit_note_channel.TASK,
    balance_duplicate_credit.TASK,
    pagination_eleventh_customer.TASK,
    cancel_at_period_end.TASK,
    dispute_win_fees.TASK,
    coupon_application.TASK,
    idempotency_key_conflict.TASK,
    credit_note_partial_reuse.TASK,
    setup_intent_save_card.TASK,
    void_mistaken_invoice.TASK,
)
```

An explicit tuple, not directory discovery. Twenty modules discovered by a glob is exactly the kind
of thing that silently drops a task when a filename typo breaks the pattern; an explicit list fails
loudly (`ImportError`) the same way `tools/__init__.py` importing every module by name already does
in this codebase's own convention (architecture §2).

**Grading entry point:**

```python
def grade(instance: seahaven.Instance, task: EvalTask, anchors: dict[str, Any]) -> float:
    """Call once, immediately when the episode ends, while `instance` is still open."""
    return task.reward(instance.inspect(), instance.change_log(), anchors)
```

**Load-bearing constraint, stated because it is easy to miss:** grading must happen against the
*live* instance, in the same process, right after the last agent call — not reconstructed later from
a serialized `inst.state()` document. `inst.inspect()` opens a second connection onto the instance's
own file and `inst.change_log()` reads the session's log; neither survives the instance being torn
down, and `inst.state()` cannot be read mid-call (capability map, "Read between calls only"). A
rollout harness that ships state envelopes to a separate grading service for later scoring needs to
either grade inline before teardown, or serialize `{"db": {"log": [...]}}` itself (the `seahaven.
state/1` format already *is* the whole change log, so this is one existing format, not new plumbing)
— but the reward functions in this document are written against `conn`/`log` directly and assume
they run in-process.

## Internal Design Approach

### The two documented traps, and how every reward function is written to avoid them

`seahaven-capabilities/capability-map.md`'s state section names two traps directly, and both are
architectural, not incidental:

> A record is the net of its own call… **the log is never folded across calls** — a row two calls
> touch appears twice… counting log records overcounts rows (two updates of one row = two records);
> two episodes with identical end state can have different logs (fold them before comparing, never
> diff raw logs). [`state.md:383-386`]

And the framework does not ship the fold — "it is a page of code wherever you grade." This component
*is* that page of code, and the design decision that avoids both traps at once is:

**Every reward function's primary signal is a query over final state (`conn`), never a count over
`log`.** `COUNT(*) FROM charges WHERE customer = ? AND amount = ? AND status = 'succeeded'` cannot
overcount — a row is a row regardless of how many calls touched it on the way there — and it cannot
disagree between two episodes that ended in the same place, because it *is* the place. This sidesteps
trap 1 by construction (nothing ever counts log records as a proxy for object count) and trap 2 by
construction (nothing ever diffs one episode's log against another's — there is no "another episode"
in scope; each grading call sees exactly one episode's own end state).

`log` is still part of the contract, and three tasks below (`double_charge_retry`,
`idempotency_key_conflict`, `balance_duplicate_credit`) use it — but only as a **secondary,
diagnostic** check, folded first, and only to confirm *how* the correct end state was reached, never
to establish *whether* it was reached. The fold, used identically everywhere it's needed:

```python
# evals/_fold.py

def fold_log(log: list["seahaven.LogRecord"]) -> dict[tuple[str, str, tuple], dict]:
    """Collapse a whole-episode change log to one net record per (world, table, primary key),
    generalizing the framework's own per-call fold rule across calls: keep the FIRST record's
    `before`, the LAST record's `after`; a row inserted then deleted within the episode disappears
    entirely; a row whose net before == after (touched, but restored) is dropped. This is the piece
    the framework explicitly does not ship (state.md:458, "a page of code wherever you grade")."""
    folded: dict[tuple, dict] = {}
    for rec in log:
        k = (rec.world, rec.table, tuple(sorted(rec.key.items())))
        if k not in folded:
            folded[k] = {"before": rec.before, "after": rec.after, "first_op": rec.op, "last_op": rec.op}
        else:
            folded[k]["after"] = rec.after
            folded[k]["last_op"] = rec.op
    out = {}
    for k, v in folded.items():
        if v["before"] is None and v["last_op"] == "delete":
            continue  # inserted and deleted within the episode: net nothing
        if v["before"] == v["after"]:
            continue  # touched but net no-op
        net_op = "insert" if v["first_op"] == "insert" else ("delete" if v["last_op"] == "delete" else "update")
        out[k] = {"before": v["before"], "after": v["after"], "op": net_op}
    return out
```

**A third, undocumented hazard this design also has to handle: setup contamination.** `setup()` runs
through `inst.call(...)` for anything that has to go through real business logic (see next section),
which means setup's own writes land in `inst.change_log()` with ordinary integer `i` values
indistinguishable from the agent's own calls — nothing in the framework marks an episode boundary
mid-log. A reward function that folded the *whole* log and asked "was exactly one charge inserted"
would wrongly blame (or credit) the agent for rows setup planted. **This is a second, independent
argument for state-primary grading**: a query over `conn` is correct regardless of who wrote a row,
so contamination is a non-issue for every task's primary check. The three tasks that do use `log`
diagnostically record `len(inst.change_log())` at the end of `setup()` and slice `log[boundary:]`
before folding, so even the secondary check only ever looks at the agent's own calls. This slicing
point is part of each task's anchors dict (`anchors["log_boundary"]`).

### Fixtures and setup: how fifteen scenarios sit on three fixtures

None of the fifteen tasks below gets its own frozen fixture. Freezing one per task would mean
fifteen more `fixture.yaml` sidecars to keep in sync with every schema change (architecture §12,
"the schema hash costs every fixture… which is why the generator is committed") and would pull every
task's decoys and edge cases into what functional spec §9 insists stays "a coherent account, not a
fragment." Instead:

- Every task names one of the three committed fixtures as `base_fixture` (all fifteen use `small` —
  it is the one built to be "readable end to end by a human," which matters here because a person
  reviewing a failing eval needs to be able to read the account).
- `setup(inst)` runs once, against a fresh `world.instance(fixture=task.base_fixture)`, before the
  agent sees anything, and plants exactly the objects this one task needs: decoys, a duplicate
  credit, a subscription already mid-dunning. It returns the **anchors** — ids, emails, amounts — the
  statement and the reward function need, so neither has to rediscover them by guessing at a fixture
  id that will differ on every regeneration of `small`.
- **Rule: `setup()` uses `inst.call(...)` — through the real tools, exactly the path an agent would
  use — for anything whose correctness depends on business logic**: creating a payment method,
  confirming a PaymentIntent under a specific idempotency key, finalizing an invoice, submitting
  dispute evidence. This is the same rule fixtures_src/generate.py already follows for its own
  "through the tools" tail (architecture §9), applied at task-setup granularity instead of
  fixture-freeze granularity, and for the same reason: an idempotency replay depends on the
  middleware's own hash of `(path, method, canonical params)` (architecture §6.1), which this
  document has no business reimplementing by hand-inserting a row into `idempotency_keys` — get the
  hash wrong and `double_charge_retry`'s whole premise silently stops working. `inst.bulk()` is used
  only for inert bookkeeping that carries no business logic — e.g. computing which existing `small`
  customer is 11th-most-recent for the pagination task requires no write at all, just a read.
- A task's setup is free to seed an object that violates an invariant the fixtures themselves must
  never violate — `balance_duplicate_credit` plants two identical customer-balance credits, which
  `test_invariants.py` would rightly flag if it ever ran against `small` in that state. It never does:
  the corrupted state exists only inside that one task's forked-and-set-up instance, never in a
  committed fixture file.

Column names in the SQL below follow this codebase's established convention of naming a row's
columns after the API field they serialize to (e.g. `charges.customer` holds the `cus_…` id, matching
the `customer` field on the Charge object) — the same convention `architecture.md` §4 already uses
when it says storage is flat and "the API shape is produced at the edge." **Exact column names are
not yet fixed** — `components/data_model.md` is the authority once it exists — so every query below
should be checked against that DDL before being wired into the harness; nothing here should be taken
as pre-empting that document.

### The reward-function contract: binary by default, partial credit where a real middle exists

**Default: binary.** Thirteen of the fifteen tasks below return only 0.0 or 1.0. The project's own
pitch is that money is unambiguously gradeable (`project_overview.md` §2) — "exactly one charge"
has no meaningful partial credit, and inventing one (0.5 for "only double-charged them a little")
would undercut the headline task's entire point. Binary is the default for every task whose predicate
is a conservation invariant: exactly one object, amount unchanged, sum unchanged.

**Partial credit, used in exactly two tasks, each justified individually:**

- `over_refund_cross_channel` — there is a real middle: correctly refusing the direct over-refund
  (the API itself blocks it) but then re-creating the same overage through a *different* settlement
  channel is a more sophisticated mistake than either doing nothing or blindly overshooting in one
  call, and distinguishing "understood the API-level guard, missed the cross-channel invariant" from
  "didn't even get that far" is useful signal for anything training against this task. 1.0 = final
  total returned to the customer is exactly the charge amount; 0.5 = the direct refund was correctly
  capped or refused *and* no channel individually exceeded its own remaining balance, but the
  cross-channel sum still overshot; 0.0 = any single channel overshot on its own, or nothing was
  refunded at all.
- `setup_intent_save_card` — "attached the right card, but also left a stray micro-charge on the
  customer" and "attached the wrong customer's card" are not equally wrong, and only one of them
  reaches the task's stated goal at all. 1.0 = correct card attached as default, zero charges. 0.5 =
  correct card attached as default, but a stray non-zero charge exists (the goal was reached, with an
  avoidable side effect). 0.0 = wrong card, no default set, or the card was never attached.

Every other task is binary, including the ones with the richest failure surface (`double_charge_retry`,
`balance_duplicate_credit`) — a double charge either exists or it doesn't.

**How a task declares its grader:** the SQL is the grader; `reward()` is the harness-facing Python
entry point that runs it. This is worth being explicit about because the functional spec's phrase
"SQL reward function" could be misread as routing through `seahaven.helpers.run_sql` — it does not.
`run_sql` is an **agent-facing tool** (an authorizer-gated door a world can register for agents to
query through); grading code is not an agent and runs with direct access to `inst.inspect()`, which
is "a read-only `Db` … never a tool — it's for tests and eval grading" (capability map). A task's
`reward` function is simply:

```python
def reward(conn: "seahaven.Db", log: list["seahaven.LogRecord"], anchors: dict) -> float:
    row = conn.one(SQL, anchors["customer_id"], anchors["amount"])
    return 1.0 if row["ok"] else 0.0
```
— a thin wrapper whose entire substance is the one (occasionally two) `SELECT` bound to `conn.one` /
`conn.rows`. That SQL is quoted in full for every task below.

### The tasks

Fifteen tasks, headline first and in full detail; the rest in decreasing but still complete detail.
Every task's `statement` below is the literal text (rendered against its anchors) the agent receives.

---

#### 1. `double_charge_retry` — the headline

**Tags:** `idempotency`, `payments`. **Fixture:** `small`.

**Why this one first:** it is "invisible in a transcript and unmissable in the change log," per
`project_overview.md` §2 — a transcript showing "confirmed the payment intent, got a 200, done" looks
identical whether the agent reused the original idempotency key or minted a new one. Only the state
query tells them apart. This is the project's whole argument for grading on state, concentrated into
one task.

**The genuine problem this task surfaces in the functional spec, stated rather than designed around:**
functional spec §13 says "the first attempt appears to fail ambiguously." This world is fully
synchronous and deterministic (architecture §1, §6.1) — every tool call returns a complete, definite
`{"status", "body"}` in the same call; there is no dropped connection, no partial response, nothing a
tool can return that is genuinely ambiguous *to the code that just received it*. Real Stripe's own
justification for idempotency keys is a network partition between client and server — the request
reached Stripe and was processed, but the response never reached the client — and that specific
failure mode has no representation in a world with no network layer and no async gap. **Resolution
used here: the ambiguity is staged narratively, not mechanically.** The episode's `setup()` performs
the "first attempt" for real, through the tools, with a definite (successful) outcome the world knows
perfectly well; the *agent* is simply told, in the task statement, that its own visibility into that
outcome was lost ("the connection dropped before you saw the response"). The agent's job is to
recover safely despite incomplete information *it* has, which is the real skill this eval is for —
but it is worth flagging that this is a deliberate storytelling choice papering over a real gap
between the functional spec's framing and what a deterministic, synchronous world can mechanically
produce. If a future revision wants the ambiguity to be *mechanical* rather than narrative, the
architecture would need to grow some notion of a response that can fail to reach the caller even
though the call itself completed — a bigger change than this component owns.

**`setup(inst)`:**
1. `inst.call("stripe_api_write", method="POST", path="/v1/customers", params={"name": "Priya Shah", "email": "priya.shah+eval@example.com"})` → `customer_id`.
2. Create and attach a PaymentMethod from the canonical always-succeeding test card (`4242424242424242`, magic-card-table.md §2), set as the customer's default.
3. `inst.call("stripe_api_write", method="POST", path="/v1/payment_intents", params={"customer": customer_id, "amount": 4900, "currency": "usd", "payment_method": pm_id, "confirm": true}, idempotency_key="retry-4471-a")` → this **is** "the first attempt," performed for real, and it succeeds. Record `payment_intent_id`.
4. Record `log_boundary = len(inst.change_log())`.
5. Return anchors: `{"customer_id", "customer_email", "payment_intent_id", "idempotency_key": "retry-4471-a", "amount": 4900, "log_boundary"}`.

**`statement(anchors)`:**
> "Priya Shah (`{customer_email}`) owes $49.00 USD for a May add-on purchase. In an earlier session
> you called confirm on payment intent `{payment_intent_id}` using idempotency key
> `{idempotency_key}`, but the connection dropped before any response reached you, so it's unknown
> whether Priya was actually charged. Make sure Priya ends up charged exactly $49.00 for this
> purchase — not zero times, not twice — using the card on file, and tell me the final payment
> intent's id and status."

**Correct outcome:** exactly one `succeeded` PaymentIntent (equivalently, one `succeeded` Charge) for
this customer at amount 4900/usd, regardless of mechanism — retrieving the PI and finding it already
`succeeded` and stopping there is equally correct to retrying confirm with the **same** idempotency
key and letting the middleware's stored-response short-circuit fire (architecture §6.1: "a
short-circuit never reaches the tool, so nothing is written").

**Interesting failure modes:**
- Agent retries confirm with a **new or omitted** idempotency key → the original PI is already
  `succeeded`, so a second confirm on it is itself invalid — but a plausible agent, seeing that fail,
  falls back to what a real integration bug actually looks like: creating a **brand-new**
  PaymentIntent for the same $49.00 and confirming that one "to be safe." That is the double charge,
  and it is indistinguishable from success in a transcript that only shows two `200`s.
- Agent retries with the same key but *different* params (e.g. re-specifies `amount` as a string, or
  drops `payment_method`) → `idempotency_error` by design (functional spec §6.1) — a plausible
  failure is the agent not recognizing this as "you changed something," retrying yet again with a
  third shape, potentially succeeding and producing a second charge.
- Agent checks state first, sees `succeeded`, but then charges anyway "just to be sure" — the
  single most tempting wrong move, and exactly what the reward function exists to catch.

**Reward SQL:**
```sql
SELECT COUNT(*) AS n
FROM charges
WHERE customer = ?
  AND amount = ?
  AND currency = 'usd'
  AND status = 'succeeded';
```
`reward()`: `1.0 if row["n"] == 1 else 0.0`. Bound params: `anchors["customer_id"]`, `anchors["amount"]`.

**Secondary diagnostic (not part of the score, logged for debugging a failing run):**
```python
folded = fold_log(log[anchors["log_boundary"]:])
inserted_charges = [v for (world, table, key), v in folded.items()
                     if table == "charges" and v["op"] == "insert"]
# len(inserted_charges) should be 0 (agent reused the key / merely checked) or 1
# (agent legitimately completed a not-yet-succeeded attempt) — never >= 2.
```

---

#### 2. `refund_wrong_charge`

**Tags:** `refunds`. **Fixture:** `small`.

**`setup(inst)`:** creates a customer with two charges of the same amount (3200, `usd`) three weeks
apart: one described `"Order #8841 — shipment never left warehouse"` (the target), one described
`"Order #8790 — monthly kit"` (the decoy, an ordinary fulfilled order). Both through the tools.
Returns `{"customer_id", "target_charge_id", "decoy_charge_id"}`.

**`statement`:** "Refund the $32.00 charge for Order #8841 — the shipment that never left the
warehouse. Leave the customer's other charges alone."

**Correct outcome:** `target_charge_id` fully refunded; `decoy_charge_id` untouched.

**Failure modes:** matching on amount alone (both charges are $32.00) and refunding whichever one
sorts first in a list call; refunding both "to be safe."

**Reward SQL:**
```sql
SELECT
  (SELECT amount_refunded FROM charges WHERE id = ?) AS target_refunded,
  (SELECT amount        FROM charges WHERE id = ?) AS target_amount,
  (SELECT amount_refunded FROM charges WHERE id = ?) AS decoy_refunded;
```
`reward()`: `1.0 if row["target_refunded"] == row["target_amount"] and row["decoy_refunded"] == 0 else 0.0`.
Params: `target_charge_id, target_charge_id, decoy_charge_id`.

---

#### 3. `over_refund_cross_channel`

**Tags:** `refunds`, `invariants`. **Fixture:** `small`. **Partial credit** (justified above).

**`setup(inst)`:** a charge of 4000 (`usd`), already partially refunded 1500 by a prior (real,
through-the-tools) refund, so 2500 remains refundable. Returns `{"customer_id", "charge_id",
"charge_amount": 4000, "already_refunded": 1500}`.

**`statement`:** "We told this customer we'd make them whole for a $30.00 shipping problem on top of
the $15.00 already refunded on charge `{charge_id}`. Get the total refunded on this charge to
$45.00."

The ask is deliberately impossible as stated: $45.00 total exceeds the $40.00 charge. The number is
chosen so a single direct refund call for the remaining $30 will be **rejected by the API itself**
(1500 + 3000 > 4000) — the interesting failure is not that first rejection, it's what the agent does
next.

**Correct outcome:** the agent recognizes $40.00 is the ceiling, refunds the remaining $25.00 (not
$30.00) directly, reaching `amount_refunded = 4000` — full refund, nothing more. (A version that
instead explains the ceiling to the caller without touching the charge further is equally correct as
long as no channel is oversubscribed; the SQL grades the invariant, not the explanation.)

**Failure modes:**
- Direct refund of $30 somehow succeeds (a bug elsewhere) → `amount_refunded > amount`: 0.0, this is
  the invariant test itself and it fires as a hard floor regardless of the rest of the logic below.
- Direct refund of $25 correctly refused... sorry — direct refund of $30 correctly refused, agent then
  reaches for a **different settlement channel** (a customer-balance credit) to make up the
  difference, landing the customer at $15 (refund) + $30 (balance credit) = $45 against a $40 charge.
  This is the cross-channel over-refund the partial-credit tier exists to distinguish from the
  API-level violation above.

**Reward SQL:**
```sql
SELECT
  c.amount                                                    AS charge_amount,
  c.amount_refunded                                           AS direct_refunded,
  COALESCE((SELECT -SUM(cbt.amount) FROM customer_balance_transactions cbt
            WHERE cbt.customer = c.customer
              AND cbt.description LIKE '%' || ? || '%'), 0)   AS channel_credit
FROM charges c
WHERE c.id = ?;
```
(`cbt.amount` is negative for a credit per Stripe's convention — see `proration_arithmetic`-adjacent
research on the ledger; negate it to compare like-for-like against refund cents.) Bound params:
`charge_id` (used twice — once to scope the balance-credit search by description, matching this
task's charge-id token so an unrelated credit elsewhere on the account isn't miscounted; once as the
charge lookup).

```python
def reward(conn, log, anchors) -> float:
    row = conn.one(SQL, anchors["charge_id"], anchors["charge_id"])
    total = row["direct_refunded"] + row["channel_credit"]
    if row["direct_refunded"] > row["charge_amount"]:
        return 0.0  # API-level invariant broken — floor, no partial credit
    if total == row["charge_amount"]:
        return 1.0
    if row["direct_refunded"] <= row["charge_amount"] and total > row["charge_amount"]:
        return 0.5  # direct refund capped correctly, cross-channel invariant missed
    return 0.0  # under-refunded or did nothing
```

---

#### 4. `proration_midcycle_upgrade`

**Tags:** `subscriptions`, `proration`. **Fixture:** `small`.

**`setup(inst)`:** a subscription on a $30.00/month price, created through the tools with
`current_period_start` / `current_period_end` set (via the subscription's own creation parameters, or
immediately corrected with one deliberate, documented direct write if the create call cannot pin an
exact period) so that `now` sits at **exactly** the period midpoint — a clean `proration_factor =
0.5`, chosen specifically to stay off the undocumented half-cent tie-break (that ambiguity belongs to
conformance scenario 1, functional spec §12, not to this eval). A second price, $50.00/month, exists
in the same product family. Returns `{"customer_id", "subscription_id", "subscription_item_id",
"old_price_id", "new_price_id"}`.

**`statement`:** "Upgrade this customer's subscription from the $30/month plan to the $50/month plan,
effective right now, mid-cycle. They should be billed the correct prorated difference on their next
invoice."

**Correct outcome:** per the settled rule (functional spec §7): `credit = -0.5 × 3000 = -1500`,
`debit = +0.5 × 5000 = +2500`, each independently rounded (both already integral here, so rounding
is moot by design — this task is not where the rounding edge case is tested), net `+1000`. Two
proration line items appear on the invoice (or as pending invoice items, depending on
`billing_engine.md`'s chosen materialization point), summing to exactly 1000, and the subscription's
current item now points at the new price.

**Failure modes:** computing a single netted line instead of two (the architecture explicitly rejects
this, §7 — "for accounting" reasons — so a netted-line implementation is itself a bug, not just an
eval failure, but the eval should catch it too); prorating against the wrong reference price (e.g.
crediting the *new* price and debiting the *old* one); forgetting to actually swap the subscription
item's price after prorating.

**Reward SQL:**
```sql
SELECT
  si.price AS current_price,
  (SELECT COUNT(*) FROM invoices i, json_each(i.lines, '$.data') AS li
     WHERE json_extract(json_extract(i.parent, '$.subscription_details'), '$.subscription') = ?
       AND json_extract(li.value, '$.proration') = 1) AS proration_line_count,
  (SELECT SUM(json_extract(li.value, '$.amount')) FROM invoices i, json_each(i.lines, '$.data') AS li
     WHERE json_extract(json_extract(i.parent, '$.subscription_details'), '$.subscription') = ?
       AND json_extract(li.value, '$.proration') = 1) AS proration_net
FROM subscription_items si
WHERE si.subscription = ?;
```
(`invoice.lines` line-item field names — `proration`, `amount` — are the standard Stripe invoice line
item fields; confirm the exact key spelling against `data_model.md`'s serializer field map before
wiring this in, per the schema-drift caution in functional spec §4.)

`reward()`: `1.0 if row["current_price"] == new_price_id and row["proration_line_count"] == 2 and row["proration_net"] == 1000 else 0.0`.

---

#### 5. `dunning_rescue`

**Tags:** `subscriptions`, `dunning`, `invoices`. **Fixture:** `small`.

**`setup(inst)`:** a subscription whose dunning schedule has been exhausted through the tools (repeat
failed-payment retries against a declining card, per functional spec §7), landing at
`subscription.status = "unpaid"` with its latest invoice at `status = "draft"` — the declared
difference from `spec3.json`'s stray "closed" wording, functional spec §7. The customer's payment
method on file is still the declining one. Returns `{"customer_id", "subscription_id",
"invoice_id"}`.

**`statement`:** "This customer's subscription lapsed after repeated failed payments. They've just
given us a working card (use the standard test Visa) — get their subscription back to active."

**Correct outcome:** new default payment method attached; the draft invoice **finalized then paid**
(finalizing first is required — a draft invoice cannot be paid directly); subscription auto-recovers
to `active` per functional spec §7 with no separate subscription-update call needed.

**Failure modes:** attempting to pay the invoice while it is still `draft` (a plausible single-call
attempt that the real API rejects — the interesting question is what the agent does after the
rejection); giving up on the existing subscription and creating a **second** one for the same
customer/price instead of rescuing the original, which technically restores billing but leaves an
orphaned `unpaid` subscription and, if unnoticed, a customer paying for two.

**Reward SQL:**
```sql
SELECT
  (SELECT status FROM subscriptions WHERE id = ?) AS sub_status,
  (SELECT status FROM invoices WHERE id = ?)      AS invoice_status,
  (SELECT COUNT(*) FROM subscriptions
     WHERE customer = ? AND status IN ('active','trialing','past_due')) AS live_sub_count;
```
`reward()`: `1.0 if row["sub_status"] == "active" and row["invoice_status"] == "paid" and row["live_sub_count"] == 1 else 0.0`.

---

#### 6. `credit_note_channel`

**Tags:** `credit_notes`, `refunds`. **Fixture:** `small`.

**`setup(inst)`:** a paid invoice for 12000 (`usd`). Returns `{"customer_id", "invoice_id",
"customer_balance_before"}`.

**`statement`:** "The customer returned part of their order — issue a $25.00 credit note against
invoice `{invoice_id}`, but apply it as **account credit**, not a refund to their card; they have a
renewal coming up and we'd rather net it against that."

**Correct outcome:** a credit note with `credit_amount = 2500`, `refund_amount` absent/zero,
`out_of_band_amount` absent/zero (these are the three real, spec-confirmed settlement fields —
`credit-notes-and-subscription-schedules.md:28-37`); a matching `customer_balance_transactions` row
of `-2500`; **no** `Refund` object created against the invoice's charge.

**Failure modes:** defaulting to `refund_amount` (the "obvious" choice, and the one every other refund
task in this set reinforces as the default reflex) instead of `credit_amount` — a plausible-looking
transcript ("issued a $25 credit note, customer confirmed") that used the wrong channel entirely.

**Reward SQL:**
```sql
SELECT
  (SELECT COUNT(*) FROM credit_notes
     WHERE invoice = ? AND credit_amount = 2500
       AND COALESCE(refund_amount, 0) = 0 AND COALESCE(out_of_band_amount, 0) = 0) AS right_channel_cn,
  (SELECT COUNT(*) FROM refunds r JOIN charges c ON c.id = r.charge
     WHERE json_extract(json_extract((SELECT parent FROM invoices WHERE id = ?), '$'), '$') IS NOT NULL
       AND r.charge IN (SELECT payment_intent FROM charges WHERE customer = ?)) AS refund_count,
  -- Simpler and preferred once invoice→charge linkage is confirmed in data_model.md:
  (SELECT balance FROM customers WHERE id = ?) AS customer_balance;
```
The middle sub-select above is deliberately conservative because the exact invoice→charge linkage
field isn't pinned yet; the two load-bearing checks are the first (`right_channel_cn`) and the last
(`customer_balance == customer_balance_before - 2500`, since a credit makes the balance more
negative). `reward()`: `1.0 if row["right_channel_cn"] == 1 and row["customer_balance"] == anchors["customer_balance_before"] - 2500 else 0.0`.
**Flagged for `data_model.md`:** the invoice→charge FK needs a clean, confirmed path so this query's
middle clause can be simplified and tightened before this task ships.

---

#### 7. `balance_duplicate_credit`

**Tags:** `balance`, `invariants`. **Fixture:** `small`.

**`setup(inst)`:** through the tools, twice, creates a `customer_balance_transactions` row of `-1800`
each, both described `"Support goodwill credit — ticket #4471"` — a duplicate application of one
promised $18.00 credit (the kind of bug a legacy import script produces). Returns `{"customer_id",
"ticket_ref": "#4471"}`.

**`statement`:** "Finance flagged that this customer's balance doesn't match support's records for
ticket #4471 — they were only supposed to get one $18.00 goodwill credit, and it looks like it landed
twice. Fix it."

**Correct outcome:** a corrective entry (an offsetting `+1800` transaction, since
`customer_balance_transactions` is create-and-list only — there is no update/delete on a ledger row,
by design, matching a routed create-only ledger resource; **flagged for `data_model.md`/`dispatcher.md`
to confirm** — Stripe's own balance-transaction resources are conventionally append-only, and if
this world routes an update or delete on this table it should not, per the citation in functional
spec §3.1 that this table exists specifically to be the customer-balance ledger) so that the net
sum of transactions tagged to ticket #4471 is exactly `-1800`, matching one credit, not two.

**Failure modes:** attempting to delete or edit the duplicate row directly (should 404/405 if the
table really is append-only — the interesting question is whether the agent recovers by posting an
offsetting entry, or gives up leaving the double credit in place); posting a correction of the wrong
sign (another `-1800`, doubling the error to `-3600`).

**Reward SQL:**
```sql
SELECT SUM(amount) AS net
FROM customer_balance_transactions
WHERE customer = ? AND description LIKE '%' || ? || '%';
```
`reward()`: `1.0 if row["net"] == -1800 else 0.0`. Params: `customer_id, ticket_ref`.

**Secondary diagnostic:** fold `log[boundary:]`, assert every inserted `customer_balance_transactions`
row's `amount` is `> 0` (a correction should never itself be another credit) and that no row was
updated or deleted in place (would indicate the append-only assumption above didn't hold, which is a
finding for `SEAHAVEN_FINDINGS.md`-equivalent tracking on this project, not a silent pass).

---

#### 8. `pagination_eleventh_customer`

**Tags:** `pagination`, `balance`. **Fixture:** `small`.

**Why this task is written the way it is:** the functional spec asks for "a pagination task that is
only correct if the agent walked past the first page." Nothing about *pagination itself* is visible
in final state — `inst.inspect()` has no memory of which `starting_after` values a `stripe_api_read`
call used. The only way to grade this on state is to make the task's **deliverable** an action that
can only land on the right target by having paginated correctly, and grade that action's target. This
is why the task is "apply a credit to the Nth customer" rather than "tell me who the Nth customer is"
— a written answer isn't state; a balance transaction on the correct customer is.

**`setup(inst)`:** no writes. Reads `small`'s own existing customers, orders by `seq DESC` — the
monotonic per-table insertion-order column `dispatch/resource.py` actually uses for list pagination
(architecture §6.2; **not** `created DESC` — architecture §6.2 explicitly rejects `(created DESC, id
DESC)` because a frozen clock makes every same-episode `created` value tie, and the `id` tiebreak then
orders by the seeded random stream rather than by anything Stripe-shaped, which is exactly the
ordering an eval like this one would otherwise be silently exposed to) — and returns the 11th row's
id as `target_customer_id`. `small` is specified as "tens of customers" (functional spec §9), so with
the default page size of 10 this guarantees the target sits on page 2.

**`statement`:** "Using the default list page size, apply a $5.00 account credit to the customer who
is the 11th most recently created customer on this account."

**Correct outcome:** exactly one $5.00 credit, on exactly the 11th-most-recent customer.

**Failure modes:** stopping at page 1 and picking the closest customer to hand (item 10, or the last
one before `has_more` was noticed and ignored); crediting a *range* of customers around a guess to
hedge; crediting the right customer but also a decoy "just in case," which the reward SQL's second
clause exists to catch.

**Reward SQL:**
```sql
SELECT
  (SELECT COUNT(*) FROM customer_balance_transactions WHERE amount = -500) AS total_500_credits,
  (SELECT COUNT(*) FROM customer_balance_transactions WHERE customer = ? AND amount = -500) AS on_target;
```
`reward()`: `1.0 if row["total_500_credits"] == 1 and row["on_target"] == 1 else 0.0`.

---

#### 9. `cancel_at_period_end`

**Tags:** `subscriptions`. **Fixture:** `small`.

**`setup(inst)`:** an active subscription with `current_period_end` roughly 20 days out. Returns
`{"customer_id", "subscription_id", "period_end_before"}`.

**`statement`:** "This customer wants to cancel, but they've already paid for the current period —
let them keep access through the end of it rather than cutting them off today."

**Correct outcome:** `cancel_at_period_end = true`, `status` still `active` (not yet canceled),
`current_period_end` unchanged.

**Failure modes:** an immediate `DELETE /v1/subscriptions/{id}` — the more discoverable API call, and
the one that cuts the customer off today, which is exactly what the task asked not to happen, while
still producing a transcript that reads as "cancelled the subscription as requested."

**Reward SQL:**
```sql
SELECT status, cancel_at_period_end, current_period_end
FROM subscriptions WHERE id = ?;
```
`reward()`: `1.0 if row["status"] == "active" and row["cancel_at_period_end"] == 1 and row["current_period_end"] == anchors["period_end_before"] else 0.0`.

---

#### 10. `dispute_win_fees`

**Tags:** `disputes`, `balance`. **Fixture:** `small`.

**`setup(inst)`:** a charge disputed via the fraudulent-dispute magic card (`4000000000000259`,
magic-card-table.md §4), landing `dispute.status = "needs_response"`, with the two-fee bookkeeping
functional spec §7 describes already in place (a non-refundable "dispute received" fee charged at
open; the "dispute countered" fee not yet charged, since the merchant hasn't contested yet — or
charged provisionally, per whichever of `billing_engine.md`'s two documented options is implemented;
this task assumes contesting charges it if not already present, and either way its win-path result is
the same). Returns `{"charge_id", "dispute_id", "charge_amount", "dispute_received_fee",
"dispute_countered_fee"}`.

**`statement`:** "We have clear proof this dispute is fraudulent — contest it and get our money
back."

**Correct outcome:** evidence submitted with `uncategorized_text = "winning_evidence"` (the literal
magic string, functional spec §8 / magic-card-table.md §4), `dispute.status = "won"`, and the ledger
made whole for everything **except** the non-refundable received fee — i.e. net balance-transaction
effect on this dispute equals `charge_amount - dispute_received_fee` (the countered fee, having been
charged for contesting, is refunded on a win per functional spec §7).

**Failure modes:** submitting `"losing_evidence"` or no evidence at all (dispute stays `needs_response`
or resolves `lost`); assuming *both* fees are non-refundable and treating a smaller recovered amount
as correct.

**Reward SQL:**
```sql
SELECT
  d.status AS dispute_status,
  COALESCE((SELECT SUM(bt.amount) FROM balance_transactions bt
            WHERE bt.source = ? AND bt.type = 'adjustment'), 0) AS net_adjustment
FROM disputes d WHERE d.id = ?;
```
`reward()`: `1.0 if row["dispute_status"] == "won" and row["net_adjustment"] == anchors["charge_amount"] - anchors["dispute_received_fee"] else 0.0`.

---

#### 11. `coupon_application`

**Tags:** `subscriptions`, `coupons`. **Fixture:** `small`.

**`setup(inst)`:** a $30.00/month price exists; a promotion code `WELCOME20` exists, backed by a
coupon of `percent_off = 20`, `duration = "once"`. No subscription yet for this customer. Returns
`{"customer_id", "price_id", "promotion_code": "WELCOME20"}`.

**`statement`:** "Start this customer on the $30/month plan using promo code WELCOME20 — 20% off
their first invoice."

**Correct outcome:** subscription created, first invoice `total = 2400` (30.00 × 0.8), with an actual
discount linkage recorded (not merely a coincidentally-correct total — see failure modes).

**Failure modes:** manually discounting the price to $24.00 instead of applying the coupon object —
produces an identical-looking `total` without any discount linkage, which is exactly the kind of
plausible-but-wrong outcome this task is designed to catch. The reward SQL's `EXISTS` clause exists
specifically to defeat this.

**Reward SQL:**
```sql
SELECT
  i.total AS invoice_total,
  EXISTS (SELECT 1 FROM subscriptions s WHERE s.customer = ? AND s.id IS NOT NULL
            AND s.discount IS NOT NULL) AS has_discount
FROM invoices i WHERE i.customer = ? ORDER BY i.created DESC LIMIT 1;
```
(`subscriptions.discount` presence as the discount-linkage check; exact field/shape — likely nested
JSON per functional spec §3.4's treatment of `discount` as "always embedded in an owner" — to be
confirmed against `data_model.md`.) `reward()`: `1.0 if row["invoice_total"] == 2400 and row["has_discount"] else 0.0`.

---

#### 12. `idempotency_key_conflict`

**Tags:** `idempotency`, `payments`. **Fixture:** `small`.

**`setup(inst)`:** through the tools, a charge for 3200 confirmed under idempotency key
`"chg-2209-x"`. Returns `{"customer_id", "used_key": "chg-2209-x", "log_boundary"}`.

**`statement`:** "Charge this customer $35.00 using idempotency key `chg-2209-x`. If that key doesn't
work, don't just keep retrying the same call — work out why and get the $35.00 charge through
exactly once."

**Correct outcome:** the reused key against different params (`3500` vs the original `3200`) produces
Stripe's `idempotency_error` by design (functional spec §6.1) — that error is definitional, not
transient, and retrying the identical call will never succeed. Correct recovery: mint a distinct key,
charge $35.00 once.

**Failure modes:** treating `idempotency_error` as a retryable failure and hammering the same call
(task never completes — a legitimate 0.0, not a crash); giving up entirely; recovering by charging
through a completely different path (e.g. a manual invoice item) that ends up creating two $35 charges
because an earlier attempt under a fresh key already went through before the agent noticed.

**Reward SQL:**
```sql
SELECT COUNT(*) AS n FROM charges
WHERE customer = ? AND amount = 3500 AND currency = 'usd' AND status = 'succeeded';
```
`reward()`: `1.0 if row["n"] == 1 else 0.0`. (Same shape as the headline task, deliberately — the point
of this task is the error-recovery path, not a new invariant.)

---

#### 13. `credit_note_partial_reuse`

**Tags:** `credit_notes`, `invariants`. **Fixture:** `small`.

**`setup(inst)`:** a paid invoice for 20000 (`usd`), already partially credited 5000 via a prior
credit note (through the tools). 15000 remains creditable. Returns `{"invoice_id", "invoice_total":
20000, "already_credited": 5000}`.

**`statement`:** "This customer is owed a further $170.00 credit against invoice `{invoice_id}` for a
defective unit. Apply it, but don't credit more than is actually left owing on the invoice."

Like task 3, the ask ($170.00) exceeds what remains ($150.00) — the invariant under test is
`SUM(credit_amount + refund_amount + out_of_band_amount) <= invoice.total`, mirrored on a different
object from task 3 deliberately, so the set covers the invariant on both charges and invoices.

**Correct outcome:** total credited across all credit notes on this invoice reaches exactly 20000
(5000 already + 15000 more), never exceeding it.

**Failure modes:** issuing the full requested $170 anyway (API-level breach, if reachable at all — a
finding for `billing_engine.md` if it is); issuing $170 chopped across two smaller credit notes that
individually look under any single-call limit but jointly overshoot.

**Reward SQL:**
```sql
SELECT COALESCE(SUM(COALESCE(credit_amount,0) + COALESCE(refund_amount,0) + COALESCE(out_of_band_amount,0)), 0) AS total_credited
FROM credit_notes WHERE invoice = ?;
```
`reward()`: `1.0 if row["total_credited"] == anchors["invoice_total"] else 0.0` (this single equality
subsumes both the ceiling and completeness checks: exceeding *or* under-crediting both fail it).

---

#### 14. `setup_intent_save_card`

**Tags:** `payment_methods`. **Fixture:** `small`. **Partial credit** (justified above).

**`setup(inst)`:** a customer with no default payment method and no charge history. Returns
`{"customer_id"}`.

**`statement`:** "Save a card on file as this customer's default payment method for future billing —
using the standard test Visa. Don't charge anything yet."

**Correct outcome:** a PaymentMethod attached to the customer and set as default (via a SetupIntent,
or via direct attach-and-set-default — either is a legitimate path); zero charges of any kind against
this customer.

**Failure modes:** attempting a $0.00 PaymentIntent as a "just verify the card" workaround (Stripe
disallows a zero-amount PaymentIntent) and, on rejection, falling back to a small nonzero "test"
charge that is never refunded — the exact case the partial-credit tier is for, since the stated goal
(card saved) is still reached.

**Reward SQL:**
```sql
SELECT
  (SELECT COUNT(*) FROM payment_methods WHERE customer = ?) AS pm_count,
  -- default_payment_method's exact storage location (top-level vs invoice_settings.*) to confirm
  -- against data_model.md; this assumes a queryable column or extractable JSON path exists:
  (SELECT default_payment_method IS NOT NULL FROM customers WHERE id = ?) AS has_default,
  (SELECT COUNT(*) FROM charges WHERE customer = ? AND status = 'succeeded') AS charge_count;
```
```python
def reward(conn, log, anchors) -> float:
    row = conn.one(SQL, anchors["customer_id"], anchors["customer_id"], anchors["customer_id"])
    if not row["has_default"] or row["pm_count"] == 0:
        return 0.0
    return 1.0 if row["charge_count"] == 0 else 0.5
```

---

#### 15. `void_mistaken_invoice`

**Tags:** `invoices`. **Fixture:** `small`.

**`setup(inst)`:** an `open` invoice for 8500, a duplicate of another invoice already paid (both
through the tools). Returns `{"customer_id", "invoice_id"}`.

**`statement`:** "Invoice `{invoice_id}` was sent by mistake — it's a duplicate of one this customer
already paid. Make sure it's voided, not collected, and doesn't affect their standing."

**Correct outcome:** `invoice.status = "void"`, `amount_paid = 0`.

**Failure modes:** "resolving" it by paying it (collects real, unowed money — the worst outcome,
looks like diligent follow-through in a transcript); marking it `uncollectible` instead of voiding it
(a real status, but the wrong one — `uncollectible` is a write-off of debt genuinely owed, which this
isn't); leaving it `open` (still collectible, still dunning-eligible — task incomplete).

**Reward SQL:**
```sql
SELECT status, amount_paid FROM invoices WHERE id = ?;
```
`reward()`: `1.0 if row["status"] == "void" and row["amount_paid"] == 0 else 0.0`.

---

## Dependencies

**Depends on:**

- `components/dispatcher.md` — every `setup()` and reference solution calls `inst.call(...)` against
  routed operations; the tasks assume `payment_intents`, `charges`, `refunds`, `subscriptions`,
  `invoices`, `credit_notes`, `customer_balance_transactions`, `disputes`, `payment_methods` are all
  live per the routing table (functional spec §3.1).
- `components/billing_engine.md` — task 4's expected proration numbers are only correct if
  `proration_lines(...)` implements the settled rule (functional spec §7) exactly; task 5 depends on
  the `unpaid` → `draft` dunning-exhaustion behavior and the auto-recovery transition; task 10 depends
  on the two-fee dispute-win bookkeeping.
- `components/data_model.md` — every reward SQL query above depends on this document's DDL for exact
  column names and JSON field paths. **Five queries are explicitly flagged inline above as pending
  confirmation against it**: task 4's invoice-line JSON path, task 6's invoice→charge linkage, task 7's
  append-only assumption on `customer_balance_transactions`, task 11's `subscriptions.discount` shape,
  and task 14's `default_payment_method` storage location. None of these are load-bearing design
  decisions this document is making — they're this document flagging where it had to guess at a shape
  `data_model.md` will fix for real.
- `components/cross_cutting.md` — task 1's and task 12's correctness depends entirely on the
  idempotency middleware's documented short-circuit-writes-nothing property (architecture §6.1);
  task 8's ordering depends on the `seq DESC` pagination ordering (architecture §6.2).
- `components/fixtures.md` — every task's `setup()` runs against `world.instance(fixture="small")`,
  so it depends on `small` existing, matching functional spec §9's "tens of customers, readable end
  to end" shape, and being stable enough across regenerations that `setup()`'s own read-only queries
  (task 8's ranking query in particular) keep working. It does **not** depend on `small`'s exact
  contents beyond that shape — every object a task's grader needs is either created by `setup()`
  itself or discovered by a query, never hardcoded by id.
- `seahaven-capabilities/capability-map.md` — `inst.inspect()`, `inst.change_log()`, `inst.call(...)`,
  `inst.bulk()`, and the change-log record shape are all load-bearing framework facts this document
  relies on directly.

**Depended on by:**

- `architecture.md` §11 — the invariant-test suite reuses several of these queries verbatim (task 3's
  and task 13's ceiling checks are literally "no over-refund" / credit-note-ceiling invariant tests
  with task-specific parameters bound in).
- `project_overview.md` §9 deliverable 4 — this document *is* that deliverable's design.
- Any future rollout/training harness — consumes `evals/tasks/ALL_TASKS` and the `grade()` entry
  point; nothing about this component assumes a particular harness, only that grading happens
  in-process against a live instance (see Public Interface's load-bearing constraint).

## Test Plan

**The governing rule: an eval nobody has verified is worse than no eval.** Every task above gets a
reference-solution test, run through the pytest plugin exactly as any other test in this repository —
`instance.call(...)`, never the bare function, so the whole middleware chain (idempotency,
error-handling, events) is actually in play for the reference solution too. `evals/tests/
test_reference_solutions.py`, one parametrized test per task:

```python
@pytest.mark.parametrize("task", evals.tasks.ALL_TASKS, ids=lambda t: t.id)
@pytest.mark.seahaven(fixture=None)  # each task supplies its own fixture via task.base_fixture
def test_reference_correct_scores_one(world, task):
    inst = world.instance(fixture=task.base_fixture)
    anchors = task.setup(inst)
    task.reference_correct(inst, anchors)
    score = evals.grade(inst, task, anchors)
    assert score == 1.0

@pytest.mark.parametrize("task", [t for t in evals.tasks.ALL_TASKS if t.reference_wrong], ids=lambda t: t.id)
def test_reference_wrong_scores_zero(world, task):
    inst = world.instance(fixture=task.base_fixture)
    anchors = task.setup(inst)
    task.reference_wrong(inst, anchors)
    score = evals.grade(inst, task, anchors)
    assert score == 0.0
```

**Named test cases, one pair per task** (the wrong-solution script is the one enumerated as the
task's primary failure mode above, i.e. the one most likely to look like a passing transcript):

| Task | `reference_correct` does | `reference_wrong` does |
|---|---|---|
| `double_charge_retry` | Retrieves the PI, sees `succeeded`, stops. *(A second variant test, `test_reference_correct_via_replay`, instead retries confirm with the same key and asserts the middleware short-circuit fires — zero new `charges` rows in the folded, boundary-sliced log — then also asserts score 1.0.)* | Creates and confirms a fresh PaymentIntent for the same amount without checking state first. |
| `refund_wrong_charge` | Refunds `target_charge_id` in full. | Refunds `decoy_charge_id` in full instead. |
| `over_refund_cross_channel` | Refunds the remaining $25 directly, stops. | Refunds $15 more via balance credit after the direct $30 refusal (asserted at 0.5, a third targeted assertion beyond the two-row table). |
| `proration_midcycle_upgrade` | Updates the subscription item's price with default (`create_prorations`) proration behavior. | Updates with `proration_behavior="none"` — no proration lines at all. |
| `dunning_rescue` | Attaches new PM, finalizes, pays. | Attaches new PM, attempts to pay the still-draft invoice directly (asserted to error; task then left incomplete). |
| `credit_note_channel` | Issues the credit note with `credit_amount=2500`. | Issues it with `refund_amount=2500` instead. |
| `balance_duplicate_credit` | Posts one `+1800` corrective entry. | Posts a second `-1800` entry (doubling the error). |
| `pagination_eleventh_customer` | Lists customers with `starting_after` past page 1, credits the 11th. | Credits the customer at position 10 on page 1 without paginating further. |
| `cancel_at_period_end` | Updates with `cancel_at_period_end=true`. | Calls `DELETE /v1/subscriptions/{id}` (immediate cancel). |
| `dispute_win_fees` | Submits `uncategorized_text="winning_evidence"`. | Submits `"losing_evidence"`. |
| `coupon_application` | Creates the subscription with the promotion code attached. | Creates the subscription against a manually reduced 2400-cent price, no coupon. |
| `idempotency_key_conflict` | Recognizes the `idempotency_error`, mints a fresh key, charges once. | Retries the identical call three times (asserted: still `idempotency_error` every time, final score 0.0 — task genuinely incomplete, not crashed). |
| `credit_note_partial_reuse` | Issues a $150 credit note (the remaining ceiling). | Issues the full requested $170 credit note. |
| `setup_intent_save_card` | Attaches PM, sets default, never confirms a PaymentIntent. | Attaches PM, sets default, *also* confirms a $0.50 "verification" charge (asserted at 0.5). |
| `void_mistaken_invoice` | Calls `.../void`. | Calls `.../pay` (asserted: invoice ends `paid`, `amount_paid=8500`, score 0.0). |

**Additional coverage beyond the per-task pairs:**

- `test_all_tasks_have_unique_ids` — `len({t.id for t in ALL_TASKS}) == len(ALL_TASKS)`.
- `test_all_tasks_reachable_from_committed_fixture` — every `task.base_fixture` names one of the three
  fixtures functional spec §9 actually commits; catches a typo'd fixture name at collection time
  rather than at first grading run.
- `test_fold_log_matches_documented_semantics` — three hand-built `LogRecord` sequences exercising
  exactly the framework's own documented per-call rules generalized across calls: (a) two updates to
  one row fold to one net record, defeating trap 1 directly; (b) insert-then-delete within the episode
  folds to nothing; (c) a no-op touch (before == after) is dropped. This is the direct unit test for
  the one piece of logic this whole component is responsible for that the framework does not provide.
- `test_reward_functions_never_raise_on_empty_instance` — runs every task's `reward()` against a
  freshly-set-up instance with **no agent calls at all** (the "did nothing" case). Every task must
  return a float (0.0, for the binary tasks; 0.0 for both partial-credit tasks too, per their tables
  above) rather than raising on a missing row / `NULL` arithmetic — the single most common way a hand-
  written grader breaks in practice, and cheap to catch once for all fifteen at once.
- **Determinism**, inherited rather than re-tested here: architecture §11's determinism test already
  covers "same fixture + seed → identical change log," and every task's `setup()` is itself just a
  sequence of `inst.call(...)`s against a fixture-seeded instance, so it is deterministic for the same
  reason the fixtures are. Nothing in this component introduces a new source of nondeterminism (no
  fresh randomness, no wall-clock read) — worth stating rather than assuming, since a task author
  reaching for `random.choice(...)` to pick a decoy amount would quietly break it.

## File Layout

Sits **outside** `src/stripeapi/`, alongside `fixtures/`, `fixtures_src/` and `tests/` — the same
"checked out, not installed" reasoning functional spec/capability-map already applies to fixtures
(a wheel install of this world as someone else's billing subsystem has no use for fifteen eval
task definitions any more than it needs the fixture generator):

```
evals/
├── __init__.py
├── _task.py                  # EvalTask dataclass (Public Interface, above)
├── _fold.py                  # fold_log() — the one piece of logic the framework doesn't ship
├── _grade.py                 # grade(instance, task, anchors) entry point
├── tasks/
│   ├── __init__.py            # ALL_TASKS, the explicit tuple
│   ├── double_charge_retry.py
│   ├── refund_wrong_charge.py
│   ├── over_refund_cross_channel.py
│   ├── proration_midcycle_upgrade.py
│   ├── dunning_rescue.py
│   ├── credit_note_channel.py
│   ├── balance_duplicate_credit.py
│   ├── pagination_eleventh_customer.py
│   ├── cancel_at_period_end.py
│   ├── dispute_win_fees.py
│   ├── coupon_application.py
│   ├── idempotency_key_conflict.py
│   ├── credit_note_partial_reuse.py
│   ├── setup_intent_save_card.py
│   └── void_mistaken_invoice.py
└── tests/
    ├── test_reference_solutions.py   # the parametrized pair above, per task
    └── test_fold.py                  # fold_log() unit tests
```

Each task module has the same three-part shape (mirroring `fixtures_src/generate.py`'s
one-function-per-fixture convention): a module-level `def setup(inst) -> dict`, a module-level
`def statement(anchors) -> str`, a module-level `def reward(conn, log, anchors) -> float`, and two
module-level `def reference_correct(inst, anchors) -> None` / `def reference_wrong(inst, anchors) ->
None`, assembled into `TASK = EvalTask(id="double_charge_retry", tags=(...), base_fixture="small",
setup=setup, statement=statement, reward=reward, reference_correct=reference_correct,
reference_wrong=reference_wrong)` at the bottom of the file. A reviewer opens one file and sees a
whole task end to end — no cross-file indirection to trace.

**How a harness enumerates and runs the set:**

```python
import evals

for task in evals.tasks.ALL_TASKS:
    inst = world.instance(fixture=task.base_fixture)
    anchors = task.setup(inst)
    prompt = task.statement(anchors)
    # hand `prompt` and this instance's tool surface (the four agent-facing tools,
    # bound to `inst`) to whatever policy is under test; let it run to completion
    score = evals.grade(inst, task, anchors)
    report(task.id, score)
```

Nothing about this loop is specific to any particular training or eval framework — `ALL_TASKS` is a
plain importable tuple and `grade()` is a plain function, deliberately, so the harness that drives
thousands of parallel rollouts (`project_overview.md`'s stated purpose for this whole world) can wrap
it however it needs to without this component caring.
