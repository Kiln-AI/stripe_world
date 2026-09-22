"""The allow-list: every difference between a recorded real-API response and
this world's replay that is *permitted*, each with its reason.

This file is the precise statement of how faithful this world is, and it is
reviewed as a design document (architecture.md §10). Anything undeclared
fails a replay: the fix is either the handler or a reviewed entry here —
never a silent pass.

Two sections, deliberately different shapes:

- `ALLOWED_DIFFERENCES` — per-path entries the tree diff consults. A path
  may differ arbitrarily (`predicate is None`) or only when the predicate
  holds. An entry also covers the path's presence on one side only: a key
  this world omits where real Stripe emits it is a difference *at* that path,
  not an untouchable structural fact.
- `STRUCTURAL_DIFFERENCES` — differences that never produce an observable
  field diff within one replay and so have no path to hang an entry on.
  Prose, reviewed the same way.

Path grammar (`components/conformance.md` "The allow-list"): patterns split
on `.`, list indices are ordinary segments (`body.data[3].id`), and in each
pattern segment `*` is the only wildcard — `*` alone is one segment, `**`
alone is any depth, a segment like `*_at` matches by suffix, and a segment
like `data[3]` matches only itself (brackets are literal, not a character
class).
"""

import re
from collections.abc import Callable
from typing import Any

__all__ = [
    "ALLOWED_DIFFERENCES",
    "STRUCTURAL_DIFFERENCES",
    "AllowedDifference",
    "allowed",
    "match_path",
]

# --- Per-path entries ------------------------------------------------------------


class AllowedDifference:
    """One permitted difference. `path` is a pattern (see the module
    docstring); `reason` is the reviewable content; `scenario` scopes the
    entry to one scenario when the difference is not global; `predicate`
    narrows an arbitrary difference to a checked one."""

    __slots__ = ("path", "predicate", "reason", "scenario")

    def __init__(
        self,
        path: str,
        reason: str,
        scenario: str | None = None,
        predicate: Callable[[Any, Any], bool] | None = None,
    ) -> None:
        self.path = path
        self.reason = reason
        self.scenario = scenario
        self.predicate = predicate

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        scope = "every scenario" if self.scenario is None else self.scenario
        return f"AllowedDifference({self.path!r}, {scope})"


def _both_false(recorded: Any, replayed: Any) -> bool:
    return recorded is False and replayed is False


def _both_cus_ids(recorded: Any, replayed: Any) -> bool:
    """A `customer`-shaped reference field: both sides are freshly minted ids
    of the same shape, never equal — the `**.id` rule on the field it lands
    on."""
    return (
        isinstance(recorded, str)
        and isinstance(replayed, str)
        and recorded.startswith("cus_")
        and replayed.startswith("cus_")
    )


_ID_IN_URL = re.compile(
    r"\b(?:cus|pm|ch|pi|in|sub|si|ii|cn|re|dp|seti|txn|po|cbtxn|evt|price|prod|promo|txr|sub_sched)_[A-Za-z0-9]+\b"
)


def _same_url_modulo_ids(recorded: Any, replayed: Any) -> bool:
    """A list envelope's `url` embeds the caller's path ids; strip every
    id-shaped token and what remains must be identical."""
    if not isinstance(recorded, str) or not isinstance(replayed, str):
        return False
    return _ID_IN_URL.sub("<id>", recorded) == _ID_IN_URL.sub("<id>", replayed)


#: The one message form whose entire variable content is the id it names.
#: Predicate-narrowed so every other error message stays byte-exact: only
#: `No such customer: '<cus_id>'` on both sides passes. Real Stripe ids vary
#: in length (14 here against this world's minted 24), so the form — not the
#: length — is the test.
_NO_SUCH_CUSTOMER = re.compile(r"^No such customer: 'cus_[A-Za-z0-9]+'$")


def _no_such_customer_modulo_id(recorded: Any, replayed: Any) -> bool:
    if not isinstance(recorded, str) or not isinstance(replayed, str):
        return False
    return bool(_NO_SUCH_CUSTOMER.fullmatch(recorded) and _NO_SUCH_CUSTOMER.fullmatch(replayed))


#: The unrecognized-URL message: the real body continues with Stripe's
#: docs/support pointers after the path; this world emits the quoted form
#: alone. Both sides' variable content is the `<METHOD>: <path>` pair, which
#: embeds freshly minted ids when the mismatch step names a real object.
_UNRECOGNIZED_LONG = re.compile(
    r"^Unrecognized request URL \((?:GET|POST|DELETE): .*\)\. "
    r"Please see https://stripe\.com/docs or we can help at https://support\.stripe\.com/\.$"
)
_UNRECOGNIZED_SHORT = re.compile(r"^Unrecognized request URL \((?:GET|POST|DELETE): .*\)\.$")


def _unrecognized_url_modulo_suffix(recorded: Any, replayed: Any) -> bool:
    if not isinstance(recorded, str) or not isinstance(replayed, str):
        return False
    return bool(_UNRECOGNIZED_LONG.fullmatch(recorded) and _UNRECOGNIZED_SHORT.fullmatch(replayed))


#: The prices lookup-key conflict names the holding price's id — its whole
#: variable content (probed, Phase 7). Same narrowing idea as the customer
#: form above.
_PRICE_LOOKUP_CONFLICT = re.compile(
    r"^A price \(`price_[A-Za-z0-9]+`\) already uses that lookup key\.$"
)


def _price_lookup_conflict_modulo_id(recorded: Any, replayed: Any) -> bool:
    if not isinstance(recorded, str) or not isinstance(replayed, str):
        return False
    return bool(
        _PRICE_LOOKUP_CONFLICT.fullmatch(recorded) and _PRICE_LOOKUP_CONFLICT.fullmatch(replayed)
    )


#: Any other `No such <object>: '<id>'` message — the same id-only rule for
#: the resources whose messages were recorded later (products, coupons,
#: promotion codes, tax rates; Phase 7). Form-only comparison, so a message
#: that differs in anything but the id still fails.
_NO_SUCH_OBJECT = re.compile(r"^No such [a-zA-Z][a-zA-Z ]+: '[A-Za-z0-9_.\-']+'$")

#: The two recorded proration description forms (cassette 01): the day is
#: the only per-run variable — "Unused time on 3 x <name> after 21 Oct 2026",
#: "Remaining time on <name> after 11 Oct 2026". The product name is a
#: caller-chosen literal on both sides, so the forms are matched whole and
#: only the date-bearing suffix is run-dependent.
_PRORATION_DESCRIPTION = re.compile(
    r"^(?:Unused|Remaining) time on (?:\d+ x )?.+ after \d{2} [A-Z][a-z]{2} \d{4}$"
)


def _no_such_object_modulo_id(recorded: Any, replayed: Any) -> bool:
    if not isinstance(recorded, str) or not isinstance(replayed, str):
        return False
    return bool(_NO_SUCH_OBJECT.fullmatch(recorded) and _NO_SUCH_OBJECT.fullmatch(replayed))


def _both_prefixed_ids(*prefixes: str) -> Callable[[Any, Any], bool]:
    """A reference-valued field predicate: both sides are freshly minted ids
    of the given shape, never equal — the `**.id` rule on the field name a
    reference lands on."""

    def check(recorded: Any, replayed: Any) -> bool:
        return (
            isinstance(recorded, str)
            and isinstance(replayed, str)
            and recorded.startswith(prefixes)
            and replayed.startswith(prefixes)
        )

    return check


def _ledger_reference(recorded: Any, replayed: Any) -> bool:
    """A ledger-reference field (`balance_transaction`, the bt a charge,
    refund or payout carries). Three shapes pass, in this order of intent:

    1. Both sides freshly minted `txn_` ids — the ordinary id rule.
    2. Recorded null, replayed id — the async settlement window: live Stripe
       mints the charge's ledger row moments after the capture (recorded:
       null on the confirm-time body, populated seconds later, cassette 04
       steps 10 vs 49); this world's ledger is synchronous, so the row
       exists inside the call that earned it.
    3. Recorded id-or-null, replayed **absent** — the dispute's singular
       `balance_transaction`, which the pinned spec does not declare on
       `dispute` at all, so this world omits the key (the spec-is-authority
       ruling every other live-only field follows). The diff walker hands
       key-absence to the predicate as a `None` replayed value, which is
       indistinguishable here from a recorded null.

    Arm 3's `None` overlap means the predicate cannot by itself tell a
    deliberately omitted field from a charge whose ledger write regressed to
    a null; that regression risk is pinned by the unit suites
    (`test_the_charge_bt`, `test_the_refund_bt`) and the ledger invariants
    instead — the compensation is stated here because this file is reviewed
    as a design document."""
    if isinstance(recorded, str) and isinstance(replayed, str):
        return recorded.startswith("txn_") and replayed.startswith("txn_")
    if replayed is None:
        return recorded is None or (isinstance(recorded, str) and recorded.startswith("txn_"))
    return recorded is None and isinstance(replayed, str) and replayed.startswith("txn_")


#: A minted promotion-code `code`: eight uppercase alphanumerics per side
#: (recorded `BFDACGQS`; ours draws the same shape from the seeded stream).
_MINTED_CODE = re.compile(r"^[A-Z0-9]{8}$")


def _both_minted_codes(recorded: Any, replayed: Any) -> bool:
    return (
        isinstance(recorded, str)
        and isinstance(replayed, str)
        and bool(_MINTED_CODE.fullmatch(recorded))
        and bool(_MINTED_CODE.fullmatch(replayed))
    )


#: A minted coupon id: eight mixed-case alphanumerics per side (recorded
#: `hbzb1NEf`).
_MINTED_COUPON = re.compile(r"^[A-Za-z0-9]{8}$")


def _both_minted_coupon_ids(recorded: Any, replayed: Any) -> bool:
    return (
        isinstance(recorded, str)
        and isinstance(replayed, str)
        and bool(_MINTED_COUPON.fullmatch(recorded))
        and bool(_MINTED_COUPON.fullmatch(replayed))
    )


#: The decline envelope's message-with-key: the idempotency-mismatch text
#: names the caller's key, which differs per recording run by design (see
#: s04's module docstring). Form-only comparison on both sides.
_IDEMPOTENCY_MISMATCH = re.compile(
    r"^Keys for idempotent requests can only be used with the same parameters "
    r"they were first used with\. Try using a key other than 's04-idem-[0-9a-f]+' "
    r"if you meant to execute a different request\.$"
)


def _idempotency_mismatch_modulo_key(recorded: Any, replayed: Any) -> bool:
    if not isinstance(recorded, str) or not isinstance(replayed, str):
        return False
    return bool(
        _IDEMPOTENCY_MISMATCH.fullmatch(recorded) and _IDEMPOTENCY_MISMATCH.fullmatch(replayed)
    )


#: A client secret embeds its intent's minted id plus a random suffix —
#: PaymentIntent and SetupIntent alike.
_CLIENT_SECRET = re.compile(r"^(?:pi|seti)_[A-Za-z0-9]+_secret_[A-Za-z0-9]+$")


def _both_client_secrets(recorded: Any, replayed: Any) -> bool:
    return (
        isinstance(recorded, str)
        and isinstance(replayed, str)
        and bool(_CLIENT_SECRET.fullmatch(recorded) and _CLIENT_SECRET.fullmatch(replayed))
    )


def _recorded_placeholder_or_same(recorded: Any, replayed: Any) -> bool:
    """The recorded value was normalized to a redaction placeholder, is the
    same, or is null where this world derived one — the last pair is the
    async-finalization window (a recorded draft whose replayed counterpart
    has already finalized, the cancel collapse's draft among them)."""
    return recorded in (
        "<redacted:receipt_url>",
        "<redacted:invoice_pdf>",
        "<redacted:hosted_invoice_url>",
        replayed,
        None,
    )


#: The confirm-missing-method message names the customer id — the same
#: id-only rule (probed Phase 8).
_CONFIRM_MISSING = re.compile(
    r"^You cannot confirm this PaymentIntent because it's missing a payment method\. "
    r"To confirm the PaymentIntent with cus_[A-Za-z0-9]+, specify a payment method "
    r"attached to this customer along with the customer ID\.$"
)


def _confirm_missing_modulo_customer(recorded: Any, replayed: Any) -> bool:
    if not isinstance(recorded, str) or not isinstance(replayed, str):
        return False
    return bool(_CONFIRM_MISSING.fullmatch(recorded) and _CONFIRM_MISSING.fullmatch(replayed))


