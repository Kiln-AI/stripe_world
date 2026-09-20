"""The routing table: the 148-operation scope of this world. Data, not code.

Every entry's `(method, pattern)` matches the pinned spec3.json verbatim,
placeholder names included, and the spec pipeline validates that on every
regeneration — so a typo'd path or a spec bump that moves the surface fails
generation rather than silently narrowing discovery (components/discovery.md
§1). `alias_of` marks the eight legacy aliases that share a canonical handler
(components/dispatcher.md §3.1.3).

Bootstrapped mechanically by `python -m tools_dev.prune_spec --bootstrap-routes`
and hand-maintained thereafter.

A route is **wired** once it carries `params`; exactly one of `handler`
(hand-written) and `(resource, action)` (engine-served) is then set, and the
router checks that at import. Routes still awaiting their resource phase are
unwired data, and dispatching one is a `WorldBug` naming the `op_id` — an
honest "not built yet" rather than a pretend Stripe answer. The resource
phases wire their own entries; the dispatcher phase wires the five core
customers routes to the throwaway slice (`resources/customers.py`).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from stripeapi.dispatch.params import ParamSpec
    from stripeapi.dispatch.resource import ResourceSpec, Scope
    from stripeapi.dispatch.response import Handler

from stripeapi.resources import (
    charges,
    coupons,
    customers,
    payment_intents,
    payment_methods,
    prices,
    products,
    promotion_codes,
    tax_rates,
)

__all__ = ["ALL", "Route"]

Method = Literal["GET", "POST", "DELETE"]
Action = Literal["list", "create", "retrieve", "update", "delete"]


@dataclass(frozen=True, slots=True)
class Route:
    """One routed operation: the HTTP verb, the path pattern with `{placeholder}`
    segments as they appear in the spec, and the spec's own operationId.

    `handler is None` is the definition of "generated": exactly one of
    `handler` and `(resource, action)` is set on a wired route, and the router
    enforces it at import. An unwired route (`params is None`) carries none of
    the three and waits for its resource phase.
    """

    method: Method
    pattern: str
    op_id: str
    params: ParamSpec | None = None
    resource: ResourceSpec | None = None
    action: Action | None = None
    handler: Handler | None = None
    scope: Scope | None = None
    alias_of: str | None = None  # the op_id this one delegates to
    # What the operation answers, declared so `expand[]` paths can be
    # validated statically, before any row is written (cross_cutting.md
    # §3.3.1). `response_object` is the schema name of the object returned —
    # the item's, for a list — and `envelope` says which shape wraps it.
    response_object: str | None = None
    envelope: Literal["object", "list"] | None = None


ALL: Final[tuple[Route, ...]] = (
    # balance
    Route(method="GET", pattern="/v1/balance", op_id="GetBalance"),
    # balance_transactions
    Route(method="GET", pattern="/v1/balance_transactions", op_id="GetBalanceTransactions"),
    Route(method="GET", pattern="/v1/balance_transactions/{id}", op_id="GetBalanceTransactionsId"),
    # charges — create and capture are the legacy surface's recorded
    # refusals (the modern API will not make a card charge without a
    # PaymentIntent); list/retrieve/update are engine-served
    Route(
        method="GET",
        pattern="/v1/charges",
        op_id="GetCharges",
        response_object="charge",
        envelope="list",
        params=charges.CHARGE_LIST,
        resource=charges.SPEC,
        action="list",
    ),
    Route(
        method="POST",
        pattern="/v1/charges",
        op_id="PostCharges",
        response_object="charge",
        envelope="object",
        params=charges.CHARGE_CREATE,
        handler=charges.create,
    ),
    Route(
        method="GET",
        pattern="/v1/charges/{charge}",
        op_id="GetChargesCharge",
        response_object="charge",
        envelope="object",
        params=charges.CHARGE_RETRIEVE,
        resource=charges.SPEC,
        action="retrieve",
    ),
    Route(
        method="POST",
        pattern="/v1/charges/{charge}",
        op_id="PostChargesCharge",
        response_object="charge",
        envelope="object",
        params=charges.CHARGE_UPDATE,
        resource=charges.SPEC,
        action="update",
    ),
    Route(
        method="POST",
        pattern="/v1/charges/{charge}/capture",
        op_id="PostChargesChargeCapture",
        response_object="charge",
        envelope="object",
        params=charges.CHARGE_CAPTURE,
        handler=charges.capture,
    ),
    Route(method="GET", pattern="/v1/charges/{charge}/dispute", op_id="GetChargesChargeDispute"),
    Route(method="POST", pattern="/v1/charges/{charge}/dispute", op_id="PostChargesChargeDispute"),
    Route(
        method="POST",
        pattern="/v1/charges/{charge}/dispute/close",
        op_id="PostChargesChargeDisputeClose",
    ),
    Route(method="POST", pattern="/v1/charges/{charge}/refund", op_id="PostChargesChargeRefund"),
    Route(method="GET", pattern="/v1/charges/{charge}/refunds", op_id="GetChargesChargeRefunds"),
    Route(method="POST", pattern="/v1/charges/{charge}/refunds", op_id="PostChargesChargeRefunds"),
    Route(
        method="GET",
        pattern="/v1/charges/{charge}/refunds/{refund}",
        op_id="GetChargesChargeRefundsRefund",
    ),
    Route(
        method="POST",
        pattern="/v1/charges/{charge}/refunds/{refund}",
        op_id="PostChargesChargeRefundsRefund",
    ),
    # coupons
    Route(
        method="GET",
        pattern="/v1/coupons",
        op_id="GetCoupons",
        response_object="coupon",
        envelope="list",
        params=coupons.COUPON_LIST,
        resource=coupons.SPEC,
        action="list",
    ),
    Route(
        method="POST",
        pattern="/v1/coupons",
        op_id="PostCoupons",
        response_object="coupon",
        envelope="object",
        params=coupons.COUPON_CREATE,
        resource=coupons.SPEC,
        action="create",
    ),
    Route(
        method="GET",
        pattern="/v1/coupons/{coupon}",
        op_id="GetCouponsCoupon",
        response_object="coupon",
        envelope="object",
        params=coupons.COUPON_RETRIEVE,
        resource=coupons.SPEC,
        action="retrieve",
    ),
    Route(
        method="POST",
        pattern="/v1/coupons/{coupon}",
        op_id="PostCouponsCoupon",
        response_object="coupon",
        envelope="object",
        params=coupons.COUPON_UPDATE,
        resource=coupons.SPEC,
        action="update",
    ),
    Route(
        method="DELETE",
        pattern="/v1/coupons/{coupon}",
        op_id="DeleteCouponsCoupon",
        params=coupons.COUPON_DELETE,
        resource=coupons.SPEC,
        action="delete",
    ),
    # credit_notes
    Route(method="GET", pattern="/v1/credit_notes", op_id="GetCreditNotes"),
    Route(method="POST", pattern="/v1/credit_notes", op_id="PostCreditNotes"),
    Route(method="GET", pattern="/v1/credit_notes/preview", op_id="GetCreditNotesPreview"),
    Route(
        method="GET",
        pattern="/v1/credit_notes/preview/lines",
        op_id="GetCreditNotesPreviewLines",
    ),
    Route(
        method="GET",
        pattern="/v1/credit_notes/{credit_note}/lines",
        op_id="GetCreditNotesCreditNoteLines",
    ),
    Route(method="GET", pattern="/v1/credit_notes/{id}", op_id="GetCreditNotesId"),
    Route(method="POST", pattern="/v1/credit_notes/{id}", op_id="PostCreditNotesId"),
    Route(method="POST", pattern="/v1/credit_notes/{id}/void", op_id="PostCreditNotesIdVoid"),
    # customers — the five core routes are wired to the throwaway slice; the
    # rest wait for the customers-and-payment-methods phase.
    Route(
        method="GET",
        pattern="/v1/customers",
        op_id="GetCustomers",
        response_object="customer",
        envelope="list",
        params=customers.CUSTOMER_LIST,
        resource=customers.SPEC,
        action="list",
    ),
    Route(
        method="POST",
        pattern="/v1/customers",
        op_id="PostCustomers",
        response_object="customer",
        envelope="object",
        params=customers.CUSTOMER_CREATE,
        resource=customers.SPEC,
        action="create",
    ),
    Route(
        method="GET",
        pattern="/v1/customers/{customer}",
        op_id="GetCustomersCustomer",
        response_object="customer",
        envelope="object",
        params=customers.CUSTOMER_RETRIEVE,
        resource=customers.SPEC,
        action="retrieve",
    ),
    Route(
        method="POST",
        pattern="/v1/customers/{customer}",
        op_id="PostCustomersCustomer",
        response_object="customer",
        envelope="object",
        params=customers.CUSTOMER_UPDATE,
        resource=customers.SPEC,
        action="update",
    ),
    Route(
        method="DELETE",
        pattern="/v1/customers/{customer}",
        op_id="DeleteCustomersCustomer",
        params=customers.CUSTOMER_DELETE,
        resource=customers.SPEC,
        action="delete",
    ),
    Route(
        method="GET",
        pattern="/v1/customers/{customer}/balance_transactions",
        op_id="GetCustomersCustomerBalanceTransactions",
    ),
    Route(
        method="POST",
        pattern="/v1/customers/{customer}/balance_transactions",
        op_id="PostCustomersCustomerBalanceTransactions",
    ),
    Route(
        method="GET",
        pattern="/v1/customers/{customer}/balance_transactions/{transaction}",
        op_id="GetCustomersCustomerBalanceTransactionsTransaction",
    ),
    Route(
        method="POST",
        pattern="/v1/customers/{customer}/balance_transactions/{transaction}",
        op_id="PostCustomersCustomerBalanceTransactionsTransaction",
    ),
    Route(
        method="GET",
        pattern="/v1/customers/{customer}/discount",
        op_id="GetCustomersCustomerDiscount",
    ),
    Route(
        method="DELETE",
        pattern="/v1/customers/{customer}/discount",
        op_id="DeleteCustomersCustomerDiscount",
    ),
    Route(
        method="GET",
        pattern="/v1/customers/{customer}/payment_methods",
        op_id="GetCustomersCustomerPaymentMethods",
        response_object="payment_method",
        envelope="list",
        params=payment_methods.CUSTOMER_PAYMENT_METHODS,
        resource=payment_methods.SPEC,
        action="list",
        scope=payment_methods.CUSTOMER_SCOPE,
    ),
    Route(
        method="GET",
        pattern="/v1/customers/{customer}/payment_methods/{payment_method}",
        op_id="GetCustomersCustomerPaymentMethodsPaymentMethod",
        response_object="payment_method",
        envelope="object",
        params=payment_methods.CUSTOMER_PAYMENT_METHOD,
        resource=payment_methods.SPEC,
        action="retrieve",
        scope=payment_methods.CUSTOMER_SCOPE,
    ),
    Route(
        method="GET",
        pattern="/v1/customers/{customer}/subscriptions",
        op_id="GetCustomersCustomerSubscriptions",
    ),
    Route(
        method="POST",
        pattern="/v1/customers/{customer}/subscriptions",
        op_id="PostCustomersCustomerSubscriptions",
    ),
    Route(
        method="GET",
        pattern="/v1/customers/{customer}/subscriptions/{subscription_exposed_id}",
        op_id="GetCustomersCustomerSubscriptionsSubscriptionExposedId",
    ),
    Route(
        method="POST",
        pattern="/v1/customers/{customer}/subscriptions/{subscription_exposed_id}",
        op_id="PostCustomersCustomerSubscriptionsSubscriptionExposedId",
    ),
    Route(
        method="DELETE",
        pattern="/v1/customers/{customer}/subscriptions/{subscription_exposed_id}",
        op_id="DeleteCustomersCustomerSubscriptionsSubscriptionExposedId",
    ),
    Route(
        method="GET",
        pattern="/v1/customers/{customer}/subscriptions/{subscription_exposed_id}/discount",
        op_id="GetCustomersCustomerSubscriptionsSubscriptionExposedIdDiscount",
    ),
    Route(
        method="DELETE",
        pattern="/v1/customers/{customer}/subscriptions/{subscription_exposed_id}/discount",
        op_id="DeleteCustomersCustomerSubscriptionsSubscriptionExposedIdDiscount",
    ),
    # disputes
    Route(method="GET", pattern="/v1/disputes", op_id="GetDisputes"),
    Route(method="GET", pattern="/v1/disputes/{dispute}", op_id="GetDisputesDispute"),
    Route(method="POST", pattern="/v1/disputes/{dispute}", op_id="PostDisputesDispute"),
    Route(method="POST", pattern="/v1/disputes/{dispute}/close", op_id="PostDisputesDisputeClose"),
    # events
    Route(method="GET", pattern="/v1/events", op_id="GetEvents"),
    Route(method="GET", pattern="/v1/events/{id}", op_id="GetEventsId"),
    # invoiceitems
    Route(method="GET", pattern="/v1/invoiceitems", op_id="GetInvoiceitems"),
    Route(method="POST", pattern="/v1/invoiceitems", op_id="PostInvoiceitems"),
    Route(
        method="GET",
        pattern="/v1/invoiceitems/{invoiceitem}",
        op_id="GetInvoiceitemsInvoiceitem",
    ),
    Route(
        method="POST",
        pattern="/v1/invoiceitems/{invoiceitem}",
        op_id="PostInvoiceitemsInvoiceitem",
    ),
    Route(
        method="DELETE",
        pattern="/v1/invoiceitems/{invoiceitem}",
        op_id="DeleteInvoiceitemsInvoiceitem",
    ),
    # invoices
    Route(method="GET", pattern="/v1/invoices", op_id="GetInvoices"),
    Route(method="POST", pattern="/v1/invoices", op_id="PostInvoices"),
    Route(method="POST", pattern="/v1/invoices/create_preview", op_id="PostInvoicesCreatePreview"),
    Route(method="GET", pattern="/v1/invoices/{invoice}", op_id="GetInvoicesInvoice"),
    Route(method="POST", pattern="/v1/invoices/{invoice}", op_id="PostInvoicesInvoice"),
    Route(method="DELETE", pattern="/v1/invoices/{invoice}", op_id="DeleteInvoicesInvoice"),
    Route(
        method="POST",
        pattern="/v1/invoices/{invoice}/add_lines",
        op_id="PostInvoicesInvoiceAddLines",
    ),
    Route(
        method="POST",
        pattern="/v1/invoices/{invoice}/attach_payment",
        op_id="PostInvoicesInvoiceAttachPayment",
    ),
    Route(
        method="POST",
        pattern="/v1/invoices/{invoice}/finalize",
        op_id="PostInvoicesInvoiceFinalize",
    ),
    Route(method="GET", pattern="/v1/invoices/{invoice}/lines", op_id="GetInvoicesInvoiceLines"),
    Route(
        method="POST",
        pattern="/v1/invoices/{invoice}/lines/{line_item_id}",
        op_id="PostInvoicesInvoiceLinesLineItemId",
    ),
    Route(
        method="POST",
        pattern="/v1/invoices/{invoice}/mark_uncollectible",
        op_id="PostInvoicesInvoiceMarkUncollectible",
    ),
    Route(method="POST", pattern="/v1/invoices/{invoice}/pay", op_id="PostInvoicesInvoicePay"),
    Route(
        method="POST",
        pattern="/v1/invoices/{invoice}/remove_lines",
        op_id="PostInvoicesInvoiceRemoveLines",
    ),
    Route(method="POST", pattern="/v1/invoices/{invoice}/send", op_id="PostInvoicesInvoiceSend"),
    Route(
        method="POST",
        pattern="/v1/invoices/{invoice}/update_lines",
        op_id="PostInvoicesInvoiceUpdateLines",
    ),
    Route(method="POST", pattern="/v1/invoices/{invoice}/void", op_id="PostInvoicesInvoiceVoid"),
    # payment_intents — create, confirm, capture and cancel are the state
    # machine (hand-written); list, retrieve and update are engine-served.
    # amount_details_line_items, apply_customer_balance,
    # increment_authorization and verify_microdeposits wait for their phases.
    Route(
        method="GET",
        pattern="/v1/payment_intents",
        op_id="GetPaymentIntents",
        response_object="payment_intent",
        envelope="list",
        params=payment_intents.PI_LIST,
        resource=payment_intents.SPEC,
        action="list",
    ),
    Route(
        method="POST",
        pattern="/v1/payment_intents",
        op_id="PostPaymentIntents",
        response_object="payment_intent",
        envelope="object",
        params=payment_intents.PI_CREATE,
        handler=payment_intents.create,
    ),
    Route(
        method="GET",
        pattern="/v1/payment_intents/{intent}",
        op_id="GetPaymentIntentsIntent",
        response_object="payment_intent",
        envelope="object",
        params=payment_intents.PI_RETRIEVE,
        resource=payment_intents.SPEC,
        action="retrieve",
    ),
    Route(
        method="POST",
        pattern="/v1/payment_intents/{intent}",
        op_id="PostPaymentIntentsIntent",
        response_object="payment_intent",
        envelope="object",
        params=payment_intents.PI_UPDATE,
        resource=payment_intents.SPEC,
        action="update",
    ),
    Route(
        method="GET",
        pattern="/v1/payment_intents/{intent}/amount_details_line_items",
        op_id="GetPaymentIntentsIntentAmountDetailsLineItems",
    ),
    Route(
        method="POST",
        pattern="/v1/payment_intents/{intent}/apply_customer_balance",
        op_id="PostPaymentIntentsIntentApplyCustomerBalance",
    ),
    Route(
        method="POST",
        pattern="/v1/payment_intents/{intent}/cancel",
        op_id="PostPaymentIntentsIntentCancel",
        response_object="payment_intent",
        envelope="object",
        params=payment_intents.PI_CANCEL,
        handler=payment_intents.cancel,
    ),
    Route(
        method="POST",
        pattern="/v1/payment_intents/{intent}/capture",
        op_id="PostPaymentIntentsIntentCapture",
        response_object="payment_intent",
        envelope="object",
        params=payment_intents.PI_CAPTURE,
        handler=payment_intents.capture,
    ),
    Route(
        method="POST",
        pattern="/v1/payment_intents/{intent}/confirm",
        op_id="PostPaymentIntentsIntentConfirm",
        response_object="payment_intent",
        envelope="object",
        params=payment_intents.PI_CONFIRM,
        handler=payment_intents.confirm,
    ),
    Route(
        method="POST",
        pattern="/v1/payment_intents/{intent}/increment_authorization",
        op_id="PostPaymentIntentsIntentIncrementAuthorization",
    ),
    Route(
        method="POST",
        pattern="/v1/payment_intents/{intent}/verify_microdeposits",
        op_id="PostPaymentIntentsIntentVerifyMicrodeposits",
    ),
    # payment_methods
    Route(
        method="GET",
        pattern="/v1/payment_methods",
        op_id="GetPaymentMethods",
        response_object="payment_method",
        envelope="list",
        params=payment_methods.PM_LIST,
        resource=payment_methods.SPEC,
        action="list",
    ),
    Route(
        method="POST",
        pattern="/v1/payment_methods",
        op_id="PostPaymentMethods",
        response_object="payment_method",
        envelope="object",
        params=payment_methods.PM_CREATE,
        resource=payment_methods.SPEC,
        action="create",
    ),
    Route(
        method="GET",
        pattern="/v1/payment_methods/{payment_method}",
        op_id="GetPaymentMethodsPaymentMethod",
        response_object="payment_method",
        envelope="object",
        params=payment_methods.PM_RETRIEVE,
        resource=payment_methods.SPEC,
        action="retrieve",
    ),
    Route(
        method="POST",
        pattern="/v1/payment_methods/{payment_method}",
        op_id="PostPaymentMethodsPaymentMethod",
        response_object="payment_method",
        envelope="object",
        params=payment_methods.PM_UPDATE,
        resource=payment_methods.SPEC,
        action="update",
    ),
    Route(
        method="POST",
        pattern="/v1/payment_methods/{payment_method}/attach",
        op_id="PostPaymentMethodsPaymentMethodAttach",
        response_object="payment_method",
        envelope="object",
        params=payment_methods.PM_ATTACH,
        handler=payment_methods.attach,
    ),
    Route(
        method="POST",
        pattern="/v1/payment_methods/{payment_method}/detach",
        op_id="PostPaymentMethodsPaymentMethodDetach",
        response_object="payment_method",
        envelope="object",
        params=payment_methods.PM_DETACH,
        handler=payment_methods.detach,
    ),
    # payouts
    Route(method="GET", pattern="/v1/payouts", op_id="GetPayouts"),
    Route(method="POST", pattern="/v1/payouts", op_id="PostPayouts"),
    Route(method="GET", pattern="/v1/payouts/{payout}", op_id="GetPayoutsPayout"),
    Route(method="POST", pattern="/v1/payouts/{payout}", op_id="PostPayoutsPayout"),
    Route(method="POST", pattern="/v1/payouts/{payout}/cancel", op_id="PostPayoutsPayoutCancel"),
    Route(method="POST", pattern="/v1/payouts/{payout}/reverse", op_id="PostPayoutsPayoutReverse"),
    # prices — create and update are hand-written (the `product_data` row and
    # the lookup-key transfer are second-row writes the engine contract keeps
    # out of its normalizers)
    Route(
        method="GET",
        pattern="/v1/prices",
        op_id="GetPrices",
        response_object="price",
        envelope="list",
        params=prices.PRICE_LIST,
        resource=prices.SPEC,
        action="list",
    ),
    Route(
        method="POST",
        pattern="/v1/prices",
        op_id="PostPrices",
        response_object="price",
        envelope="object",
        params=prices.PRICE_CREATE,
        handler=prices.create,
    ),
    Route(
        method="GET",
        pattern="/v1/prices/{price}",
        op_id="GetPricesPrice",
        response_object="price",
        envelope="object",
        params=prices.PRICE_RETRIEVE,
        resource=prices.SPEC,
        action="retrieve",
    ),
    Route(
        method="POST",
        pattern="/v1/prices/{price}",
        op_id="PostPricesPrice",
        response_object="price",
        envelope="object",
        params=prices.PRICE_UPDATE,
        resource=prices.SPEC,
        handler=prices.update,
    ),
    # products — create is hand-written (`default_price_data` writes the
    # price row before the product's created-event snapshot)
    Route(
        method="GET",
        pattern="/v1/products",
        op_id="GetProducts",
        response_object="product",
        envelope="list",
        params=products.PRODUCT_LIST,
        resource=products.SPEC,
        action="list",
    ),
    Route(
        method="POST",
        pattern="/v1/products",
        op_id="PostProducts",
        response_object="product",
        envelope="object",
        params=products.PRODUCT_CREATE,
        handler=products.create,
    ),
    Route(
        method="GET",
        pattern="/v1/products/{id}",
        op_id="GetProductsId",
        response_object="product",
        envelope="object",
        params=products.PRODUCT_RETRIEVE,
        resource=products.SPEC,
        action="retrieve",
    ),
    Route(
        method="POST",
        pattern="/v1/products/{id}",
        op_id="PostProductsId",
        response_object="product",
        envelope="object",
        params=products.PRODUCT_UPDATE,
        resource=products.SPEC,
        action="update",
    ),
    Route(
        method="DELETE",
        pattern="/v1/products/{id}",
        op_id="DeleteProductsId",
        params=products.PRODUCT_DELETE,
        resource=products.SPEC,
        action="delete",
    ),
    # promotion_codes
    Route(
        method="GET",
        pattern="/v1/promotion_codes",
        op_id="GetPromotionCodes",
        response_object="promotion_code",
        envelope="list",
        params=promotion_codes.PROMO_LIST,
        resource=promotion_codes.SPEC,
        action="list",
    ),
    Route(
        method="POST",
        pattern="/v1/promotion_codes",
        op_id="PostPromotionCodes",
        response_object="promotion_code",
        envelope="object",
        params=promotion_codes.PROMO_CREATE,
        resource=promotion_codes.SPEC,
        action="create",
    ),
    Route(
        method="GET",
        pattern="/v1/promotion_codes/{promotion_code}",
        op_id="GetPromotionCodesPromotionCode",
        response_object="promotion_code",
        envelope="object",
        params=promotion_codes.PROMO_RETRIEVE,
        resource=promotion_codes.SPEC,
        action="retrieve",
    ),
    Route(
        method="POST",
        pattern="/v1/promotion_codes/{promotion_code}",
        op_id="PostPromotionCodesPromotionCode",
        response_object="promotion_code",
        envelope="object",
        params=promotion_codes.PROMO_UPDATE,
        resource=promotion_codes.SPEC,
        action="update",
    ),
    # refunds
    Route(method="GET", pattern="/v1/refunds", op_id="GetRefunds"),
    Route(method="POST", pattern="/v1/refunds", op_id="PostRefunds"),
    Route(method="GET", pattern="/v1/refunds/{refund}", op_id="GetRefundsRefund"),
    Route(method="POST", pattern="/v1/refunds/{refund}", op_id="PostRefundsRefund"),
    Route(method="POST", pattern="/v1/refunds/{refund}/cancel", op_id="PostRefundsRefundCancel"),
    # setup_intents
    Route(method="GET", pattern="/v1/setup_intents", op_id="GetSetupIntents"),
    Route(method="POST", pattern="/v1/setup_intents", op_id="PostSetupIntents"),
    Route(method="GET", pattern="/v1/setup_intents/{intent}", op_id="GetSetupIntentsIntent"),
    Route(method="POST", pattern="/v1/setup_intents/{intent}", op_id="PostSetupIntentsIntent"),
    Route(
        method="POST",
        pattern="/v1/setup_intents/{intent}/cancel",
        op_id="PostSetupIntentsIntentCancel",
    ),
    Route(
        method="POST",
        pattern="/v1/setup_intents/{intent}/confirm",
        op_id="PostSetupIntentsIntentConfirm",
    ),
    Route(
        method="POST",
        pattern="/v1/setup_intents/{intent}/verify_microdeposits",
        op_id="PostSetupIntentsIntentVerifyMicrodeposits",
    ),
    # subscription_items
    Route(method="GET", pattern="/v1/subscription_items", op_id="GetSubscriptionItems"),
    Route(method="POST", pattern="/v1/subscription_items", op_id="PostSubscriptionItems"),
    Route(method="GET", pattern="/v1/subscription_items/{item}", op_id="GetSubscriptionItemsItem"),
    Route(
        method="POST",
        pattern="/v1/subscription_items/{item}",
        op_id="PostSubscriptionItemsItem",
    ),
    Route(
        method="DELETE",
        pattern="/v1/subscription_items/{item}",
        op_id="DeleteSubscriptionItemsItem",
    ),
    # subscription_schedules
    Route(method="GET", pattern="/v1/subscription_schedules", op_id="GetSubscriptionSchedules"),
    Route(method="POST", pattern="/v1/subscription_schedules", op_id="PostSubscriptionSchedules"),
    Route(
        method="GET",
        pattern="/v1/subscription_schedules/{schedule}",
        op_id="GetSubscriptionSchedulesSchedule",
    ),
    Route(
        method="POST",
        pattern="/v1/subscription_schedules/{schedule}",
        op_id="PostSubscriptionSchedulesSchedule",
    ),
    Route(
        method="POST",
        pattern="/v1/subscription_schedules/{schedule}/cancel",
        op_id="PostSubscriptionSchedulesScheduleCancel",
    ),
    Route(
        method="POST",
        pattern="/v1/subscription_schedules/{schedule}/release",
        op_id="PostSubscriptionSchedulesScheduleRelease",
    ),
    # subscriptions
    Route(method="GET", pattern="/v1/subscriptions", op_id="GetSubscriptions"),
    Route(method="POST", pattern="/v1/subscriptions", op_id="PostSubscriptions"),
    Route(
        method="GET",
        pattern="/v1/subscriptions/{subscription_exposed_id}",
        op_id="GetSubscriptionsSubscriptionExposedId",
    ),
    Route(
        method="POST",
        pattern="/v1/subscriptions/{subscription_exposed_id}",
        op_id="PostSubscriptionsSubscriptionExposedId",
    ),
    Route(
        method="DELETE",
        pattern="/v1/subscriptions/{subscription_exposed_id}",
        op_id="DeleteSubscriptionsSubscriptionExposedId",
    ),
    Route(
        method="DELETE",
        pattern="/v1/subscriptions/{subscription_exposed_id}/discount",
        op_id="DeleteSubscriptionsSubscriptionExposedIdDiscount",
    ),
    Route(
        method="POST",
        pattern="/v1/subscriptions/{subscription}/migrate",
        op_id="PostSubscriptionsSubscriptionMigrate",
    ),
    Route(
        method="POST",
        pattern="/v1/subscriptions/{subscription}/resume",
        op_id="PostSubscriptionsSubscriptionResume",
    ),
    # tax_rates
    Route(
        method="GET",
        pattern="/v1/tax_rates",
        op_id="GetTaxRates",
        response_object="tax_rate",
        envelope="list",
        params=tax_rates.TAX_RATE_LIST,
        resource=tax_rates.SPEC,
        action="list",
    ),
    Route(
        method="POST",
        pattern="/v1/tax_rates",
        op_id="PostTaxRates",
        response_object="tax_rate",
        envelope="object",
        params=tax_rates.TAX_RATE_CREATE,
        resource=tax_rates.SPEC,
        action="create",
    ),
    Route(
        method="GET",
        pattern="/v1/tax_rates/{tax_rate}",
        op_id="GetTaxRatesTaxRate",
        response_object="tax_rate",
        envelope="object",
        params=tax_rates.TAX_RATE_RETRIEVE,
        resource=tax_rates.SPEC,
        action="retrieve",
    ),
    Route(
        method="POST",
        pattern="/v1/tax_rates/{tax_rate}",
        op_id="PostTaxRatesTaxRate",
        response_object="tax_rate",
        envelope="object",
        params=tax_rates.TAX_RATE_UPDATE,
        resource=tax_rates.SPEC,
        action="update",
    ),
)
