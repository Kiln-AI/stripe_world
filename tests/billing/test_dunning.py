"""Dunning: the counter semantics (`spec3.json`'s own most precise
statement), the nine hard-decline codes, the policy envelope, and the three
end-of-schedule outcomes — plus the frozen-clock facts pinned as tests
(billing_engine §5)."""

import pytest
import seahaven

from conftest import BLANK_NOW, api_read, api_write
from seahaven_stripe_world.billing import dunning
from seahaven_stripe_world.errors import StripeToolError
from seahaven_stripe_world.spec.enums import DECLINE_CODES

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW, clock_mode="fixed")


# --- the pure core -----------------------------------------------------------------------


def test_first_attempt_sets_one_manual_or_automatic() -> None:
    assert dunning.next_attempt_number(0, automatic=False) == 1
    assert dunning.next_attempt_number(0, automatic=True) == 1


def test_manual_retries_never_increment_past_one() -> None:
    assert dunning.next_attempt_number(1, automatic=False) == 1
    assert dunning.next_attempt_number(4, automatic=False) == 4


def test_automatic_retries_increment() -> None:
    assert dunning.next_attempt_number(1, automatic=True) == 2
    assert dunning.next_attempt_number(7, automatic=True) == 8


def test_nine_hard_decline_codes_exactly() -> None:
    assert len(dunning.RETRY_BLOCKING_DECLINE_CODES) == 9


def test_hard_decline_codes_are_all_real_decline_codes() -> None:
    # The two sources stay aligned: every gating code is a member of the
    # extracted 50-value enumeration — with ONE declared divergence, the
    # docs' own `highest_risk_level`, which the pinned spec's enum does not
    # carry (billing_engine §5 corrected by the docs; see the module's
    # declaration on RETRY_BLOCKING_DECLINE_CODES).
    assert {"highest_risk_level"} == dunning.RETRY_BLOCKING_DECLINE_CODES - DECLINE_CODES
    assert dunning.RETRY_BLOCKING_DECLINE_CODES - {"highest_risk_level"} <= DECLINE_CODES


def test_will_reach_network_gates_exactly_the_nine() -> None:
    assert dunning.will_reach_network(None) is True
    assert dunning.will_reach_network("insufficient_funds") is True
    for code in dunning.RETRY_BLOCKING_DECLINE_CODES:
        assert dunning.will_reach_network(code) is False


def test_schedule_exhaustion_is_the_max_attempts() -> None:
    policy = dunning.RetryPolicy()
    assert dunning.schedule_exhausted(7, policy) is False
    assert dunning.schedule_exhausted(8, policy) is True
    assert dunning.schedule_exhausted(9, policy) is True


def test_next_attempt_clears_at_exhaustion() -> None:
    policy = dunning.RetryPolicy()
    assert dunning.next_attempt_at(BLANK_NOW, 1, policy) is not None
    assert dunning.next_attempt_at(BLANK_NOW, 8, policy) is None


def test_the_policy_reads_the_instance_config() -> None:
    class _State(dict):
        pass

    # The default, with no account config at all
    ctx = _CtxStub({})
    assert dunning.policy(ctx).max_attempts == 8
    assert dunning.policy(ctx).window == "2w"
    assert dunning.policy(ctx).end_behavior == "cancel"
    # And the override seam, the ledger_spec shape
    ctx = _CtxStub(
        {"account": {"dunning": {"max_attempts": 3, "window": "1w", "end_behavior": "mark_unpaid"}}}
    )
    policy = dunning.policy(ctx)
    assert (policy.max_attempts, policy.window, policy.end_behavior) == (3, "1w", "mark_unpaid")


class _CtxStub(seahaven.Ctx):
    def __init__(self, state: dict) -> None:  # deliberately no super().__init__
        self._state = state

    @property
    def state(self) -> dict:
        return self._state


def test_the_policy_is_not_a_subscription_field(instance: seahaven.Instance) -> None:
    """Stripe exposes no retry-schedule field on the subscription object, so
    a column or parameter here would be a fidelity regression
    (billing_engine §5) — asserted against the live schema and both
    parameter specs."""
    from seahaven_stripe_world.resources import subscriptions

    with instance.bulk() as ctx:
        columns = {
            row["name"]
            for row in ctx.db.rows("SELECT name FROM pragma_table_info('subscriptions')")
        }
    assert not any("retry" in column or "dunning" in column for column in columns)
    for spec in (subscriptions.SUB_CREATE, subscriptions.SUB_UPDATE):
        body = {param.name for param in spec.body or ()}
        assert not any("retry" in name or "dunning" in name for name in body)


