"""The routing table: the 148-operation scope of this world. Data, not code.

Every entry's `(method, pattern)` matches the pinned spec3.json verbatim,
placeholder names included, and the spec pipeline validates that on every
regeneration — so a typo'd path or a spec bump that moves the surface fails
generation rather than silently narrowing discovery (components/discovery.md
§1). Handlers and ParamSpecs attach to these entries in the dispatcher phase;
`alias_of` will mark the eight legacy aliases that share a canonical handler
(components/dispatcher.md §3.1.3).

Bootstrapped mechanically by `python -m tools_dev.prune_spec --bootstrap-routes`
and hand-maintained thereafter.
"""

from dataclasses import dataclass
from typing import Final

__all__ = ["ALL", "Route"]


@dataclass(frozen=True, slots=True)
class Route:
    """One routed operation: the HTTP verb, the path pattern with `{placeholder}`
    segments as they appear in the spec, and the spec's own operationId."""

    method: str
    pattern: str
    op_id: str


ALL: Final[tuple[Route, ...]] = (
    # balance
    Route(method="GET", pattern="/v1/balance", op_id="GetBalance"),
    # balance_transactions
    Route(method="GET", pattern="/v1/balance_transactions", op_id="GetBalanceTransactions"),
    Route(method="GET", pattern="/v1/balance_transactions/{id}", op_id="GetBalanceTransactionsId"),
    # charges
    Route(method="GET", pattern="/v1/charges", op_id="GetCharges"),
    Route(method="POST", pattern="/v1/charges", op_id="PostCharges"),
    Route(method="GET", pattern="/v1/charges/{charge}", op_id="GetChargesCharge"),
    Route(method="POST", pattern="/v1/charges/{charge}", op_id="PostChargesCharge"),
    Route(method="POST", pattern="/v1/charges/{charge}/capture", op_id="PostChargesChargeCapture"),
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
    Route(method="GET", pattern="/v1/coupons", op_id="GetCoupons"),
    Route(method="POST", pattern="/v1/coupons", op_id="PostCoupons"),
    Route(method="GET", pattern="/v1/coupons/{coupon}", op_id="GetCouponsCoupon"),
    Route(method="POST", pattern="/v1/coupons/{coupon}", op_id="PostCouponsCoupon"),
    Route(method="DELETE", pattern="/v1/coupons/{coupon}", op_id="DeleteCouponsCoupon"),
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
    # customers
    Route(method="GET", pattern="/v1/customers", op_id="GetCustomers"),
    Route(method="POST", pattern="/v1/customers", op_id="PostCustomers"),
    Route(method="GET", pattern="/v1/customers/{customer}", op_id="GetCustomersCustomer"),
    Route(method="POST", pattern="/v1/customers/{customer}", op_id="PostCustomersCustomer"),
    Route(method="DELETE", pattern="/v1/customers/{customer}", op_id="DeleteCustomersCustomer"),
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
    ),
    Route(
        method="GET",
        pattern="/v1/customers/{customer}/payment_methods/{payment_method}",
        op_id="GetCustomersCustomerPaymentMethodsPaymentMethod",
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
    # payment_intents
    Route(method="GET", pattern="/v1/payment_intents", op_id="GetPaymentIntents"),
    Route(method="POST", pattern="/v1/payment_intents", op_id="PostPaymentIntents"),
    Route(method="GET", pattern="/v1/payment_intents/{intent}", op_id="GetPaymentIntentsIntent"),
    Route(method="POST", pattern="/v1/payment_intents/{intent}", op_id="PostPaymentIntentsIntent"),
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
    ),
    Route(
        method="POST",
        pattern="/v1/payment_intents/{intent}/capture",
        op_id="PostPaymentIntentsIntentCapture",
    ),
    Route(
        method="POST",
        pattern="/v1/payment_intents/{intent}/confirm",
        op_id="PostPaymentIntentsIntentConfirm",
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
    Route(method="GET", pattern="/v1/payment_methods", op_id="GetPaymentMethods"),
    Route(method="POST", pattern="/v1/payment_methods", op_id="PostPaymentMethods"),
    Route(
        method="GET",
        pattern="/v1/payment_methods/{payment_method}",
        op_id="GetPaymentMethodsPaymentMethod",
    ),
    Route(
        method="POST",
        pattern="/v1/payment_methods/{payment_method}",
        op_id="PostPaymentMethodsPaymentMethod",
    ),
    Route(
        method="POST",
        pattern="/v1/payment_methods/{payment_method}/attach",
        op_id="PostPaymentMethodsPaymentMethodAttach",
    ),
    Route(
        method="POST",
        pattern="/v1/payment_methods/{payment_method}/detach",
        op_id="PostPaymentMethodsPaymentMethodDetach",
    ),
    # payouts
    Route(method="GET", pattern="/v1/payouts", op_id="GetPayouts"),
    Route(method="POST", pattern="/v1/payouts", op_id="PostPayouts"),
    Route(method="GET", pattern="/v1/payouts/{payout}", op_id="GetPayoutsPayout"),
    Route(method="POST", pattern="/v1/payouts/{payout}", op_id="PostPayoutsPayout"),
    Route(method="POST", pattern="/v1/payouts/{payout}/cancel", op_id="PostPayoutsPayoutCancel"),
    Route(method="POST", pattern="/v1/payouts/{payout}/reverse", op_id="PostPayoutsPayoutReverse"),
    # prices
    Route(method="GET", pattern="/v1/prices", op_id="GetPrices"),
    Route(method="POST", pattern="/v1/prices", op_id="PostPrices"),
    Route(method="GET", pattern="/v1/prices/{price}", op_id="GetPricesPrice"),
    Route(method="POST", pattern="/v1/prices/{price}", op_id="PostPricesPrice"),
    # products
    Route(method="GET", pattern="/v1/products", op_id="GetProducts"),
    Route(method="POST", pattern="/v1/products", op_id="PostProducts"),
    Route(method="GET", pattern="/v1/products/{id}", op_id="GetProductsId"),
    Route(method="POST", pattern="/v1/products/{id}", op_id="PostProductsId"),
    Route(method="DELETE", pattern="/v1/products/{id}", op_id="DeleteProductsId"),
    # promotion_codes
    Route(method="GET", pattern="/v1/promotion_codes", op_id="GetPromotionCodes"),
    Route(method="POST", pattern="/v1/promotion_codes", op_id="PostPromotionCodes"),
    Route(
        method="GET",
        pattern="/v1/promotion_codes/{promotion_code}",
        op_id="GetPromotionCodesPromotionCode",
    ),
    Route(
        method="POST",
        pattern="/v1/promotion_codes/{promotion_code}",
        op_id="PostPromotionCodesPromotionCode",
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
    Route(method="GET", pattern="/v1/tax_rates", op_id="GetTaxRates"),
    Route(method="POST", pattern="/v1/tax_rates", op_id="PostTaxRates"),
    Route(method="GET", pattern="/v1/tax_rates/{tax_rate}", op_id="GetTaxRatesTaxRate"),
    Route(method="POST", pattern="/v1/tax_rates/{tax_rate}", op_id="PostTaxRatesTaxRate"),
)
