"""The tools as an agent meets them: the contract of each face."""

import pytest
import seahaven

from conftest import BLANK_NOW
from seahaven_stripe_world.errors import SessionValidation, UnknownOperation
from seahaven_stripe_world.startup import ACCOUNT_ID

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def test_the_read_tool_refuses_a_non_string_operation_id(instance: seahaven.Instance) -> None:
    """A non-string operation_id is the framework's own INVALID_INPUT, not a
    Stripe envelope (functional spec §2.3)."""
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call(
            "stripe_api_read",
            stripe_api_operation_id=123,
            parameters={},
            stripe_context=ACCOUNT_ID,
            livemode=True,
        )
    assert raised.value.code == "INVALID_INPUT"


def test_the_write_tool_refuses_a_get_operation(instance: seahaven.Instance) -> None:
    """A GET operation ID through the write tool raises UnknownOperation —
    the write resolver only accepts POST/DELETE."""
    with pytest.raises(UnknownOperation):
        instance.call(
            "stripe_api_write",
            stripe_api_operation_id="GetCustomers",
            parameters={},
            stripe_context=ACCOUNT_ID,
            livemode=True,
        )


def test_a_non_object_params_is_refused_before_the_dispatcher(
    instance: seahaven.Instance,
) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call(
            "stripe_api_read",
            stripe_api_operation_id="GetCustomers",
            parameters=["nope"],
            stripe_context=ACCOUNT_ID,
            livemode=True,
        )
    assert raised.value.code == "INVALID_INPUT"


def test_the_read_tool_cannot_reach_a_post_only_route(instance: seahaven.Instance) -> None:
    """A POST-only operation ID through the read tool raises UnknownOperation —
    the read resolver only accepts GET."""
    with pytest.raises(UnknownOperation):
        instance.call(
            "stripe_api_read",
            stripe_api_operation_id="PostChargesChargeCapture",
            parameters={},
            stripe_context=ACCOUNT_ID,
            livemode=True,
        )


def test_wrong_stripe_context_refuses_before_dispatch(instance: seahaven.Instance) -> None:
    """A stripe_context that does not match the session account raises
    SessionValidation on every tool."""
    for tool in ("stripe_api_read", "stripe_api_write"):
        with pytest.raises(SessionValidation, match="list_available_accounts_or_orgs"):
            instance.call(
                tool,
                stripe_api_operation_id="GetCustomers",
                parameters={},
                stripe_context="acct_wrong",
                livemode=True,
            )
    with pytest.raises(SessionValidation, match="list_available_accounts_or_orgs"):
        instance.call(
            "stripe_api_search",
            intent="list",
            resource="customers",
            stripe_context="acct_wrong",
            livemode=True,
        )
    with pytest.raises(SessionValidation, match="list_available_accounts_or_orgs"):
        instance.call(
            "stripe_api_details",
            stripe_api_operation_id="GetCustomers",
            stripe_context="acct_wrong",
            livemode=True,
        )


def test_wrong_livemode_refuses_before_dispatch(instance: seahaven.Instance) -> None:
    """livemode=false against a live-mode account raises SessionValidation
    with guidance to retry with true."""
    with pytest.raises(SessionValidation, match="Retry with livemode set to true"):
        instance.call(
            "stripe_api_read",
            stripe_api_operation_id="GetCustomers",
            parameters={},
            stripe_context=ACCOUNT_ID,
            livemode=False,
        )


def test_call_stripe_reaches_every_verb(instance: seahaven.Instance) -> None:
    """The unregistered escape hatch cannot rot: it dispatches all three verbs
    over the same router (functional spec §2.5). Driven through `bulk()`'s
    context, the one context a caller can hold outside a tool call."""
    from seahaven_stripe_world.dispatch.response import ApiResponse
    from seahaven_stripe_world.tools.api import call_stripe

    with instance.bulk() as ctx:
        created = call_stripe(ctx, "POST", "/v1/customers", {"name": "hatch"})
        assert isinstance(created, ApiResponse)
        assert created.status == 200
        listed = call_stripe(ctx, "GET", "/v1/customers")
        assert listed.status == 200
        cus = listed.body["data"][0]["id"]
        deleted = call_stripe(ctx, "DELETE", f"/v1/customers/{cus}")
        assert deleted.status == 200
        assert deleted.body["deleted"] is True