# --- the routed counter semantics ---------------------------------------------------------


def _armed(instance: seahaven.Instance) -> tuple[str, str]:
    cus = call(instance, "POST", "/v1/customers", {"email": "dun@example.test"})["id"]
    pm = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {
            "type": "card",
            "card": {"number": "4000000000000341", "exp_month": 1, "exp_year": 2031},
        },
    )["id"]
    call(instance, "POST", f"/v1/payment_methods/{pm}/attach", {"customer": cus})
    call(
        instance,
        "POST",
        f"/v1/customers/{cus}",
        {"invoice_settings": {"default_payment_method": pm}},
    )
    return cus, pm


def call(instance: seahaven.Instance, method: str, path: str, params: dict | None = None):
    if method == "GET":
        return api_read(instance, path, params)
    return api_write(instance, method, path, params)


def one_row(instance: seahaven.Instance, sql: str, *params):
    with instance.bulk() as ctx:
        return ctx.db.one(sql, *params)


def _open_declining_invoice(instance: seahaven.Instance) -> str:
    """An OPEN invoice whose card declines, through the routed surface: an
    always_invoice proration switch against the decline card."""
    cus, _pm = _armed(instance)
    prod = call(instance, "POST", "/v1/products", {"name": "dun"})["id"]
    p10 = call(
        instance,
        "POST",
        "/v1/prices",
        {
            "product": prod,
            "unit_amount": 1000,
            "currency": "usd",
            "recurring": {"interval": "month"},
        },
    )["id"]
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": p10}], "payment_behavior": "allow_incomplete"},
    )
    assert sub["status"] == "incomplete"
    return sub["latest_invoice"]


def test_a_failed_attempt_counts_one_and_schedules_the_retry(instance: seahaven.Instance) -> None:
    invoice_id = _open_declining_invoice(instance)
    row = one_row(instance, "SELECT * FROM invoices WHERE id = ?", invoice_id)
    assert row["attempted"] == 1
    assert row["attempt_count"] == 1  # the first attempt, however made
    assert row["next_payment_attempt"] is not None
    assert row["status"] == "open"


def test_manual_pay_after_the_first_never_increments(instance: seahaven.Instance) -> None:
    invoice_id = _open_declining_invoice(instance)
    before = one_row(
        instance,
        "SELECT attempt_count, next_payment_attempt FROM invoices WHERE id = ?",
        invoice_id,
    )
    for _ in range(3):
        with pytest.raises(StripeToolError):
            call(instance, "POST", f"/v1/invoices/{invoice_id}/pay", {})
    after = one_row(
        instance,
        "SELECT attempt_count, next_payment_attempt FROM invoices WHERE id = ?",
        invoice_id,
    )
    assert after["attempt_count"] == before["attempt_count"] == 1
    assert after["next_payment_attempt"] == before["next_payment_attempt"]


def test_no_automatic_retry_fires_within_an_instance(instance: seahaven.Instance) -> None:
    """The frozen-clock statement as a test, not a comment: any number of
    unrelated calls never moves the counter or the schedule."""
    cus, _ = _armed(instance)
    invoice_id = _open_declining_invoice(instance)
    for _ in range(5):
        call(instance, "GET", "/v1/invoices", {"customer": cus})
        call(instance, "GET", f"/v1/invoices/{invoice_id}")
    row = one_row(instance, "SELECT * FROM invoices WHERE id = ?", invoice_id)
    assert row["attempt_count"] == 1
    assert row["status"] == "open"


# --- the walkers (fixture/test surface) ----------------------------------------------------


def _past_due_invoice(instance: seahaven.Instance) -> tuple[str, str]:
    invoice_id = _open_declining_invoice(instance)
    sub = one_row(
        instance, "SELECT parent_subscription AS s FROM invoices WHERE id = ?", invoice_id
    )["s"]
    with instance.bulk() as ctx:
        ctx.db.execute("UPDATE subscriptions SET status = 'past_due' WHERE id = ?", sub)
    return sub, invoice_id


def test_record_failed_attempt_increments_and_schedules(instance: seahaven.Instance) -> None:
    _sub, invoice_id = _past_due_invoice(instance)
    with instance.bulk() as ctx:
        dunning.record_failed_attempt(ctx, invoice_id, decline_code="insufficient_funds")
    row = one_row(instance, "SELECT * FROM invoices WHERE id = ?", invoice_id)
    assert row["attempt_count"] == 2
    assert row["next_payment_attempt"] is not None