#: The two charge-capture refusal messages name the object id — the id rule
#: where the id is the message's whole variable content (both probed, Phase 8).
_CHARGE_CAPTURE_REFUSED = re.compile(
    r"^(?:Charge ch_[A-Za-z0-9]+ has already been captured\."
    r"|This uncaptured Charge was created by a PaymentIntent \(pi_[A-Za-z0-9]+\)\. "
    r"You must capture the PaymentIntent instead\. For more information, see "
    r"https://stripe\.com/docs/payments/place-a-hold-on-a-payment-method)$"
)


def _charge_capture_message_modulo_id(recorded: Any, replayed: Any) -> bool:
    if not isinstance(recorded, str) or not isinstance(replayed, str):
        return False
    return bool(
        _CHARGE_CAPTURE_REFUSED.fullmatch(recorded) and _CHARGE_CAPTURE_REFUSED.fullmatch(replayed)
    )


#: The ownership refusal a customerless intent's confirm with a customer's
#: method answers (probed CR round 1): both ids are per-run mints.
_PM_BELONGS_TO = re.compile(
    r"^The `payment_method` parameter supplied pm_[A-Za-z0-9]+ belongs to the "
    r"Customer cus_[A-Za-z0-9]+\. Please include the Customer in the `customer` "
    r"parameter on the PaymentIntent\.$"
)


def _pm_belongs_to_modulo_ids(recorded: Any, replayed: Any) -> bool:
    if not isinstance(recorded, str) or not isinstance(replayed, str):
        return False
    return bool(_PM_BELONGS_TO.fullmatch(recorded) and _PM_BELONGS_TO.fullmatch(replayed))


#: The wrong-customer confirm refusal (recorded, cassette 04, CR round 2):
#: same id-only rule, its sibling spelling.
_PM_NOT_BELONG = re.compile(
    r"^The PaymentMethod pm_[A-Za-z0-9]+ does not belong to the Customer you "
    r"supplied cus_[A-Za-z0-9]+\. Please use this PaymentMethod with the "
    r"Customer that it belongs to instead\.$"
)


def _pm_not_belong_modulo_ids(recorded: Any, replayed: Any) -> bool:
    if not isinstance(recorded, str) or not isinstance(replayed, str):
        return False
    return bool(_PM_NOT_BELONG.fullmatch(recorded) and _PM_NOT_BELONG.fullmatch(replayed))


#: The recorded transfer_group refusal on a PI-created charge (cassette 04,
#: CR round 2) versus this world's scope-cut answer — real Stripe names the
#: PaymentIntent and directs the update there; this world has cut the
#: parameter (data_model §7's constant-null ruling) and answers
#: `parameter_unknown`. Four entries, one per envelope field that differs.
_TRANSFER_GROUP_RECORDED = re.compile(
    r"^This Charge was created by a PaymentIntent \(pi_[A-Za-z0-9]+\)\. You must "
    r"update the `transfer_group` on the PaymentIntent instead of updating the "
    r"Charge directly\. See https://stripe\.com/docs/api#update_payment_intent$"
)


def _transfer_group_message(recorded: Any, replayed: Any) -> bool:
    return (
        isinstance(recorded, str)
        and isinstance(replayed, str)
        and (
            bool(_TRANSFER_GROUP_RECORDED.fullmatch(recorded))
            and replayed == "Received unknown parameter: transfer_group"
        )
    )


def _transfer_group_code(recorded: Any, replayed: Any) -> bool:
    return recorded is None and replayed == "parameter_unknown"


def _transfer_group_param(recorded: Any, replayed: Any) -> bool:
    return recorded is None and replayed == "transfer_group"


def _transfer_group_doc_url(recorded: Any, replayed: Any) -> bool:
    return recorded is None and isinstance(replayed, str)


#: Card-issuer geography of the test cards varies by recording account region
#: (probed Phase 8: the decline and 3DS tokens answered `IE` where the table's
#: US issuers answered `US`); a two-letter country either way is the form.
_COUNTRY = re.compile(r"^[A-Z]{2}$")


def _both_country_codes(recorded: Any, replayed: Any) -> bool:
    return (
        isinstance(recorded, str)
        and isinstance(replayed, str)
        and bool(_COUNTRY.fullmatch(recorded) and _COUNTRY.fullmatch(replayed))
    )


def _both_pm_ids(recorded: Any, replayed: Any) -> bool:
    return (
        isinstance(recorded, str)
        and isinstance(replayed, str)
        and recorded.startswith("pm_")
        and replayed.startswith("pm_")
    )


#: The refunds-and-disputes messages whose whole variable content is an
#: object id (all probed verbatim, Phase 9): the fully-refunded refusal, the
#: charged-back refusal, the no-dispute scoped read, and the uncaptured-hold
#: refund refusal.
_CHARGE_ALREADY_REFUNDED = re.compile(r"^Charge ch_[A-Za-z0-9]+ has already been refunded\.$")
_CHARGE_CHARGED_BACK = re.compile(
    r"^Charge ch_[A-Za-z0-9]+ has been charged back; cannot issue a refund\.$"
)
_NO_DISPUTE_FOR_CHARGE = re.compile(r"^No dispute for charge: ch_[A-Za-z0-9]+$")
_UNCAPTURED_REFUND_REFUSED = re.compile(
    r"^This uncaptured Charge was created by a PaymentIntent \(pi_[A-Za-z0-9]+\)\. "
    r"You must cancel the PaymentIntent to reverse the authorization instead of "
    r"refunding the Charge directly\. For more information, see "
    r"https://stripe\.com/docs/payments/place-a-hold-on-a-payment-method$"
)


def _message_modulo_id(pattern: re.Pattern[str]) -> Callable[[Any, Any], bool]:
    def check(recorded: Any, replayed: Any) -> bool:
        return (
            isinstance(recorded, str)
            and isinstance(replayed, str)
            and bool(pattern.fullmatch(recorded))
            and bool(pattern.fullmatch(replayed))
        )

    return check


#: The setup-intent ownership refusals name both ids they turn on (all
#: recorded verbatim, Phase 10, at create, update and confirm): the
#: wrong-customer spelling and the customerless one.
_SETI_DOES_NOT_BELONG = re.compile(
    r"^The PaymentMethod pm_[A-Za-z0-9]+ does not belong to the Customer you "
    r"supplied cus_[A-Za-z0-9]+\. Please use this PaymentMethod with the "
    r"Customer that it belongs to instead\.$"
)
_SETI_SUPPLIED_BELONGS = re.compile(
    r"^The payment method supplied \(pm_[A-Za-z0-9]+\) belongs to the "
    r"Customer cus_[A-Za-z0-9]+\. Please include the Customer in the `customer` "
    r"parameter on the SetupIntent\.$"
)


def _seti_ownership_modulo_ids(recorded: Any, replayed: Any) -> bool:
    return (
        isinstance(recorded, str)
        and isinstance(replayed, str)
        and (
            bool(
                _SETI_DOES_NOT_BELONG.fullmatch(recorded)
                and _SETI_DOES_NOT_BELONG.fullmatch(replayed)
            )
            or bool(
                _SETI_SUPPLIED_BELONGS.fullmatch(recorded)
                and _SETI_SUPPLIED_BELONGS.fullmatch(replayed)
            )
        )
    )


#: A ledger-source reference: both sides freshly minted ids of the same
#: prefix family (`ch_`, `re_`, `du_`, `po_`) — the `**.id` rule on the
#: polymorphic `balance_transaction.source` (Phase 11).
_LEDGER_SOURCE_PREFIXES = ("ch_", "re_", "du_", "po_")


def _both_ledger_sources(recorded: Any, replayed: Any) -> bool:
    return (
        isinstance(recorded, str)
        and isinstance(replayed, str)
        and any(recorded.startswith(p) and replayed.startswith(p) for p in _LEDGER_SOURCE_PREFIXES)
    )


#: The dispute ledger rows' descriptions name the charge they belong to — the
#: id-only rule in its third message-shaped disguise (recorded, cassette 05
#: and 11): `Chargeback withdrawal for ch_…` / `Chargeback reversal for ch_…`.
_DISPUTE_BT_DESCRIPTION = re.compile(r"^Chargeback (?:withdrawal|reversal) for ch_[A-Za-z0-9]+$")


def _dispute_bt_description_modulo_id(recorded: Any, replayed: Any) -> bool:
    return (
        isinstance(recorded, str)
        and isinstance(replayed, str)
        and bool(
            _DISPUTE_BT_DESCRIPTION.fullmatch(recorded)
            and _DISPUTE_BT_DESCRIPTION.fullmatch(replayed)
        )
    )


#: The settlement-status pair a balance transaction's own `status` may differ
#: by: the recorded account marks dispute withdrawals available immediately,
#: this world's T+2 rule holds them pending under a frozen clock (Phase 11).
#: No other object's `status` can record `available`, so the predicate cannot
#: mask a charge, refund, payout or dispute status regression.
def _bt_settlement_status(recorded: Any, replayed: Any) -> bool:
    return recorded == "available" and replayed == "pending"


#: The duplicate-price refusal: its only variable content is the price id
#: (probed, Phase 12); form-only comparison.
_DUP_PRICE = re.compile(
    r"^Cannot create a Subscription with multiple Subscription Items with the "
    r"same Price: price_[A-Za-z0-9]+$"
)


def _dup_price_modulo_id(recorded: Any, replayed: Any) -> bool:
    if not isinstance(recorded, str) or not isinstance(replayed, str):
        return False
    return bool(_DUP_PRICE.fullmatch(recorded) and _DUP_PRICE.fullmatch(replayed))


#: An invoice number: `<8-char prefix>-<4-digit sequence>` per side (the
#: prefix is the customer's own minted `invoice_prefix`, Phase 13).
_INVOICE_NUMBER = re.compile(r"^[A-Z0-9]{8}-\d{4}$")


def _invoice_number_pair(recorded: Any, replayed: Any) -> bool:
    # Recorded null against a replayed number is the cancel collapse's
    # un-numbered draft (below); everything else must be number-shaped both
    # sides so a real numbering regression still fails.
    if recorded is None:
        return isinstance(replayed, str)
    return (
        isinstance(recorded, str)
        and isinstance(replayed, str)
        and bool(_INVOICE_NUMBER.fullmatch(recorded))
        and bool(_INVOICE_NUMBER.fullmatch(replayed))
    )


#: The Invoice Item 404: `No such Invoice Item: 'ii_…'(livemode=false)` —
#: the id-only rule in Stripe's own odd spelling (recorded, cassette 13).
_NO_SUCH_INVOICE_ITEM = re.compile(r"^No such Invoice Item: 'ii_[A-Za-z0-9]+'\(livemode=false\)$")


def _no_such_invoice_item_modulo_id(recorded: Any, replayed: Any) -> bool:
    if not isinstance(recorded, str) or not isinstance(replayed, str):
        return False
    return bool(
        _NO_SUCH_INVOICE_ITEM.fullmatch(recorded) and _NO_SUCH_INVOICE_ITEM.fullmatch(replayed)
    )


#: The dispute settle collapse (Phase 9): live test mode resolves the magic
#: evidence strings asynchronously, answering `under_review`-shaped statuses
#: and flipping seconds later; this world's frozen clock settles inside the
#: submitting call, so the submit response is exactly one hop ahead.
_SETTLE_COLLAPSED = re.compile(r"^(?:under_review|warning_under_review)$")
_SETTLE_LANDED = re.compile(r"^(?:won|lost|needs_response)$")


def _settle_status(recorded: Any, replayed: Any) -> bool:
    return (
        isinstance(recorded, str)
        and isinstance(replayed, str)
        and bool(_SETTLE_COLLAPSED.fullmatch(recorded))
        and bool(_SETTLE_LANDED.fullmatch(replayed))
    )