def test_the_wired_surface_is_small_and_named() -> None:
    """What is served end to end: the customers phase's thirteen routes (five
    core customers, six payment_methods, two customer-scoped payment-method
    reads), the catalog phase's twenty-two (five products, four prices, five
    coupons, four promotion_codes, four tax_rates), the money path's twelve
    (seven payment_intents, five charges), the refunds-and-disputes
    phase's seventeen (five /v1/refunds, five charge-scoped refund routes,
    four /v1/disputes, three charge-scoped dispute routes), the
    setup-intents phase's seven, the ledger-and-payouts phase's nine
    (one balance, two balance_transactions, six payouts), the
    subscriptions phase's eleven (six subscriptions including `/resume`,
    five subscription_items), the subscription-schedules phase's six
    (two generated reads, four hand-written state transitions), the
    invoices phase's twenty (fifteen invoices -- `create_preview` and
    `attach_payment` stay unwired -- plus five invoiceitems), the
    credit-notes-and-customer-balance phase's twelve (eight credit_notes,
    four customer_balance_transactions), and the events phase's two
    (list and retrieve). The rest are routed data awaiting their resource
    phases, and calling one is an honest `INTERNAL` naming the op (see
    `test_dispatch.py`)."""
    wired = [route.op_id for route in _all_routes() if route.params is not None]
    assert wired == [
        "GetBalance",
        "GetBalanceTransactions",
        "GetBalanceTransactionsId",
        "GetCharges",
        "PostCharges",
        "GetChargesSearch",
        "GetChargesCharge",
        "PostChargesCharge",
        "PostChargesChargeCapture",
        "GetChargesChargeDispute",
        "PostChargesChargeDispute",
        "PostChargesChargeDisputeClose",
        "PostChargesChargeRefund",
        "GetChargesChargeRefunds",
        "PostChargesChargeRefunds",
        "GetChargesChargeRefundsRefund",
        "PostChargesChargeRefundsRefund",
        "GetCoupons",
        "PostCoupons",
        "GetCouponsCoupon",
        "PostCouponsCoupon",
        "DeleteCouponsCoupon",
        "GetCreditNotes",
        "PostCreditNotes",
        "GetCreditNotesPreview",
        "GetCreditNotesPreviewLines",
        "GetCreditNotesCreditNoteLines",
        "GetCreditNotesId",
        "PostCreditNotesId",
        "PostCreditNotesIdVoid",
        "GetCustomers",
        "PostCustomers",
        "GetCustomersSearch",
        "GetCustomersCustomer",
        "PostCustomersCustomer",
        "DeleteCustomersCustomer",
        "GetCustomersCustomerBalanceTransactions",
        "PostCustomersCustomerBalanceTransactions",
        "GetCustomersCustomerBalanceTransactionsTransaction",
        "PostCustomersCustomerBalanceTransactionsTransaction",
        "GetCustomersCustomerPaymentMethods",
        "GetCustomersCustomerPaymentMethodsPaymentMethod",
        "GetDisputes",
        "GetDisputesDispute",
        "PostDisputesDispute",
        "PostDisputesDisputeClose",
        "GetEvents",
        "GetEventsId",
        "GetInvoiceitems",
        "PostInvoiceitems",
        "GetInvoiceitemsInvoiceitem",
        "PostInvoiceitemsInvoiceitem",
        "DeleteInvoiceitemsInvoiceitem",
        "GetInvoices",
        "PostInvoices",
        "GetInvoicesSearch",
        "GetInvoicesInvoice",
        "PostInvoicesInvoice",
        "DeleteInvoicesInvoice",
        "PostInvoicesInvoiceAddLines",
        "PostInvoicesInvoiceFinalize",
        "GetInvoicesInvoiceLines",
        "PostInvoicesInvoiceLinesLineItemId",
        "PostInvoicesInvoiceMarkUncollectible",
        "PostInvoicesInvoicePay",
        "PostInvoicesInvoiceRemoveLines",
        "PostInvoicesInvoiceSend",
        "PostInvoicesInvoiceUpdateLines",
        "PostInvoicesInvoiceVoid",
        "GetPaymentIntents",
        "PostPaymentIntents",
        "GetPaymentIntentsSearch",
        "GetPaymentIntentsIntent",
        "PostPaymentIntentsIntent",
        "PostPaymentIntentsIntentCancel",
        "PostPaymentIntentsIntentCapture",
        "PostPaymentIntentsIntentConfirm",
        "GetPaymentMethods",
        "PostPaymentMethods",
        "GetPaymentMethodsPaymentMethod",
        "PostPaymentMethodsPaymentMethod",
        "PostPaymentMethodsPaymentMethodAttach",
        "PostPaymentMethodsPaymentMethodDetach",
        "GetPayouts",
        "PostPayouts",
        "GetPayoutsPayout",
        "PostPayoutsPayout",
        "PostPayoutsPayoutCancel",
        "PostPayoutsPayoutReverse",
        "GetPrices",
        "PostPrices",
        "GetPricesSearch",
        "GetPricesPrice",
        "PostPricesPrice",
        "GetProducts",
        "PostProducts",
        "GetProductsSearch",
        "GetProductsId",
        "PostProductsId",
        "DeleteProductsId",
        "GetPromotionCodes",
        "PostPromotionCodes",
        "GetPromotionCodesPromotionCode",
        "PostPromotionCodesPromotionCode",
        "GetRefunds",
        "PostRefunds",
        "GetRefundsRefund",
        "PostRefundsRefund",
        "PostRefundsRefundCancel",
        "GetSetupIntents",
        "PostSetupIntents",
        "GetSetupIntentsIntent",
        "PostSetupIntentsIntent",
        "PostSetupIntentsIntentCancel",
        "PostSetupIntentsIntentConfirm",
        "PostSetupIntentsIntentVerifyMicrodeposits",
        "GetSubscriptionItems",
        "PostSubscriptionItems",
        "GetSubscriptionItemsItem",
        "PostSubscriptionItemsItem",
        "DeleteSubscriptionItemsItem",
        "GetSubscriptionSchedules",
        "PostSubscriptionSchedules",
        "GetSubscriptionSchedulesSchedule",
        "PostSubscriptionSchedulesSchedule",
        "PostSubscriptionSchedulesScheduleCancel",
        "PostSubscriptionSchedulesScheduleRelease",
        "GetSubscriptions",
        "PostSubscriptions",
        "GetSubscriptionsSearch",
        "GetSubscriptionsSubscriptionExposedId",
        "PostSubscriptionsSubscriptionExposedId",
        "DeleteSubscriptionsSubscriptionExposedId",
        "PostSubscriptionsSubscriptionResume",
        "GetTaxRates",
        "PostTaxRates",
        "GetTaxRatesTaxRate",
        "PostTaxRatesTaxRate",
    ]


def test_account_info_returns_account_object(instance: seahaven.Instance) -> None:
    """The fifth tool: a static account object with billing-relevant fields."""
    result = instance.call("get_stripe_account_info")
    assert result["object"] == "account"
    assert result["id"].startswith("acct_")
    assert result["charges_enabled"] is False
    assert result["payouts_enabled"] is False
    assert result["default_currency"] == "usd"
    assert result["country"] == "US"
    assert result["type"] == "standard"
    assert result["details_submitted"] is False
    assert isinstance(result["business_profile"], dict)
    assert isinstance(result["capabilities"], dict)
    assert isinstance(result["settings"], dict)
    assert "metadata" not in result
    assert isinstance(result["created"], int)


def test_account_info_is_idempotent(instance: seahaven.Instance) -> None:
    """Two calls return identical results -- the account is static."""
    first = instance.call("get_stripe_account_info")
    second = instance.call("get_stripe_account_info")
    assert first == second


def _all_routes():
    from seahaven_stripe_world.dispatch.routes import ALL

    return ALL