def test_blocked_retry_moves_the_counter_but_writes_nothing(
    instance: seahaven.Instance,
) -> None:
    """The distinction a careless mock erases: for a hard decline the
    schedule advances (counter, next attempt) but no charge row exists and
    no event lands — nothing was attempted."""
    _sub, invoice_id = _past_due_invoice(instance)
    charges_before = one_row(instance, "SELECT count(*) AS n FROM charges")["n"]
    events_before = one_row(
        instance, "SELECT count(*) AS n FROM events WHERE type = 'invoice.payment_failed'"
    )["n"]
    with instance.bulk() as ctx:
        dunning.record_failed_attempt(ctx, invoice_id, decline_code="stolen_card")
    row = one_row(instance, "SELECT * FROM invoices WHERE id = ?", invoice_id)
    assert row["attempt_count"] == 2
    assert one_row(instance, "SELECT count(*) AS n FROM charges")["n"] == charges_before
    assert (
        one_row(instance, "SELECT count(*) AS n FROM events WHERE type = 'invoice.payment_failed'")[
            "n"
        ]
        == events_before
    )


def test_end_behavior_cancel_leaves_the_invoice_open(instance: seahaven.Instance) -> None:
    sub, invoice_id = _past_due_invoice(instance)
    with instance.bulk() as ctx:
        for _ in range(7):  # attempts 2..8 exhaust the default policy
            dunning.record_failed_attempt(ctx, invoice_id, decline_code="insufficient_funds")
    row = one_row(instance, "SELECT * FROM subscriptions WHERE id = ?", sub)
    assert row["status"] == "canceled"
    assert row["ended_at"] is not None
    import json

    assert json.loads(row["cancellation_details"])["reason"] == "payment_failed"
    # The open invoice that caused the dunning is LEFT OPEN (billing_engine
    # §1: Stripe does not void it) — it is the recovery invoice.
    invoice = one_row(instance, "SELECT status FROM invoices WHERE id = ?", invoice_id)
    assert invoice["status"] == "open"


def test_end_behavior_mark_unpaid(instance: seahaven.Instance) -> None:
    sub, invoice_id = _past_due_invoice(instance)
    with instance.bulk() as ctx:
        ctx.state["account"]["dunning"] = {"end_behavior": "mark_unpaid"}
        for _ in range(7):
            dunning.record_failed_attempt(ctx, invoice_id, decline_code="insufficient_funds")
    assert one_row(instance, "SELECT status FROM subscriptions WHERE id = ?", sub)["status"] == (
        "unpaid"
    )


def test_end_behavior_leave_past_due(instance: seahaven.Instance) -> None:
    sub, invoice_id = _past_due_invoice(instance)
    with instance.bulk() as ctx:
        ctx.state["account"]["dunning"] = {"end_behavior": "leave_past_due"}
        for _ in range(7):
            dunning.record_failed_attempt(ctx, invoice_id, decline_code="insufficient_funds")
    assert one_row(instance, "SELECT status FROM subscriptions WHERE id = ?", sub)["status"] == (
        "past_due"
    )


def test_leave_past_due_fires_no_event(instance: seahaven.Instance) -> None:
    """billing_engine §1's `past_due` table lists NO event for the
    `leave_past_due` row (cancel fires `…deleted`, `mark_unpaid`
    `…updated`) — a degenerate "updated" with empty previous_attributes
    would be noise."""
    _sub, invoice_id = _past_due_invoice(instance)
    with instance.bulk() as ctx:
        ctx.state["account"]["dunning"] = {"end_behavior": "leave_past_due"}
        for _ in range(7):
            dunning.record_failed_attempt(ctx, invoice_id, decline_code="insufficient_funds")
    events = instance.inspect().rows(
        "SELECT type FROM events WHERE type = 'customer.subscription.updated'"
    )
    assert events == []


def test_the_policy_refuses_invalid_config_at_the_seam() -> None:
    """An authoring fault at the config seam, not a KeyError from deep
    inside a walker or a typo silently behaving as `leave_past_due`."""
    with pytest.raises(seahaven.WorldBug, match=r"account\.dunning\.window"):
        dunning.policy(_CtxStub({"account": {"dunning": {"window": "4w"}}}))
    with pytest.raises(seahaven.WorldBug, match=r"account\.dunning\.end_behavior"):
        dunning.policy(_CtxStub({"account": {"dunning": {"end_behavior": "unpaid"}}}))