ALLOWED_DIFFERENCES: list[AllowedDifference] = [
    AllowedDifference(
        "**.id",
        "Ids are freshly minted per world instance from the seeded stream "
        "(architecture.md §4.4); they never equal the recorded real-Stripe id "
        "by construction, so comparing them tests nothing.",
    ),
    AllowedDifference(
        "**.created",
        "The instance clock and the record-time wall clock are different "
        "instants; equality was never meaningful.",
    ),
    AllowedDifference(
        "**.*_at",
        "Every `*_at` timestamp field (`canceled_at`, `available_on`-shaped "
        "windows, …) differs for the same clock reason as `created`. Suffix-"
        "matched per field so a blanket `**` cannot hide a non-timestamp.",
    ),
    AllowedDifference(
        "**.invoice_prefix",
        "Stripe mints a random 8-character prefix per customer and so does "
        "this world (`components/data_model.md` §3.13): same shape, different "
        "random draws — the id rule in miniature.",
    ),
    AllowedDifference(
        "**.livemode",
        "Both sides are always false — recorded from test mode, emitted as a "
        "constant here. Predicate mode: if either side is ever anything but "
        "false, that is a real divergence, not a permitted difference.",
        predicate=_both_false,
    ),
    AllowedDifference(
        "**.request_log_url",
        "A Stripe-dashboard URL tied to the recording account; this world "
        "does not model a dashboard. Recorded values are already normalized "
        "to a placeholder by the redaction pass, and the field is absent on "
        "this side.",
    ),
    AllowedDifference(
        "**.fingerprint",
        "Card fingerprints are derived per recording account on real Stripe "
        "and per content digest here (billing/magic_cards.py): same number -> "
        "same fingerprint within each world, never equal across the divide — "
        "the id rule in miniature, with the same stability property.",
    ),
    AllowedDifference(
        "**.shared_payment_granted_token",
        "The live API at the pinned version emits fields its own spec does "
        "not declare (recorded on payment_method, Phase 6); this world emits "
        "the spec's property set, which is the authority for object shape.",
    ),
    AllowedDifference(
        "**.customer",
        "Reference-valued fields carry ids minted per instance "
        "(architecture.md §4.4): the `**.id` rule on the field name a "
        "reference lands on. Predicate mode so a non-id divergence on any "
        "`customer` field still fails.",
        predicate=_both_cus_ids,
    ),
    AllowedDifference(
        "**.url",
        "A list envelope's `url` echoes the caller's path, scoped paths "
        "included, so it embeds the same freshly minted ids. Predicate mode: "
        "with every id-shaped token masked, recorded and replayed must be "
        "identical.",
        predicate=_same_url_modulo_ids,
    ),
    AllowedDifference(
        "**.error.message",
        "A `resource_missing` message names the id it could not find, and "
        "the ids differ per instance — the `**.id` rule where the id is the "
        "message's whole variable content. Predicate mode admits exactly the "
        "`No such customer: '<cus_id>'` form on both sides; every other "
        "message stays byte-exact.",
        predicate=_no_such_customer_modulo_id,
    ),
    AllowedDifference(
        "**.error.message",
        "The same id-only rule for every other `No such <object>: '<id>'` "
        "message (recorded for products, coupons, promotion codes and tax "
        "rates, Phase 7): form-only comparison, id-shaped on both sides.",
        predicate=_no_such_object_modulo_id,
    ),
    AllowedDifference(
        "**.error.message",
        "The unrecognized-URL 404 (recorded in cassette 07 on both a method "
        "mismatch and an unknown path): the real message continues with "
        "Stripe's docs/support pointers, which this world has no dashboard "
        "to source, so it emits the quoted form alone. Predicate mode admits "
        "exactly the long form recorded and the short form replayed; the "
        "`<METHOD>: <path>` pair itself still carries any minted ids.",
        predicate=_unrecognized_url_modulo_suffix,
    ),
    AllowedDifference(
        "**.updated",
        "A product's `updated` is a timestamp with the same clock reason as "
        "`created` (data_model §4.2 names the pair); the instance clock and "
        "the record-time wall clock are different instants.",
    ),
    AllowedDifference(
        "**.error.message",
        "The prices lookup-key conflict names the holding price's id — "
        "the `**.id` rule in its second message-shaped disguise (probed, "
        "Phase 7). Predicate mode admits exactly the `A price (…)` form on "
        "both sides.",
        predicate=_price_lookup_conflict_modulo_id,
    ),
    # --- the catalog's reference-valued fields (Phase 7) ---
    AllowedDifference(
        "**.default_price",
        "A product's default price carries a freshly minted id "
        "(architecture.md §4.4): the `**.id` rule on the field a reference "
        "lands on, predicated to id-shaped pairs so a real divergence still "
        "fails.",
        predicate=_both_prefixed_ids("price_"),
    ),
    AllowedDifference(
        "**.product",
        "A price's product reference carries a freshly minted id — the "
        "`**.id` rule again, predicated to `prod_`-shaped pairs.",
        predicate=_both_prefixed_ids("prod_"),
    ),
    AllowedDifference(
        "**.promotion.coupon",
        "The coupon nested under `promotion` is a reference whose value is "
        "minted per instance — the `**.id` rule on the pinned version's "
        "nested spelling, narrowed to minted-shape pairs so a caller-supplied "
        "coupon id still compares byte-exact.",
        predicate=_both_minted_coupon_ids,
    ),
    AllowedDifference(
        "**.code",
        "A promotion code's minted `code` is eight uppercase alphanumerics "
        "drawn per instance (recorded `BFDACGQS`); caller-supplied codes "
        "compare byte-exact and never reach this entry.",
        predicate=_both_minted_codes,
    ),
    # The three fields the live product body carries that the pinned spec
    # does not declare (recorded, Phase 7): this world emits the spec's
    # property set — the same ruling as `shared_payment_granted_token`.
    AllowedDifference(
        "**.attributes",
        "The live product body carries `attributes: []`, a legacy field the "
        "pinned spec no longer declares; the spec is the authority for "
        "shape, so this world omits it.",
        predicate=lambda recorded, replayed: recorded == [] and replayed is None,
    ),
    AllowedDifference(
        "**.tax_details",
        "The live product body carries a `tax_details` object (mirroring "
        "`tax_code`) the pinned spec does not declare; omitted here for the "
        "same reason as `attributes`.",
        predicate=lambda recorded, replayed: replayed is None,
    ),
    AllowedDifference(
        "body.type",
        'The live product body carries `type: "service"`, undeclared at '
        "the pinned version. Path-scoped rather than `**` because `type` is "
        "a real, compared field on price bodies; the predicate admits only "
        "the product's constant.",
        predicate=lambda recorded, replayed: recorded == "service" and replayed is None,
    ),
    AllowedDifference(
        "body.data[*].type",
        "The list-bodies form of the product `type` entry above: same "
        "constant, same reason, scoped so price lists still compare their "
        "`type` byte-exact.",
        predicate=lambda recorded, replayed: recorded == "service" and replayed is None,
    ),
    AllowedDifference(
        "**.recurring.trial_period_days",
        "The live `price.recurring` carries `trial_period_days`, which the "
        "pinned spec does not declare on `recurring`; omitted here like every "
        "other undeclared live field.",
        predicate=lambda recorded, replayed: replayed is None,
    ),
    # Scenario 6's attachment refusal: the recorded decline envelope carries
    # network chatter and a form-boundary artifact this world deliberately
    # omits. Scoped here so the entries can never mask a regression on any
    # other decline.
    AllowedDifference(
        "body.error.param",
        "The recorded envelope answers param as an empty string (a form-"
        "encoding artifact); this world omits a param it has no value for.",
        scenario="06_customers_payment_methods",
    ),
    AllowedDifference(
        "body.error.advice_code",
        "Real Stripe decorates the decline with advice_code try_again_later; "
        "no advice model exists here to source one from.",
        scenario="06_customers_payment_methods",
    ),
    AllowedDifference(
        "body.error.network_decline_code",
        "Issuer network decline codes are network state this world does not model.",
        scenario="06_customers_payment_methods",
    ),
    # --- the money path's reference-valued fields and secrets (Phase 8) ---
    AllowedDifference(
        "**.client_secret",
        "A client secret embeds its intent's minted id and a per-creation "
        "random suffix — the id rule twice over. Predicate mode admits only "
        "the `{pi}_secret_{…}` shape on both sides.",
        predicate=_both_client_secrets,
    ),
    AllowedDifference(
        "**.payment_method",
        "Reference-valued fields carry ids minted per instance: the `**.id` "
        "rule on the field a payment-method reference lands on, predicated "
        "to `pm_`-shaped string pairs (the full-object shapes under "
        "`last_payment_error.payment_method` compare field-by-field).",
        predicate=_both_pm_ids,
    ),
    AllowedDifference(
        "**.latest_charge",
        "The id rule on the charge reference a PaymentIntent carries.",
        predicate=_both_prefixed_ids("ch_"),
    ),
    AllowedDifference(
        "**.payment_intent",
        "The id rule on the intent reference a charge carries, predicated to "
        "`pi_`-shaped string pairs.",
        predicate=_both_prefixed_ids("pi_"),
    ),
    AllowedDifference(
        "**.error.charge",
        "The decline envelope's `charge` names the freshly minted failed "
        "charge — the id rule in its message-shaped position.",
        predicate=_both_prefixed_ids("ch_"),
    ),
    AllowedDifference(
        "**.last_payment_error.charge",
        "The same rule one level in, on the recorded shape of `last_payment_error`.",
        predicate=_both_prefixed_ids("ch_"),
    ),
    AllowedDifference(
        "**.invoice_settings.default_payment_method",
        "The customer's default payment method carries a minted id — the id "
        "rule, predicated to `pm_`-shaped pairs.",
        predicate=_both_pm_ids,
    ),
    # --- the money path's account-config artifacts (Phase 8) ---
    # The recording account drives payment-method availability from its
    # Dashboard configuration; this world has no dashboard. These entries
    # carry the artifacts that config puts on every recorded body.
    AllowedDifference(
        "**.automatic_payment_methods",
        "The recording account's Dashboard configuration fills this on every "
        "intent a caller did not set it on; this world has no dashboard to "
        "source one from and emits the parameter's own value or null.",
        predicate=lambda recorded, replayed: replayed is None or recorded == replayed,
    ),
    AllowedDifference(
        "**.payment_method_configuration_details",
        "The recording account's payment-method configuration echoes its "
        "`pmc_…` id on unconfigured intents; no configuration object exists "
        "in this world (scope boundary).",
        predicate=lambda recorded, replayed: replayed is None,
    ),
    AllowedDifference(
        "**.payment_method_types",
        "The Dashboard configuration adds `link` beside `card` wherever the "
        "caller pinned no types; this world's unpinned default is "
        "payment_methods' own `['card']`. Predicate mode admits exactly the "
        "one-extra-rail shape, so a pinned list still compares byte-exact.",
        predicate=lambda recorded, replayed: (
            isinstance(recorded, list)
            and isinstance(replayed, list)
            and recorded == [*replayed, "link"]
        ),
    ),
    AllowedDifference(
        "**.payment_method_options.link",
        "The Dashboard configuration adds a `link` block beside `card`; no "
        "Link rail is modeled here.",
        predicate=lambda recorded, replayed: replayed is None,
    ),
    AllowedDifference(
        "**.managed_payments",
        'The live body carries `{"enabled": false}` where the pinned spec '
        "types the field `string` — this world emits the spec-legal null "
        "rather than failing schema conformance (functional spec §4: the "
        "spec is the authority for shape).",
        predicate=lambda recorded, replayed: recorded == {"enabled": False} and replayed is None,
    ),
    # --- live-only fields the pinned spec does not declare (Phase 8) ---
    AllowedDifference(
        "**.payment_record",
        "Undeclared in the pinned spec, emitted null by the live API "
        "(recorded, Phase 8); omitted here like every other undeclared live "
        "field.",
        predicate=lambda recorded, replayed: recorded is None and replayed is None,
    ),
    AllowedDifference(
        "**.source",
        "Undeclared on payment_intent and charge at the pinned version, "
        "emitted null live; omitted here.",
        predicate=lambda recorded, replayed: recorded is None and replayed is None,
    ),
    AllowedDifference(
        "**.destination",
        "Connect-shaped and undeclared on charge at the pinned version; "
        "nulled on the wire, omitted here.",
        predicate=lambda recorded, replayed: recorded is None and replayed is None,
    ),
    AllowedDifference(
        "**.dispute",
        "Undeclared on charge at the pinned version (disputes are Phase 9); "
        "nulled on the wire, omitted here.",
        predicate=lambda recorded, replayed: recorded is None and replayed is None,
    ),
    AllowedDifference(
        "**.order",
        "Undeclared on charge at the pinned version; nulled on the wire, omitted here.",
        predicate=lambda recorded, replayed: recorded is None and replayed is None,
    ),
    AllowedDifference(
        "**.radar_options",
        "Typed literally `null` by the pinned spec but emitted `{}` live; "
        "omitted here, where the spec-legal value would be null and null is "
        "indistinguishable from absent for an empty object.",
        predicate=lambda recorded, replayed: recorded == {} and replayed is None,
    ),
    # --- network and Radar state this world does not model (Phase 8) ---
    AllowedDifference(
        "**.outcome.risk_score",
        "Radar's risk score is ML state this world does not model; recorded "
        "integers vary per charge (17, 6, 56…), replayed omits the field.",
    ),
    AllowedDifference(
        "**.outcome.advice_code",
        "Network advice codes are issuer chatter this world does not model; "
        "recorded `try_again_later` on declines, replayed null.",
        predicate=lambda recorded, replayed: replayed is None,
    ),
    AllowedDifference(
        "**.outcome.network_decline_code",
        "Issuer network decline codes are network state this world does not "
        "model; recorded `01` on declines, replayed null.",
        predicate=lambda recorded, replayed: replayed is None,
    ),
    AllowedDifference(
        "**.error.advice_code",
        "The decline envelope's network chatter, omitted here — the same "
        "ruling as scenario 06's attachment refusal, now met on the money "
        "path.",
        predicate=lambda recorded, replayed: replayed is None,
    ),
    AllowedDifference(
        "**.error.network_decline_code",
        "The decline envelope's network chatter, omitted here.",
        predicate=lambda recorded, replayed: replayed is None,
    ),
    AllowedDifference(
        "**.last_payment_error.advice_code",
        "`last_payment_error`'s network chatter, omitted here.",
        predicate=lambda recorded, replayed: replayed is None,
    ),
    AllowedDifference(
        "**.last_payment_error.network_decline_code",
        "`last_payment_error`'s network chatter, omitted here.",
        predicate=lambda recorded, replayed: replayed is None,
    ),
    AllowedDifference(
        "**.payment_method_details.card.authorization_code",
        "The issuer's six-digit authorization code is network randomness; "
        "the field is spec-nullable and null here.",
        predicate=lambda recorded, replayed: replayed is None,
    ),
    AllowedDifference(
        "**.payment_method_details.card.network_transaction_id",
        "The card network's transaction id is network randomness; spec-nullable and null here.",
        predicate=lambda recorded, replayed: replayed is None,
    ),
    AllowedDifference(
        "**.payment_method_details.card.electronic_commerce_indicator",
        "Undeclared in the pinned spec, emitted `07` live; omitted here.",
        predicate=lambda recorded, replayed: replayed is None,
    ),
    AllowedDifference(
        "**.capture_before",
        "The authorization-expiry instant is network-set (recorded values "
        "jitter around created+7d) and embeds the clock difference besides — "
        "the `**.created` reasoning in miniature.",
        predicate=lambda recorded, replayed: (
            isinstance(recorded, int) and isinstance(replayed, int)
        ),
    ),
    AllowedDifference(
        "**.card.country",
        "The test cards' issuer geography varies by recording account region "
        "(probed Phase 8: the decline and 3DS tokens answered `IE` where the "
        "success tokens answered `US`); a two-letter code either way is the "
        "form.",
        predicate=_both_country_codes,
    ),
    AllowedDifference(
        "**.payment_method.card.checks.cvc_check",
        "The payment-method snapshot a decline embeds in "
        "`last_payment_error` / `error.payment_method` reports the CVC check "
        "inconsistently across recordings (probed both `pass` and "
        "`unchecked` for the same unattached card, Phase 8); either member "
        "of the pair is the form.",
        predicate=lambda recorded, replayed: (
            recorded in ("pass", "unchecked") and replayed in ("pass", "unchecked")
        ),
    ),
    AllowedDifference(
        "**.balance_transaction",
        "The ledger reference a charge, refund or payout carries: freshly "
        "minted `txn_` ids both sides; null recorded against a replayed id "
        "(the async settlement window — live Stripe mints the charge's row "
        "moments after capture; this world's ledger is synchronous, Phase 11); "
        "or the dispute's spec-undeclared singular field, absent here. See "
        "`_ledger_reference` for the shapes and the compensation for the "
        "null overlap.",
        predicate=_ledger_reference,
    ),
    AllowedDifference(
        "**.receipt_url",
        "A dashboard URL whose signature base64-embeds the recording account "
        "id (redacted to a placeholder at record time); this world derives a "
        "deterministic id-shaped URL the way credit_note.pdf does.",
        predicate=_recorded_placeholder_or_same,
    ),
    AllowedDifference(
        "**.error.message",
        "The idempotency-mismatch message names the caller's key, which "
        "differs per recording run by design (the keyed steps embed "
        "run-specific ids, so every run draws a fresh key) — form-only "
        "comparison, the id rule where the key is the message's whole "
        "variable content.",
        predicate=_idempotency_mismatch_modulo_key,
    ),
    AllowedDifference(
        "**.error.message",
        "The two charge-capture refusals name the object id they refuse — "
        "the id-only rule again (both probed verbatim, Phase 8).",
        predicate=_charge_capture_message_modulo_id,
    ),
    AllowedDifference(
        "**.error.message",
        "The confirm-missing-method refusal names the customer id — the "
        "id-only rule (probed verbatim, Phase 8).",
        predicate=_confirm_missing_modulo_customer,
    ),
    AllowedDifference(
        "**.error.message",
        "The ownership refusal a customerless intent's confirm with a "
        "customer's method answers names both ids — the id-only rule "
        "(probed verbatim, CR round 1).",
        predicate=_pm_belongs_to_modulo_ids,
    ),
    AllowedDifference(
        "**.error.message",
        "The wrong-customer confirm refusal names both ids — the id-only "
        "rule, its sibling spelling (recorded verbatim, cassette 04).",
        predicate=_pm_not_belong_modulo_ids,
    ),
    # The transfer_group scope cut (cassette 04, CR round 2): real Stripe
    # refuses the update on a PI-created charge; this world has cut the
    # parameter, so the whole refusal envelope differs shape by shape —
    # four entries rather than one message rule, because the recorded
    # envelope carries no code/param/doc_url where the replayed one does.
    AllowedDifference(
        "**.error.message",
        "Real Stripe's PI-naming transfer_group refusal versus this world's "
        "`Received unknown parameter` — the parameter is cut (data_model §7), "
        "declared here rather than silently diverging.",
        predicate=_transfer_group_message,
    ),
    AllowedDifference(
        "**.error.code",
        "The recorded transfer_group refusal carries no code; the scope cut's "
        "`parameter_unknown` does.",
        predicate=_transfer_group_code,
    ),
    AllowedDifference(
        "**.error.param",
        "The recorded transfer_group refusal carries no param; the scope cut's "
        "refusal names the parameter.",
        predicate=_transfer_group_param,
    ),
    AllowedDifference(
        "**.error.doc_url",
        "The recorded transfer_group refusal carries no doc_url; the scope "
        "cut's derived one is present.",
        predicate=_transfer_group_doc_url,
    ),
    # --- the refunds-and-disputes block (Phase 9) ---
    AllowedDifference(
        "**.charge",
        "The id rule on the charge reference refund and dispute bodies carry, "
        "predicated to `ch_`-shaped string pairs.",
        predicate=_both_prefixed_ids("ch_"),
    ),
    AllowedDifference(
        "**.dispute",
        "The disputed charge's `dispute` names its freshly minted `du_` "
        "dispute (probed, Phase 9: live dispute ids are `du_…` at the pinned "
        "version). The field is undeclared on charge in the pinned spec, so "
        "this world omits it and the spec's property set is the authority — "
        "the same ruling as every other live-only field.",
        predicate=lambda recorded, replayed: (
            isinstance(recorded, str) and recorded.startswith("du_") and replayed is None
        ),
    ),
    AllowedDifference(
        "**.refunds.total_count",
        "The charge's inline refunds envelope (under `expand[]=refunds`) "
        "carries a `total_count` the pinned spec's inline schema does not "
        "declare; omitted here per the spec-is-authority ruling.",
        predicate=lambda recorded, replayed: isinstance(recorded, int) and replayed is None,
    ),
    AllowedDifference(
        "body.count",
        "The live disputes list carries a fifth envelope key, `count`, "
        "undeclared by the pinned spec's list schema; omitted here like every "
        "other undeclared live field.",
        predicate=lambda recorded, replayed: isinstance(recorded, int) and replayed is None,
    ),
    AllowedDifference(
        "**.destination_details.card.reference",
        "The acquirer reference number is network randomness that appears "
        "once the refund settles at the network (seconds later live); this "
        "world's refund never leaves `pending`, so the field stays absent.",
        predicate=lambda recorded, replayed: replayed is None,
    ),
    AllowedDifference(
        "**.destination_details.card.reference_status",
        "The acquirer reference flips `pending` → `available` on network "
        "settlement timing a frozen clock cannot model; `pending` is emitted "
        "here and either member of the pair is the form.",
        predicate=lambda recorded, replayed: (
            recorded in ("available", "pending") and replayed in ("available", "pending")
        ),
    ),
    AllowedDifference(
        "**.due_by",
        "The evidence deadline is computed from the creation instant "
        "(end of the UTC day eight days out, the recorded model), so it "
        "differs for the same clock reason as `created`.",
        predicate=lambda recorded, replayed: (
            isinstance(recorded, int) and isinstance(replayed, int)
        ),
    ),
    AllowedDifference(
        "**.evidence.customer_name",
        "Stripe enriches dispute evidence from the customer record "
        "(`customer_name`, and its description when unnamed — observed, not "
        "documented); this world stores submitted evidence verbatim, so the "
        "auto-filled value has no counterpart here.",
        predicate=lambda recorded, replayed: (
            (recorded is None or isinstance(recorded, str)) and replayed is None
        ),
    ),
    AllowedDifference(
        "**.evidence.customer_email_address",
        "The email half of Stripe's customer-record evidence enrichment; not "
        "modeled, like `customer_name` beside it.",
        predicate=lambda recorded, replayed: (
            (recorded is None or isinstance(recorded, str)) and replayed is None
        ),
    ),
    AllowedDifference(
        "**.evidence.product_description",
        "Stripe also enriches evidence from the charge's own description "
        "(observed, Phase 9); not modeled.",
        predicate=lambda recorded, replayed: (
            (recorded is None or isinstance(recorded, str)) and replayed is None
        ),
    ),
    AllowedDifference(
        "**.balance_transactions",
        "A dispute's derived ledger rows (Phase 11): the recording account "
        "settles in CAD, so the recorded withdrawal/reversal amounts are "
        "FX-converted from the USD charges and name the recording run's "
        "charge ids; this world derives the same rows natively in the "
        "charge's own currency — same types, same reporting categories, "
        "different money and ids. Predicated to lists of bt objects so a "
        "shape regression still fails.",
        predicate=lambda recorded, replayed: (
            isinstance(recorded, list)
            and isinstance(replayed, list)
            and all(
                isinstance(item, dict) and item.get("object") == "balance_transaction"
                for item in recorded
            )
            and all(
                isinstance(item, dict) and item.get("object") == "balance_transaction"
                for item in replayed
            )
        ),
    ),
    AllowedDifference(
        "**.error.message",
        "The fully-refunded refusal names the charge id — the id-only rule "
        "(probed verbatim, Phase 9).",
        predicate=_message_modulo_id(_CHARGE_ALREADY_REFUNDED),
    ),
    AllowedDifference(
        "**.error.message",
        "The charged-back refusal names the charge id — the id-only rule "
        "(probed verbatim, Phase 9).",
        predicate=_message_modulo_id(_CHARGE_CHARGED_BACK),
    ),
    AllowedDifference(
        "**.error.message",
        "The charge-scoped dispute read of an undisputed charge names the "
        "charge id — the id-only rule (probed verbatim, Phase 9).",
        predicate=_message_modulo_id(_NO_DISPUTE_FOR_CHARGE),
    ),
    AllowedDifference(
        "**.error.message",
        "The uncaptured-hold refund refusal names the PaymentIntent id — the "
        "id-only rule (probed verbatim, Phase 9).",
        predicate=_message_modulo_id(_UNCAPTURED_REFUND_REFUSED),
    ),
    # The dispute settle collapse, scoped to the scenario that records it so
    # no other status comparison can hide behind it.
    AllowedDifference(
        "body.status",
        "Live test mode resolves the magic evidence strings asynchronously: "
        "the submit response carries `under_review` (inquiries "
        "`warning_under_review`) and the won/lost/escalated state lands "
        "seconds later. A frozen clock cannot wait out issuer review, so "
        "this world settles inside the call — the submit response is exactly "
        "one hop ahead, and every later read matches. Scoped to the scenario "
        "recording it; the general declaration is in STRUCTURAL_DIFFERENCES.",
        scenario="05_refunds_disputes",
        predicate=_settle_status,
    ),
    AllowedDifference(
        "body.is_charge_refundable",
        "The refund gate the settle collapse moves: the winning submit "
        "flips `is_charge_refundable` open synchronously here and the "
        "inquiry escalation closes it, both asynchronously live. Scoped "
        "with the `body.status` entry beside it.",
        scenario="05_refunds_disputes",
        predicate=lambda recorded, replayed: (
            (recorded is False and replayed is True) or (recorded is True and replayed is False)
        ),
    ),
    # --- the setup-intents block (Phase 10) ---
    AllowedDifference(
        "**.latest_attempt",
        "The setup attempt stub is a freshly minted `setatt_…` id on every "
        "confirm attempt (recorded, cassette 10) — the id rule on the field, "
        "predicated to setatt_-shaped pairs.",
        predicate=_both_prefixed_ids("setatt_"),
    ),
    AllowedDifference(
        "**.error.network_advice_code",
        "The expired-card setup decline carries the issuer's network advice "
        "code beside its network decline code; network chatter is omitted "
        "here like the money path's declines.",
        predicate=lambda recorded, replayed: replayed is None,
    ),
    AllowedDifference(
        "**.last_setup_error.advice_code",
        "`last_setup_error`'s network chatter (recorded `try_again_later` / "
        "`confirm_card_data`), omitted here — the same ruling as the money "
        "path's `last_payment_error`.",
        predicate=lambda recorded, replayed: replayed is None,
    ),
    AllowedDifference(
        "**.last_setup_error.network_advice_code",
        "`last_setup_error`'s network chatter, omitted here.",
        predicate=lambda recorded, replayed: replayed is None,
    ),
    AllowedDifference(
        "**.last_setup_error.network_decline_code",
        "`last_setup_error`'s issuer decline code, omitted here.",
        predicate=lambda recorded, replayed: replayed is None,
    ),
    AllowedDifference(
        "**.error.message",
        "The setup-intent ownership refusals name the PaymentMethod and the "
        "Customer — the id-only rule, both recorded spellings (probed "
        "verbatim at create, update and confirm, Phase 10).",
        predicate=_seti_ownership_modulo_ids,
    ),
    # The recording account's Dashboard fills an unpinned SetupIntent's
    # payment_method_types with five rails beside card (recorded: card,
    # bancontact, klarna, link, pix, satispay) — a wider fill than the
    # PaymentIntent's one-extra-rail shape the global entry admits, so the
    # bare-create divergence is scoped here.
    AllowedDifference(
        "**.payment_method_types",
        "The Dashboard configuration fills an unpinned SetupIntent's types "
        "with five rails beside this world's `['card']` default (recorded, "
        "cassette 10's one unpinned create); a pinned list still compares "
        "byte-exact, which is why every other step pins one.",
        scenario="10_setup_intents",
        predicate=lambda recorded, replayed: (
            recorded == ["card", "bancontact", "klarna", "link", "pix", "satispay"]
            and replayed == ["card"]
        ),
    ),
    # --- the ledger-and-payouts block (Phase 11) ---
    AllowedDifference(
        "**.available_on",
        "The settlement instant is derived from the creation clock plus the "
        "account's settlement schedule (`settlement_business_days`, a "
        "LedgerSpec constant — no API-discoverable value exists, the research "
        "lane's gap 3), so it differs for the same clock reason as `created`.",
        predicate=lambda recorded, replayed: (
            isinstance(recorded, int) and isinstance(replayed, int)
        ),
    ),
    AllowedDifference(
        "**.arrival_date",
        "A payout's expected arrival follows the destination's banking "
        "schedule, which a frozen clock and a stub destination cannot model; "
        "this world pins the settlement window. Same int-pair shape as "
        "`available_on`.",
        predicate=lambda recorded, replayed: (
            isinstance(recorded, int) and isinstance(replayed, int)
        ),
    ),
    AllowedDifference(
        "**.source",
        "The id rule on the polymorphic `balance_transaction.source` (charge, "
        "refund, dispute and payout ids), predicated to same-prefix pairs so "
        "the recorded nulls the money path carries keep comparing through the "
        "entry above.",
        predicate=_both_ledger_sources,
    ),
    AllowedDifference(
        "**.fee",
        "The processing fee is account pricing, not spec behavior (the "
        "research lane's gap 1): the recorded account's schedule differs from "
        "this world's declared `FeeSchedule` constant, on the percent part "
        "only. Predicated to int pairs; `fee_details` below carries the same "
        "difference in its line items.",
        predicate=lambda recorded, replayed: (
            isinstance(recorded, int) and isinstance(replayed, int)
        ),
    ),
    AllowedDifference(
        "**.net",
        "`net = amount - fee`, so the fee difference lands here by arithmetic "
        "— the identity itself is enforced by the schema's CHECK and the "
        "ledger invariants, not compared against the recording.",
        predicate=lambda recorded, replayed: (
            isinstance(recorded, int) and isinstance(replayed, int)
        ),
    ),
    AllowedDifference(
        "**.fee_details[*].amount",
        "The fee breakdown's line-item amounts — the pricing difference "
        "`**.fee` carries, one level down.",
        predicate=lambda recorded, replayed: (
            isinstance(recorded, int) and isinstance(replayed, int)
        ),
    ),
    AllowedDifference(
        "**.description",
        "The dispute ledger rows' descriptions name the charge they belong "
        "to — the id-only rule (recorded, cassettes 05 and 11). Predicated to "
        "the two Chargeback forms so every other description stays "
        "byte-exact.",
        predicate=_dispute_bt_description_modulo_id,
    ),
    AllowedDifference(
        "body.status",
        "A balance transaction's settlement status: the recording account "
        "marks dispute withdrawals available immediately, this world's T+2 "
        "rule holds them pending under a frozen clock (Phase 11). No other "
        "object's `status` can record `available`, so the predicate cannot "
        "mask a status regression elsewhere.",
        predicate=_bt_settlement_status,
    ),
    AllowedDifference(
        "body.data[*].status",
        "The list-bodies form of the settlement-status entry above.",
        predicate=_bt_settlement_status,
    ),
    # The dispute settle collapse, met again on this slice's winning-evidence
    # submit (the scenario-05 entries above carry the general declaration).
    AllowedDifference(
        "body.status",
        "The settle collapse on the ledger slice's winning-evidence submit "
        "(scenario 05's entries carry the declaration): live answers "
        "`under_review` and resolves seconds later; the frozen clock settles "
        "inside the call.",
        scenario="11_ledger_payouts",
        predicate=_settle_status,
    ),
    AllowedDifference(
        "body.is_charge_refundable",
        "The refund gate the settle collapse moves, on this slice's submit — "
        "scenario 05's entry, scoped here the same way.",
        scenario="11_ledger_payouts",
        predicate=lambda recorded, replayed: (
            (recorded is False and replayed is True) or (recorded is True and replayed is False)
        ),
    ),
    AllowedDifference(
        "body.source.status",
        "The settle collapse seen through `expand[]=source` off a dispute's "
        "withdrawal row: the inflated dispute body is one hop ahead the same "
        "way its own body is.",
        predicate=_settle_status,
    ),
    AllowedDifference(
        "body.source.is_charge_refundable",
        "The refund gate through the same expansion.",
        predicate=lambda recorded, replayed: (
            (recorded is False and replayed is True) or (recorded is True and replayed is False)
        ),
    ),
    # The computed balance on the recording account is the account's whole
    # pre-existing ledger — a CAD settlement account with a dispute history —
    # where the replay's balance derives from the scenario's own objects
    # alone. Whole-key entries, scoped to the one scenario that reads it.
    AllowedDifference(
        "body.available",
        "The recording account's available balance is pre-existing account "
        "state (its own CAD ledger), not scenario state; the replay's is "
        "computed from the scenario's rows. Scoped to the scenario that "
        "reads /v1/balance.",
        scenario="11_ledger_payouts",
    ),
    AllowedDifference(
        "body.pending",
        "The pending half of the same computed-balance difference.",
        scenario="11_ledger_payouts",
    ),
    AllowedDifference(
        "body.refund_and_dispute_prefunding",
        "The recorded account reports an all-zero prefunding block in its "
        "settlement currency; this world has no prefunding ledger rows to "
        "report and omits the key — spec-legal (only available/livemode/"
        "object/pending are required), the spec-is-authority ruling.",
        scenario="11_ledger_payouts",
    ),
    # Scenario 3 exists to record what a malformed Stripe-Version answers.
    # The world deliberately serves one fixed version with no header channel
    # and no negotiation (functional spec §6.5), so the whole response —
    # status and body — differs, scoped to this scenario alone so the entry
    # can never mask a regression elsewhere.
    AllowedDifference(
        "status",
        "Real Stripe answers a malformed Stripe-Version with 400; this world "
        "serves the one pinned version and cannot see the header "
        "(functional spec §6.5).",
        scenario="03_malformed_stripe_version",
    ),
    AllowedDifference(
        "body",
        "The recorded body is the malformed-version error envelope; the "
        "replayed body is the normal pinned-version response for the same "
        "request.",
        scenario="03_malformed_stripe_version",
    ),
    # --- the subscriptions slice's reference pairs and periods (Phase 12) ---
    AllowedDifference(
        "**.latest_invoice",
        "The first invoice's freshly minted `in_` id — the `**.id` rule on "
        "the field a subscription's invoice reference lands on, predicated "
        "to id-shaped pairs (and null pairs) so a real divergence fails.",
        predicate=_both_prefixed_ids("in_"),
    ),
    AllowedDifference(
        "**.subscription",
        "A subscription_item's parent reference carries the minted `sub_` "
        "id — the `**.id` rule, predicated.",
        predicate=_both_prefixed_ids("sub_"),
    ),
    AllowedDifference(
        "**.pending_setup_intent",
        "The resume flow's SetupIntent id is minted per instance — the "
        "`**.id` rule on the pause/resume machinery's reference.",
        predicate=_both_prefixed_ids("seti_"),
    ),
    AllowedDifference(
        "**.default_payment_method",
        "A subscription's explicit default-method reference — minted `pm_` "
        "ids both sides, predicated so a non-id divergence still fails.",
        predicate=_both_prefixed_ids("pm_"),
    ),
    AllowedDifference(
        "**.start_date",
        "The subscription's start is the creation clock — the `**.created` "
        "reasoning on the one timestamp field the suffix rule cannot catch "
        "(`start_date` carries no `_at`).",
        predicate=lambda recorded, replayed: (
            isinstance(recorded, int) and isinstance(replayed, int)
        ),
    ),
    AllowedDifference(
        "**.billing_cycle_anchor",
        "The anchor is the creation clock (or a caller timestamp, which "
        "then compares equal); predicated to integer pairs so only the "
        "clock-derived case is admitted.",
        predicate=lambda recorded, replayed: (
            isinstance(recorded, int) and isinstance(replayed, int)
        ),
    ),
    AllowedDifference(
        "**.current_period_start",
        "An item's period opens at the subscription's clock-derived anchor "
        "— the `**.created` reasoning on the period pair this version "
        "carries per item (there is no subscription-level period).",
        predicate=lambda recorded, replayed: (
            isinstance(recorded, int) and isinstance(replayed, int)
        ),
    ),
    AllowedDifference(
        "**.current_period_end",
        "An item's period closes one calendar interval past its start "
        "(clock-derived) — the same reasoning as its start.",
        predicate=lambda recorded, replayed: (
            isinstance(recorded, int) and isinstance(replayed, int)
        ),
    ),
    AllowedDifference(
        "**.trial_start",
        "A trial's start is the creation clock — `**.created` again, on "
        "the field name the suffix rule cannot catch.",
        predicate=lambda recorded, replayed: (
            isinstance(recorded, int) and isinstance(replayed, int)
        ),
    ),
    AllowedDifference(
        "**.trial_end",
        "A caller-supplied trial end compares equal; the `trial_end: "
        '"now"` path derives the instant from the clock, which is the '
        "only pair this entry admits.",
        predicate=lambda recorded, replayed: (
            isinstance(recorded, int) and isinstance(replayed, int)
        ),
    ),
    # The live-only legacy echoes the pinned spec does not declare
    # (recorded, Phase 12); the spec is the authority for shape.
    AllowedDifference(
        "**.plan",
        "The live subscription and item bodies carry a legacy `plan` "
        "object the pinned spec no longer declares; omitted here like "
        "every other undeclared live field.",
        predicate=lambda recorded, replayed: isinstance(recorded, dict) and replayed is None,
    ),
    AllowedDifference(
        "body.quantity",
        "The live subscription body's legacy top-level `quantity` echo "
        "(item 0's quantity, probed present even on a two-item "
        "subscription); undeclared at the pinned version and omitted. "
        "Path-scoped so an item's own `quantity` still compares byte-exact.",
        predicate=lambda recorded, replayed: isinstance(recorded, int) and replayed is None,
    ),
    AllowedDifference(
        "body.data[*].quantity",
        "The list-bodies form of the legacy subscription `quantity` echo.",
        predicate=lambda recorded, replayed: isinstance(recorded, int) and replayed is None,
    ),
    AllowedDifference(
        "**.current_trial",
        "The live item body's `current_trial` echo; undeclared at the pinned version, omitted.",
        predicate=lambda recorded, replayed: replayed is None,
    ),
    AllowedDifference(
        "**.items.total_count",
        "The live `items` envelope carries `total_count`, which the pinned "
        "spec's nested list schema does not declare — the `charge.refunds` "
        "ruling (Phase 9), omitted and allow-listed.",
        predicate=lambda recorded, replayed: replayed is None,
    ),
    AllowedDifference(
        "**.trial_settings.end_behavior.billing_cycle_anchor",
        "The live trial_settings echo carries `billing_cycle_anchor: "
        '"now"`, undeclared at the pinned version; omitted.',
        predicate=lambda recorded, replayed: recorded == "now" and replayed is None,
    ),
    AllowedDifference(
        "**.pending_update.cancel_at_period_end",
        "The live pending_update echo carries `cancel_at_period_end` "
        "(null), undeclared at the pinned version; this world's parked "
        "update follows the schema's property set.",
        predicate=lambda recorded, replayed: recorded is None and replayed is None,
    ),
    AllowedDifference(
        "**.billing_mode.type",
        "The recording account's dashboard default is flexible billing "
        "mode; this world's constant is `classic` until the flexible-mode "
        "phase (conformance scenario 1's business). Predicated to exactly "
        "that pair.",
        predicate=lambda recorded, replayed: recorded == "flexible" and replayed == "classic",
    ),
    AllowedDifference(
        "**.billing_mode.flexible",
        "The flexible-mode configuration object, present on the recording "
        "account's bodies and null on this world's classic ones.",
        predicate=lambda recorded, replayed: isinstance(recorded, dict) and replayed is None,
    ),
    AllowedDifference(
        "**.pending_update.billing_cycle_anchor",
        "The parked resume's anchor target: cassette 12's resume body parks "
        "null where this world parks the instant the confirming SetupIntent "
        "applies (`now` -> the resume moment, `unchanged` -> the stored "
        "anchor). Predicated to that recorded-null/replayed-integer pair so "
        "any other divergence on the field still fails.",
        predicate=lambda recorded, replayed: recorded is None and isinstance(replayed, int),
    ),
    AllowedDifference(
        "**.error.message",
        "The duplicate-price refusal names the price id — the `**.id` rule "
        "where the id is the message's whole variable content, narrowed to "
        "the recorded form.",
        predicate=_dup_price_modulo_id,
    ),
    # --- the invoices slice (Phase 13) ---
    AllowedDifference(
        "**.invoice",
        "The id rule on the invoice reference a line, an invoiceitem or a "
        "payment carries, predicated to `in_`-shaped string pairs.",
        predicate=_both_prefixed_ids("in_"),
    ),
    AllowedDifference(
        "**.invoice_item",
        "The id rule on the invoiceitem reference a line's "
        "`parent.invoice_item_details` names, predicated to `ii_`-shaped "
        "pairs.",
        predicate=_both_prefixed_ids("ii_"),
    ),
    AllowedDifference(
        "**.pricing.price_details.price",
        "An `amount`+`currency` invoice item mints a one-off price per "
        "instance (the recorded mechanism, cassette 13) — the `**.id` rule "
        "on the minted price a `pricing` block names, predicated to "
        "`price_`-shaped pairs.",
        predicate=_both_prefixed_ids("price_"),
    ),
    AllowedDifference(
        "**.number",
        "An invoice's number is `<customer prefix>-<sequence>`, and both "
        "halves are per-instance mints (the prefix from `ctx.ids`, data_model "
        "§3.13) — the `**.id` rule on the compound. Predicated to the "
        "`AAAAAAAA-0000` shape on both sides so a real numbering regression "
        "still fails.",
        predicate=_invoice_number_pair,
    ),
    AllowedDifference(
        "**.hosted_invoice_url",
        "The hosted-invoice page URL embeds the recording account id and a "
        "signed payload (normalized to a placeholder at record time); this "
        "world derives a deterministic id-shaped URL the way credit_note.pdf "
        "and receipt_url do.",
        predicate=_recorded_placeholder_or_same,
    ),
    AllowedDifference(
        "**.invoice_pdf",
        "The invoice PDF URL — the same account-signed shape as "
        "hosted_invoice_url, derived id-shaped here.",
        predicate=_recorded_placeholder_or_same,
    ),
    AllowedDifference(
        "**.period_start",
        "A draft's zero-width period opens at the creation clock — the "
        "`**.created` reasoning on the period pair, which carries no `_at` "
        "suffix for the blanket rule to catch.",
        predicate=lambda recorded, replayed: (
            isinstance(recorded, int) and isinstance(replayed, int)
        ),
    ),
    AllowedDifference(
        "**.period_end",
        "The period pair's closing half — the same clock reasoning.",
        predicate=lambda recorded, replayed: (
            isinstance(recorded, int) and isinstance(replayed, int)
        ),
    ),
    AllowedDifference(
        "**.period.start",
        "A line's period opens at its source's clock (the invoice item's "
        "`date`, the subscription item's period) — the `**.created` "
        "reasoning inside the frozen `lines` JSON, where the field is a "
        "nested key rather than a column.",
        predicate=lambda recorded, replayed: (
            isinstance(recorded, int) and isinstance(replayed, int)
        ),
    ),
    AllowedDifference(
        "**.period.end",
        "A line's period closes at its source's clock — the same reasoning as its opening half.",
        predicate=lambda recorded, replayed: (
            isinstance(recorded, int) and isinstance(replayed, int)
        ),
    ),
    AllowedDifference(
        "**.date",
        "`invoiceitem` has no `created`; `date` is the creation clock — "
        "`**.created` under the field name the suffix rule cannot catch.",
        predicate=lambda recorded, replayed: (
            isinstance(recorded, int) and isinstance(replayed, int)
        ),
    ),
    AllowedDifference(
        "**.due_date",
        "A send_invoice draft's due date is `created + days_until_due` — the "
        "clock reasoning again (a caller-supplied absolute `due_date` compares "
        "equal; only the derived pairs reach this entry's predicate).",
        predicate=lambda recorded, replayed: (
            isinstance(recorded, int) and isinstance(replayed, int)
        ),
    ),
    AllowedDifference(
        "**.next_payment_attempt",
        "The scheduled first attempt on an auto-advancing draft is "
        "`created + 3600s` (recorded, cassette 13) — the clock reasoning on "
        "the one scheduled field the `*_at` suffix rule cannot catch.",
        predicate=lambda recorded, replayed: (
            isinstance(recorded, int) and isinstance(replayed, int)
        ),
    ),
    AllowedDifference(
        "**.account_country",
        "The account-echo country is recording-account state ('CA'); this "
        "world's static account defaults to 'US' until an account object "
        "exists (Phase 21) — the same ruling as `**.billing_mode.type`.",
        predicate=lambda recorded, replayed: (
            isinstance(recorded, str) and isinstance(replayed, str)
        ),
    ),
    AllowedDifference(
        "**.account_name",
        "The account-echo name is the recording account's own ('Seahaven "
        "Sandbox'); this world's static account has none to source one from "
        "and answers null.",
        predicate=lambda recorded, replayed: (
            (recorded is None or isinstance(recorded, str)) and replayed is None
        ),
    ),
    AllowedDifference(
        "**.rendering",
        "The recording account's Dashboard fills `rendering` with its PDF "
        "default on every manual invoice (recorded; null on its subscription "
        "invoices); no dashboard exists here and the spec-legal null is "
        "emitted — the `**.billing_mode.flexible` ruling.",
        predicate=lambda recorded, replayed: (
            (isinstance(recorded, dict) or recorded is None) and replayed is None
        ),
    ),
    AllowedDifference(
        "**.lines.total_count",
        "The live `lines` envelope carries `total_count`, which the pinned "
        "spec's nested list schema does not declare — the `items.total_count` "
        "ruling (Phase 12), omitted here and allow-listed.",
        predicate=lambda recorded, replayed: isinstance(recorded, int) and replayed is None,
    ),
    AllowedDifference(
        "**.invoicing_rules",
        "The live invoiceitem body carries `invoicing_rules: []`, a field the "
        "pinned spec does not declare; omitted here like every other "
        "undeclared live echo.",
        predicate=lambda recorded, replayed: recorded == [] and replayed is None,
    ),
    AllowedDifference(
        "**.amount_paid_off_stripe",
        "The spec marks this field required and the live API omits it from "
        "every recorded body (cassette 13); the spec's property set is the "
        "authority for shape here, so this world emits the required 0 and the "
        "pair is declared — the inverse of the usual live-only-echo entry.",
        predicate=lambda recorded, replayed: recorded is None and replayed == 0,
    ),
    AllowedDifference(
        "**.error.message",
        "The Invoice Item 404 names the id it could not find inside Stripe's "
        "own odd `(livemode=false)` spelling — the `**.id` rule where the id "
        "is the message's whole variable content (recorded, cassette 13).",
        predicate=_no_such_invoice_item_modulo_id,
    ),
    # --- the proration block (Phase 14, cassette 01) ---
    AllowedDifference(
        "**.subscription_item",
        "The id rule on the item reference a line's or an item's parent "
        "carries, predicated to `si_`-shaped pairs.",
        predicate=_both_prefixed_ids("si_"),
    ),
    AllowedDifference(
        "**.invoice_line_items[*]",
        "The id rule on the credited line-item back-links a proration credit "
        "carries, predicated to `il_`-shaped pairs.",
        predicate=_both_prefixed_ids("il_"),
    ),
    AllowedDifference(
        "**.description",
        "A proration description names the proration date's day — a "
        "clock-derived date inside a string, so the frozen-clock replay "
        "spells its own day where the recording spells record-time's. "
        "Predicated to exactly the two recorded proration forms on both "
        "sides, so every other description stays byte-exact.",
        predicate=_message_modulo_id(_PRORATION_DESCRIPTION),
    ),
    # The cancel-flow uncollectible collapse, scoped to the scenario that
    # records it: live answers the still-draft final invoice and marks it
    # uncollectible seconds later (both probe rounds); a frozen clock cannot
    # wait out the async mark, so the replayed body is already collapsed.
    AllowedDifference(
        "body.status",
        "The invoice_now cancel collapse: live's credit-only final invoice "
        "is a draft at read time and lands `uncollectible` asynchronously "
        "(~5 s, recorded in both probe rounds); this world's frozen clock "
        "collapses it inside the call — exactly one hop ahead, the "
        "dispute-settle precedent.",
        scenario="01_proration_half_cent",
        predicate=lambda recorded, replayed: (
            recorded in ("draft", "uncollectible") and replayed == "uncollectible"
        ),
    ),
]

# --- Structural differences ------------------------------------------------------


STRUCTURAL_DIFFERENCES: list[str] = [
    "Idempotency-key retention: real Stripe evicts a key once it is at least "
    "24 hours old — a floor, not a fixed TTL; this world's clock never "
    "advances, so a key never expires within a rollout (functional spec "
    "§6.1). No single replay can observe the difference.",
    "Search result freshness: real Stripe search lags writes; this world's "
    "search is exact (functional spec §3.3). Not exercised before the search "
    "phase; declared here so the difference is on record before it exists.",
    "The stripe-version response header: the real API echoes it and the tool "
    "return here is {status, body} with no header channel — an agent using "
    "Stripe's own MCP tools sees JSON, not HTTP, on both sides (functional "
    "spec §6.5).",
    "A malformed Stripe-Version is a request-header fault this world cannot "
    "express: one version is served, the version is not an agent-facing "
    "parameter, and the recorded behavior lives in cassette 03 rather than "
    "in an implementation (functional spec §6.5; scenario-scoped allow-list "
    "entries carry the replay difference).",
    "Scalar stringification at the form boundary: real Stripe form-encodes "
    "parameters, so a JSON number or boolean arrives as a string and fails "
    "validation as that string (email=5 -> 'Invalid email address: 5'); this "
    "surface takes JSON and type-checks it instead (functional spec §2.2 — "
    "no form encoding exists in this project). Only observable on type-fault "
    "requests, which the scenarios deliberately do not record.",
    "Stripe's nullable annotations are not authoritative where a recording "
    "contradicts them: at the pinned version a fresh customer carries "
    "default_source: null and invoice_settings.default_payment_method: null "
    "while the spec types both non-nullable (probed on the sandbox account, "
    "Phase 4, 2026-09-19). Schema conformance carries exactly two "
    "NULLABLE_DESPITE_SPEC entries on that evidence "
    "(tests/schema_conformance/validate.py); this is the general declaration.",
    "setup_intent.usage is read as a closed set (on_session / off_session) "
    "by this project's ruling — the spec types it a bare string whose "
    "description names two values without calling them exhaustive. The "
    "validator enforces the closed set and marks it DECLARED_OVERRIDES so "
    "the reading is never presented as documented fact (functional spec §4; "
    "spec/enums.py).",
    "Dunning's unpaid outcome leaves invoices in draft: two Stripe "
    "documentation pages say draft, while spec3.json's own prose says "
    "'closed', which was never a real invoice.status value. The docs win "
    "(functional spec §7); observable when the invoices phase's cassettes "
    "land, declared here now.",
    "Raw card numbers are accepted here and refused on the recording "
    "account: newer Stripe accounts block raw PANs by default ('Sending "
    "credit card numbers directly to the Stripe API is generally unsafe', "
    "recorded Phase 6), while the magic-card table is this world's spec'd "
    "failure-injection mechanism and needs the raw path. Cassettes create "
    "cards through tok_* tokens; unit tests exercise the numbers.",
    "The 54 payment rails the pruner drops are creatable and answered as "
    "`{}` under their own type key (recorded on klarna), exactly as "
    "components/data_model.md §3.9 stubs them; the pruned spec cannot "
    "declare those properties, so the schema-conformance validator carries "
    "the one declared stub exception (tests/schema_conformance/validate.py).",
    "A customer created or updated with a nonzero balance is stamped with "
    "the account default currency — the recording account's is 'cad', this "
    "world's single static account defaults to 'usd' until an account "
    "object exists (Phase 21). Probed both ways: balance=0 leaves currency "
    "null, and `currency` is not itself a settable parameter.",
    "Token card expiries are frozen at the recorded (9, 2027): real Stripe "
    "rolls a token's default expiry forward with wall time, and a frozen "
    "clock cannot follow (billing/magic_cards.py::TOKEN_EXPIRY).",
    "The catalog's stored-only fields: `coupon.applies_to`, "
    "`coupon.currency_options` and `price.currency_options` are accepted "
    "parameters that no recorded response body carries at the pinned "
    "version, and a tiered price's `tiers` never appears either (probed, "
    "Phase 7). They are stored for the billing phases to read and never "
    "serialized; an undeclared-in-response reading would fail schema "
    "conformance the other way.",
    "Rate float normalization: real Stripe serializes a whole-number rate "
    "as a float (`percent_off: 10.0`, `percentage: 20.0`), and this world "
    "emits the exact digits stored (`10`, `20`). Both are JSON numbers and "
    "compare equal under the replay diff; the textual difference is Ruby "
    "serialization, not a different value.",
    "Boolean list filters arrive as JSON booleans here and as form-encoded "
    "strings on the wire (`active=true`): the recorded refusals for "
    "string-typed booleans (`Invalid boolean: 'false'`) do not exist on this "
    "surface, which type-checks instead (functional spec §2.2 — the "
    "scalar-stringification declaration, narrowed to the catalog's filters).",
    "The map-parameter currency refusal: an unsupported or malformed key "
    "answers `Invalid currency: <key>. Stripe currently supports these "
    "currencies: …` with Stripe's full ordered 154-code list (probed live, "
    "Phase 7). This world reproduces the message from a constant transcribed "
    "from that same probe (`dispatch/params.py`). A cassette step would bind "
    "the transcription — the replay diffs Stripe's live message against the "
    "constant, exactly like any other verbatim message — and it is declined "
    "as a maintenance choice: the list churns with Stripe's currency "
    "support, and a committed step would turn every churn into a cassette "
    "re-record. The transcription's freshness is therefore this declared "
    "property, not a replay-checked one.",
    "Price amount XOR: a `unit_amount_decimal` together with any "
    "`currency_options[<cur>][unit_amount]` refuses on the real API (`You may "
    "only specify one of these parameters: …`, probed live, Phase 7); this "
    "world accepts and stores both. Recording the refusal would need a "
    "dedicated cassette step beside the legal carrier (an integer "
    "`unit_amount` with currency_options, which cassette 07 records), so the "
    "guard is declared here pending a later phase's recording slot.",
    "Method-mismatch responses: a verb a path does not carry answers 404 "
    "`Unrecognized request URL` on the real API (recorded in cassette 07 on "
    "DELETE /v1/prices/{price} and on an unknown path, Phase 7). The status "
    "and the quoted form match; the real message then continues with "
    "Stripe's docs/support pointers, which this world has no dashboard to "
    "source — the suffix is the predicated `**.error.message` entry above, "
    "not a silent pass.",
    "Dispute settlement is collapsed into the submitting call: live test "
    "mode answers `winning_evidence` with `under_review` and resolves the "
    "win asynchronously (~5 s, with `charge.dispute.funds_reinstated` then "
    "`charge.dispute.closed`), and `losing_evidence` / "
    "`escalate_inquiry_evidence` likewise. A frozen clock cannot wait out "
    "issuer review, and `won`/`lost` must be reachable — the refund gate, "
    "`is_charge_refundable` and the dispute eval all need them — so the "
    "magic strings settle synchronously and the submit response differs by "
    "exactly one status hop (scenario 05's scoped entries). `/close` is "
    "synchronous even live and needs no entry.",
    "Refund settlement events are not modeled: the live API emits a "
    "`refund.updated` + `charge.refund.updated` pair per refund when the "
    "acquirer reference becomes available (network timing), and flips "
    "`destination_details.card.reference_status` pending → available with "
    "it. This world's refunds never leave `pending`, so neither the events "
    "nor the flip exist here; the recorded bodies' available-state fields "
    "are predicated entries above.",
    "The async refund cards never transition: charging `4000000000007726` "
    "begins refunds `pending` (modeled — that is the documented initial "
    "state) which live settles to `succeeded`, and `4000000000005126` "
    "begins `succeeded` which live flips to `failed` with "
    "`refund.failed`. A frozen clock fires neither transition, and the "
    "bookkeeping counts settled refunds only (invariant I7), so a pending "
    "refund here is a permanently open state an agent may cancel inside "
    "the test-mode 30-minute window — which never closes either.",
    "Dispute evidence enrichment is not modeled: Stripe fills "
    "`evidence.customer_name`, `customer_email_address` and "
    "`product_description` from the customer record and the charge "
    "description without them being submitted (observed at the pinned "
    "version, timing varying by dispute track). This world stores "
    "submitted evidence verbatim; the predicated entries above carry the "
    "recorded bodies' enriched values.",
    "The payout success path is unrecordable on this account: the sandbox "
    "has no external account in any currency (every create answers `Sorry, "
    "you don't have any external accounts in that currency (cad).`), the "
    "recording key cannot add one (`POST /v1/accounts/{id}/external_accounts` "
    "→ 403 `more_permissions_required`), and top-ups are unsupported for its "
    "country (probed, Phase 11). The payout create/cancel/reverse success "
    "bodies, the draw-down and the wrong-state refusals are therefore "
    "spec-derived and unit-tested (`tests/test_payouts.py`), not "
    "cassette-pinned; cassette 11 carries every account-independent refusal "
    "the endpoint answers.",
    "The dispute fee rule the recordings pin, correcting functional spec §7: "
    "the settled-won dispute carries exactly two rows — the withdrawal "
    "(-amount, fee 1500, kept) and the reversal (+amount, fee 0) — with no "
    "separate countered-fee row and no fee refund on the win (cassette 05, "
    "re-read on cad-native rows in cassette 11). The spec's two-fee sentence "
    "is corrected in the same phase per the implementation plan's recipe.",
    "The plain-`currency` parameter refusal is transcribed from the live "
    "payout endpoint (Phase 11 probe) — the same maintenance choice as the "
    "map-parameter list above, and a different list: the payout spelling "
    "carries no `eurc`/`usdt`/`open_usd` and no trailing period. Its "
    "freshness is a declared property, not a replay-checked one.",
    "Instant payouts draw `instant_available`, a bucket no money path in "
    "this world fills; `method: instant` is accepted and draws the ordinary "
    "available balance instead. The `instant_available` bucket itself is "
    "omitted from /v1/balance while empty (spec-optional), so the "
    "substituted draw is unobservable through the tools.",
    "The resume flow's SetupIntent lifecycle: live mints it (at trial "
    "create without a payment method, reused by /resume) and it reads "
    "`canceled` moments later, refusing its own confirm — an async expiry "
    "a frozen clock cannot reproduce and no tool could survive. This "
    "world's seti is a real requires-confirmation row: confirming it pays "
    "the cycle invoice and applies the parked update (`paused` -> `active`, "
    "the documented mechanism), which is the unit-tested path the recording "
    "cannot carry (`tests/billing/test_subscription_machine.py`).",
    "The recorded pause collapses each item's period to the pause instant "
    "(`[trial_start, pause_moment)`); under a frozen clock that instant "
    "equals the trial start and the schema's "
    "`current_period_start < current_period_end` CHECK forbids a "
    "zero-length period, so the items keep their `[start, trial_end)` "
    "periods. The honest frozen-clock analog, declared here (Phase 12).",
    "The decline-card subscription creation is **not recorded**, and this "
    "round's probe closed the last apparent route to it: every decline-table "
    "token except `4000000000000341` refuses attach (probed, Phase 6), "
    "`…0341` has no token spelling (probed, Phase 12), and the raw PAN is "
    "refused by the recording account itself (402, 'Sending credit card "
    "numbers directly to the Stripe API is generally unsafe…' — Phase 6's "
    "declaration; re-confirmed against a subscription flow, Phase 12 CR "
    "round). No step on this account can reach a subscription whose first "
    "charge declines; recording one needs a host whose account enables raw "
    "card data. The `incomplete`-by-decline branch (open invoice, "
    "attempt_count advanced, `invoice.payment_failed`, sub `incomplete`) is "
    "spec-derived and unit-tested; cassette 12 carries the 3DS flavor, "
    "which the `tok_threeDSecure2Required` token reaches.",
    "A future `billing_cycle_anchor` on create is refused here until the "
    "proration phase: the stub-period first invoice bills a flexible-mode "
    "fraction (116 recorded for a $30/10-day stub — the recording "
    "account's own mode) whose arithmetic conformance scenario 1 exists to "
    "settle. `billing_cycle_anchor` on update is the same cut (`now` "
    "truncates and prorates).",
    "Subscription invoice events are deferred to the invoices phase: an "
    "event's data.object is the verbatim serialized object "
    "(cross_cutting §3.4.3), and the invoice serializer is Phase 13's. The "
    "invoice rows themselves are written now (creation, trial, resume, "
    "advance_cycle); only their `invoice.*` emissions wait.",
    "Item-level proration is the proration phase's span: item "
    "create/update/delete and `proration_behavior` are accepted and "
    "validated, and no proration lines are generated until Phase 14 lands "
    "`proration_lines` (implementation plan Phase 14: 'the behavior "
    "spanning Phases 12 and 13').",
    "The recorded pending_update's `billing_cycle_anchor` disagrees "
    "between recordings (null in cassette 12's resume body, a timestamp in "
    "the ad-hoc probe); this world parks the anchor the confirming "
    "SetupIntent will apply — `now` resolves to the resume instant, "
    "`unchanged` to the stored anchor — where the cassette parks null. The "
    "predicated `**.pending_update.billing_cycle_anchor` entry carries the "
    "pair the replay diff sees.",
    "`/v1/invoices/create_preview` and `/v1/invoices/{id}/attach_payment` "
    "stay unwired (Phase 13's declared scope cuts): the preview surface is a "
    "never-stored sub-API of its own (and `billing_reason: upcoming` is "
    "never stored, as ever), and attach_payment was not recorded this "
    "round. Dispatching either is an honest WorldBug naming the op_id, the "
    "same mid-build honesty every unwired route answers with.",
    "The invoiceitem create surface is narrowed to `amount`+`currency` "
    "(plus description/discountable/period/quantity/tax_rates/metadata/ "
    "invoice): `price_data`, `pricing`, `discounts`, `tax_code`, "
    "`tax_behavior`, `unit_amount_decimal`, `quantity_decimal`, "
    "`subscription` and `customer_account` are cut at the parameter layer "
    "(cassette 13 records the amount+currency mechanism, whose one-off "
    "price mint is reproduced). The invoice create/update surface likewise "
    "cuts the Connect/shipping/rendering/custom-fields/`from_invoice`/"
    "`automatically_finalizes_at`-parameter family, and `/pay` cuts "
    "`mandate`/`off_session`/`source`/`payment_method` — this world's pay "
    "resolves the recorded default-method chain (subscription default, "
    "then the customer's `invoice_settings`).",
    "`invoice_payment.paid` is not emitted: the `invoice_payment` object is "
    "out of scope (no payments sub-resource exists here), and the pair "
    "`invoice.payment_succeeded` + `invoice.paid` carries the payment the "
    "machine table names. `invoiceitem.updated` is not emitted either — "
    "it is absent from the closed 266-entry event set, so Stripe's own "
    "catalog emits none.",
    "The wrong-state invoice refusals are the recorded no-`code` spellings "
    "(cassette 13), correcting billing_engine's table: re-finalize answers "
    "\"This invoice is already finalized, you can't re-finalize a non-draft "
    'invoice.", delete non-draft "You can only delete draft invoices.", '
    'void/mark-uncollectible "You can only pass in open invoices. This '
    'invoice isn\'t open." (and re-mark "This invoice has already been '
    'marked uncollectible."), void-paid "Invoices with `paid` payments '
    'cannot be voided.", re-pay "Invoice is already paid", and the '
    "field-update-on-finalized family \"Finalized invoices can't be updated "
    'in this way" — `invoice_not_editable` survives only on the line '
    "endpoints' refusal, and `status_transition_invalid` nowhere. "
    "`metadata` updates succeed on any status (recorded on paid).",
    "Deleting a draft invoice does NOT release its swept invoice items "
    "back to pending (recorded, cassette 13 steps 23-24 and 62-63 — "
    "correcting billing_engine §2): they stay attached to the dead "
    "invoice id, refuse later deletes (\"Can't delete an invoice item that "
    'is attached to an invoice that is no longer editable"), read as '
    'deleted on update ("This invoice item has been deleted."), and '
    "`pending=true` no longer lists them. The `invoiceitems.invoice` FK "
    "was therefore dropped to a bare column — the recorded state outlives "
    "its parent row.",
    "The draft window's exact offsets are recorded facts, not the "
    "documented 'about an hour': `automatically_finalizes_at = created + "
    "3601s` (the ceil of the un-floored creation instant) and "
    "`next_payment_attempt = created + 3600s` (its floor) — one second "
    "apart on the wire (cassette 13, recorded twice). Recomputed from "
    "`created`, never from the update moment, when `auto_advance` is "
    "cleared and re-set on a draft.",
    "An unparameterized manual create excludes pending invoice items "
    "(recorded, cassette 13 step 11 — the API's `include` default is a "
    "dashboard concept); `include` sweeps the customer's pending items "
    "matching the invoice's currency, regardless of period — the invoice's "
    "own zero-width period bounds nothing (correcting billing_engine "
    "§3.1.1's period-bounded sweep), and one currency per invoice is "
    "Stripe's own invariant, so mismatched items stay pending for a later "
    "invoice of their own (the currency bound is unprobed and declared; "
    "the unparameterized currency follows the newest pending item). The "
    "swept order is newest-first, and a later `add_lines` line is simply "
    "the newest pending item — the spec's three-bucket line order collapses "
    "to two (invoice items newest-first, then subscription items "
    "newest-first).",
    "Line `quantity` is a multiplier, not a no-op (CR round's ruling): a "
    "line bills its row's unit amount x quantity, so a quantity-only edit "
    "on `update_lines`/`lines/{id}` recomputes the line and an "
    "invoiceitem create's quantity multiplies through the sweep. On "
    "`add_lines` quantity can never multiply — beside `amount` it is the "
    "recorded XOR refusal and alone it has no unit amount (live pairs it "
    "with `price_data`, which this surface cuts), so the binder's "
    "missing-parameter refusal answers a quantity-only add. The one-off "
    "price/product mint behind an `amount`+`currency` item emits the "
    "catalog's own `product.created`/`price.created` pair (the rows are "
    "ordinary catalog rows, product-first per cassette 07); an amount "
    "edit's re-mint writes only the price against the SAME product "
    "(recorded, cassette 13 step 33) and emits `price.created` alone.",
    "Every draft-mutating path rebuilds the lines and emits "
    "`invoice.updated` on change — `add_lines`, `update_lines`, "
    "`remove_lines`, the single-line `lines/{id}` update, and an "
    "invoiceitem create with `invoice=`. The family's invoice-level "
    "emission is consistent-with-siblings-unprobed: the cassette pins the "
    "bodies, not the events, and `invoice.updated` fires only when the "
    "serialized body actually changed (a no-op edit answers 200 and "
    "writes no event row).",
    "`/pay` on a draft finalizes and collects inside the one call "
    "(recorded, cassette 13 step 25), and `paid_out_of_band: true` settles "
    "without an attempt at all — `attempted` stays false (step 61). A $0 "
    "finalization settles to `paid` with `attempted: true, attempt_count: "
    "0` inside the finalizing call (steps 36/43/49/57); a nonzero plain "
    "finalize attempts nothing (`attempted` stays false, step 45) — "
    "collection is the auto-advance machinery's business, which a frozen "
    "clock never runs. Paying flips `auto_advance` false (step 62); "
    "finalizing preserves it (step 60).",
    "The invoice body's account echoes and PDF artifacts: `rendering` is "
    "null here (the recording account's dashboard default object is "
    "account state), `account_country`/`account_name` come from the "
    "instance's static account config ('US'/null by default — Phase 21 "
    "may make them configurable), `webhooks_delivered_at` mirrors "
    "`created`, `hosted_invoice_url`/`invoice_pdf` are deterministic "
    "id-shaped URLs from finalization on, and `payments`/`threshold_reason`/"
    "`confirmation_secret` are omitted (none is ever non-empty here). "
    "`parent.subscription_details.metadata` is the subscription's own "
    "metadata, not a separately stored finalization snapshot (no second "
    "copy exists; `{}` when the subscription carries none).",
    # --- Phase 14 (cassette 01 and its probe trails) ---
    "Proration lines round by FLOORING each line's exact rational "
    "(recorded, cassette 01: -2.5 -> -3 and +4.5 -> +4 at a fraction of "
    "exactly 1/400; the documented -666.67 -> -667 agrees) — correcting "
    "billing_engine's original round-half-up assumption, which no "
    "recording ever supported. The fee rule (half-up, documented) and the "
    "coupon apportionment (floor-then-remainder, documented) are "
    "untouched; three rules, three functions.",
    "The minimum chargeable is 50 minor units, a declared world constant "
    "for the two-decimal currencies this world bills (recorded EFFECT, "
    "cassette 01: nets of 1 and 4 are never charged — they roll onto "
    "customer.balance through `invoice_too_small` rows and settle "
    "`attempted: true, attempt_count: 0`). The real API's by-currency "
    "minimum table is out of scope. The roll also runs BEFORE payment-"
    "method resolution: nothing is charged, so no method is needed — an "
    "invoice of 30 cents settles paid on a customer with no card, where "
    "the 50-and-up path would report no_payment_method. Every recording "
    "of the roll carried a card, so the no-PM ordering is this "
    "declaration, not a recording.",
    "The invoice_now cancel's credit-only final invoice is marked "
    "uncollectible asynchronously live (~5 s, both probe rounds) and "
    "synchronously here — the dispute-settle precedent, one hop ahead. "
    "The scenario-scoped `body.status` entry carries the replay pair. "
    "`invoice_now=true` with nothing to bill (no `prorate`, or a zero "
    "remainder) mints NO invoice at all — Stripe's final invoice bills "
    "the outstanding amount, and an empty invoice marked uncollectible "
    "would be a stretch of the credit-only recording, not an application "
    "of it (unprobed; declared here).",
    "The classic-mode `billing_cycle_anchor: now` update shape is "
    "unrecordable: the recording account is dashboard-flexible and answers "
    "a stub-debit shape instead (probe_proration_invoices.json carries it "
    "for reference). This world serves the machine table's classic ruling "
    "(truncate, prorate per proration_behavior, roll the periods and the "
    "anchor); under the frozen clock the reset instant can even equal the "
    "creation instant, making the restart a byte-identical no-op.",
    "Dunning's retry SCHEDULE is unrecordable on this account (the "
    "decline-card structural declaration above closes the only route to a "
    "declining subscription charge): the counter semantics, the nine "
    "hard-decline codes and the three end-of-schedule outcomes are "
    "spec-derived and unit-tested, and `next_payment_attempt`'s spacing "
    "is this world's declared even-spacing stand-in — never a guess at "
    "Stripe's ML schedule, which no one outside Stripe has.",
    "`highest_risk_level` is docs-listed among the nine hard-decline "
    "codes but absent from the pinned spec's 50-value `decline_code` "
    "enumeration — a real docs/spec divergence. The docs win for the "
    "gating list and tests/billing/test_dunning.py pins the divergence "
    "by name.",
]

# --- Matching --------------------------------------------------------------------

_SEGMENT_CACHE: dict[str, re.Pattern[str]] = {}


def _segment(pattern_segment: str) -> re.Pattern[str]:
    """One pattern segment as a regex: only `*` is a wildcard (within the
    segment); everything else — including a literal `data[3]` — is escaped,
    because fnmatch would read the brackets as a character class and a
    bracketed pattern segment would then match nothing at all."""
    compiled = _SEGMENT_CACHE.get(pattern_segment)
    if compiled is None:
        compiled = re.compile(re.escape(pattern_segment).replace(re.escape("*"), "[^.]*"))
        _SEGMENT_CACHE[pattern_segment] = compiled
    return compiled


def match_path(pattern: str, path: str) -> bool:
    """True when `path` (the diff walker's dotted/bracketed path) matches
    `pattern`. `**` consumes any number of segments; every other segment is
    matched with `*` as its only wildcard (`*` alone matches exactly one
    segment, `*_at` a suffix, `data[3]` only itself)."""
    pat = pattern.split(".")
    seg = path.split(".")

    def walk(index_pat: int, index_seg: int) -> bool:
        if index_pat == len(pat):
            return index_seg == len(seg)
        if pat[index_pat] == "**":
            # `**` at the end matches everything remaining; otherwise try
            # consuming 0..n segments.
            return any(walk(index_pat + 1, i) for i in range(index_seg, len(seg) + 1))
        if index_seg == len(seg):
            return False
        return bool(_segment(pat[index_pat]).fullmatch(seg[index_seg])) and walk(
            index_pat + 1, index_seg + 1
        )

    return walk(0, 0)


def allowed(path: str, recorded: Any, replayed: Any, scenario: str) -> AllowedDifference | None:
    """The entry that permits this difference, if one does.

    Global entries apply everywhere; a scenario-scoped entry applies only
    when its scenario name matches. A predicate entry permits the difference
    only while the predicate holds — a path being listed is not enough."""
    for entry in ALLOWED_DIFFERENCES:
        if entry.scenario is not None and entry.scenario != scenario:
            continue
        if not match_path(entry.path, path):
            continue
        if entry.predicate is not None and not entry.predicate(recorded, replayed):
            continue
        return entry
    return None
