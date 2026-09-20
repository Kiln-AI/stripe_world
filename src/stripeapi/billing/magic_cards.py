"""The magic-value table: test-mode failure injection as data.

Transcribed from
[`magic-card-table.md`](../../../specs/projects/stripe_world/research/stripe-billing-and-payments/test-mode-clocks-and-prior-art/magic-card-table.md),
which is itself primary-sourced from Stripe's testing documentation. A payment
method created from one of these numbers or tokens carries the behavior the
value implies: `payment_methods.x_behavior` stores the charge-time tag
(`card_declined:generic_decline`, `dispute:fraudulent`, …) for the money-path
phases to read, and creation/attachment refuse where Stripe refuses.

Card *fingerprints* here are a deterministic digest of the number — real
Stripe derives them per account and they are stable per number, which a
content digest reproduces without `ctx`: the same number gives the same
fingerprint in every instance and every replay, which is the property that
matters (declared in the conformance allow-list, where the recorded values
differ).

The raw-number path could not be recorded at the pinned version: the
recording account refuses raw card data ("Sending credit card numbers
directly to the Stripe API is generally unsafe…", probed 2026-09-19), so the
token family — `tok_*` and `pm_card_*` — is what the cassettes exercise and
what pins the response shapes. This world still accepts raw numbers, because
the magic-card table is the spec'd failure-injection mechanism and tokens are
not a replacement for every row (the numbers behind the tokens are what an
eval's fixture writes). That split is a declared structural difference.
"""

import hashlib
import re
import string
from dataclasses import dataclass

__all__ = [
    "ATTACH_REFUSED_ENVELOPE",
    "TOKEN_EXPIRY",
    "CardBehavior",
    "card_for",
    "fingerprint",
    "is_pan",
    "luhn_valid",
    "number_for_token",
]


@dataclass(frozen=True, slots=True)
class CardBehavior:
    """What creating and charging one test card does.

    `charge_decline` is `(code, decline_code)` — `None` means the card
    succeeds. `attach_declines` marks the numbers Stripe refuses to attach to
    a customer at all (every decline-table card except `4000…0341`, the
    "attachable decline"). The dispute / refund / payout tags carry no
    behavior yet: their phases consume them.
    """

    number: str
    brand: str
    funding: str
    display_brand: str | None = None
    country: str = "US"
    charge_decline: tuple[str, str | None] | None = None
    attach_declines: bool = False
    cvc_check: str = "unchecked"
    dispute: str | None = None
    refund_async: str | None = None
    three_d_secure: str | None = None


_ALPHABET = string.ascii_letters + string.digits

#: The ACH test-account numbers that carry charge-time behavior (§7 of the
#: magic table). Tagged onto `payment_methods.x_behavior` at creation;
#: consumed by the money-path phases.
US_BANK_BEHAVIORS = {
    "000111111116": "us_bank=no_account",
    "000111111113": "us_bank=account_closed",
    "000222222227": "us_bank=insufficient_funds",
    "000333333335": "us_bank=debit_not_authorized",
    "000444444440": "us_bank=invalid_currency",
    "000666666661": "us_bank=fail_microdeposits",
    "000555555559": "us_bank=dispute",
    "000000000009": "us_bank=processing",
    "000777777771": "us_bank=weekly_limit_exceeded",
    "000888888885": "us_bank=deactivated",
}

#: Decline cards (§1 of the table). Attach refuses for all but `…0341`,
#: probed at the pinned version: attaching `tok_visa_chargeDeclined` answers
#: 402 `card_declined` / `generic_decline`, while `…0341` attaches and only
#: declines when charged.
_DECLINES = {
    "4000000000000002": ("card_declined", "generic_decline"),
    "4000000000009995": ("card_declined", "insufficient_funds"),
    "4000000000009987": ("card_declined", "lost_card"),
    "4000000000009979": ("card_declined", "stolen_card"),
    "4000000000000069": ("expired_card", None),
    "4000000000000127": ("incorrect_cvc", None),
    "4000000000000119": ("processing_error", None),
    "4000000000006975": ("card_declined", "card_velocity_exceeded"),
}

#: Success cards by brand (§2). Number -> (brand, funding).
_SUCCESS = {
    "4242424242424242": ("visa", "credit"),
    "4000056655665556": ("visa", "debit"),
    "5555555555554444": ("mastercard", "credit"),
    "2223003122003222": ("mastercard", "credit"),
    "5200828282828210": ("mastercard", "debit"),
    "5105105105105100": ("mastercard", "prepaid"),
    "378282246310005": ("amex", "credit"),
    "371449635398431": ("amex", "credit"),
    "6011111111111117": ("discover", "credit"),
    "6011000990139424": ("discover", "credit"),
    "6011981111111113": ("discover", "debit"),
    "3056930009020004": ("diners", "credit"),
    "36227206271667": ("diners", "credit"),
    "3566002020360505": ("jcb", "credit"),
    "6200000000000005": ("unionpay", "credit"),
    "6200000000000047": ("unionpay", "debit"),
    "6205500000000000004": ("unionpay", "credit"),
}

_3DS = {
    "4000002500003155": "authentication_required",
    "4000002760003184": "authentication_required",
    "4000003800000446": None,  # already set up for off-session use
    "4000008400001629": "authentication_required",  # authenticates, then declines
    "4000000000003220": "authentication_required",
    "4000000000003055": None,  # 3DS supported, not required
}

_DISPUTES = {
    "4000000000000259": "fraudulent",
    "4000000000002685": "product_not_received",
    "4000000000001976": "fraudulent",  # inquiry
    "4000000000005423": "fraudulent",  # early fraud warning
    "4000004040000079": "fraudulent",  # multiple disputes
}

_REFUNDS = {
    "4000000000007726": "succeeded",
    "4000000000005126": "failed",
}

#: The attachable decline (§1's footnote): attaches like a success card, then
#: declines every charge.
_ATTACHABLE_DECLINE = "4000000000000341"

#: `tok_*` / `pm_card_*` families (§10) mapped onto the numbers whose behavior
#: they carry. `pm_card_*` names rejected as `card[token]` on the recording
#: account are still accepted here: both spellings name the same test card and
#: the world has no reason to prefer one.
_TOKENS = {
    "tok_visa": "4242424242424242",
    "tok_visa_debit": "4000056655665556",
    "tok_mastercard": "5555555555554444",
    "tok_amex": "378282246310005",
    "tok_discover": "6011111111111117",
    "tok_diners": "3056930009020004",
    "tok_jcb": "3566002020360505",
    "tok_unionpay": "6200000000000005",
    "tok_visa_chargeDeclined": "4000000000000002",
    "tok_visa_chargeDeclinedInsufficientFunds": "4000000000009995",
    "tok_visa_chargeDeclinedLostCard": "4000000000009987",
    "tok_visa_chargeDeclinedStolenCard": "4000000000009979",
    "tok_chargeDeclinedExpiredCard": "4000000000000069",
    "tok_chargeDeclinedIncorrectCvc": "4000000000000127",
    "tok_chargeDeclinedProcessingError": "4000000000000119",
    "tok_visa_chargeDeclinedVelocityLimitExceeded": "4000000000006975",
    "tok_card_threeDSecure2Required": "4000000000003220",
    "tok_card_threeDSecureRequiredChargeDeclined": "4000008400001629",
    "tok_card_threeDSecureOptional": "4000000000003055",
    "tok_card_authenticationRequired": "4000002500003155",
    "tok_card_createDispute": "4000000000000259",
    "tok_card_createDisputeProductNotReceived": "4000000000002685",
    "tok_card_createDisputeInquiry": "4000000000001976",
    "pm_card_visa": "4242424242424242",
    "pm_card_visa_debit": "4000056655665556",
    "pm_card_mastercard": "5555555555554444",
    "pm_card_amex": "378282246310005",
    "pm_card_discover": "6011111111111117",
    "pm_card_diners": "3056930009020004",
    "pm_card_jcb": "3566002020360505",
    "pm_card_unionpay": "6200000000000005",
    "pm_card_visa_chargeDeclined": "4000000000000002",
    "pm_card_visa_chargeDeclinedInsufficientFunds": "4000000000009995",
    "pm_card_visa_chargeDeclinedLostCard": "4000000000009987",
    "pm_card_visa_chargeDeclinedStolenCard": "4000000000009979",
    "pm_card_chargeDeclinedExpiredCard": "4000000000000069",
    "pm_card_chargeDeclinedIncorrectCvc": "4000000000000127",
    "pm_card_chargeDeclinedProcessingError": "4000000000000119",
    "pm_card_visa_chargeDeclinedVelocityLimitExceeded": "4000000000006975",
    "pm_card_threeDSecure2Required": "4000000000003220",
    "pm_card_threeDSecureRequiredChargeDeclined": "4000008400001629",
    "pm_card_threeDSecureOptional": "4000000000003055",
    "pm_card_authenticationRequired": "4000002500003155",
    "pm_card_createDispute": "4000000000000259",
    "pm_card_createDisputeProductNotReceived": "4000000000002685",
    "pm_card_createDisputeInquiry": "4000000000001976",
    "pm_card_cvcCheckFail": "4242424242424242",
    "pm_card_riskLevelHighest": "4242424242424242",
    "pm_card_riskLevelElevated": "4242424242424242",
}

#: Every token-created card answers with this expiry (recorded at the pinned
#: version, 2026-09-19). Real Stripe rolls token expiries forward with wall
#: time; a frozen literal keeps replays deterministic. Declared difference.
TOKEN_EXPIRY = (9, 2027)

#: The envelope an attachment refusal answers with, probed verbatim at the
#: pinned version on `tok_visa_chargeDeclined`. `advice_code` /
#: `network_decline_code` arrived on the same recording but are network
#: chatter this world does not model; their omission is a scenario-scoped
#: allow-list entry, not silence.
ATTACH_REFUSED_ENVELOPE = {
    "code": "card_declined",
    "decline_code": "generic_decline",
    "message": "Your card was declined.",
}

# BIN prefixes for a Luhn-valid number the table does not name: an eval may
# invent a card, and the brand still has to be plausible. Ordered longest
# prefix first.
_BINS: tuple[tuple[str, str], ...] = (
    ("6011", "discover"),
    ("34", "amex"),
    ("37", "amex"),
    ("35", "jcb"),
    ("36", "diners"),
    ("30", "diners"),
    ("62", "unionpay"),
    ("50", "mastercard"),
    ("5", "mastercard"),
    ("2", "mastercard"),
    ("4", "visa"),
)

_CVC_FAIL_TOKENS = frozenset({"pm_card_cvcCheckFail"})


def fingerprint(number: str) -> str:
    """A stable 16-character fingerprint for one card number.

    A digest of the number, not a draw from `ctx.ids`: the real API answers
    the same fingerprint for the same number, and per-instance randomness
    would break that.
    """
    digest = hashlib.sha256(f"pm-card-fingerprint:{number}".encode()).digest()
    value = int.from_bytes(digest[:12], "big")
    chars = []
    while len(chars) < 16:
        value, index = divmod(value, len(_ALPHABET))
        chars.append(_ALPHABET[index])
    return "".join(chars)


def luhn_valid(number: str) -> bool:
    digits = [int(c) for c in number]
    checksum = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def number_for_token(token: str) -> str | None:
    return _TOKENS.get(token)


def card_for(number: str, *, cvc_provided: bool = False, token: str | None = None) -> CardBehavior:
    """The behavior one card number carries.

    An unknown but Luhn-valid number is a success card branded by BIN — the
    table's rows are the interesting cards, not the only legal ones.
    """
    brand: str | None = None
    funding = "credit"
    decline: tuple[str, str | None] | None = None
    attach_declines = False
    dispute = None
    refund_async = None
    three_d = None
    cvc_check = "pass" if cvc_provided else "unchecked"

    if number in _DECLINES:
        code, decline_code = _DECLINES[number]
        decline = (code, decline_code)
        attach_declines = True
        brand = "visa"
        funding = "credit"
    elif number == _ATTACHABLE_DECLINE:
        decline = ("card_declined", "generic_decline")
        brand = "visa"
    elif number in _SUCCESS:
        brand, funding = _SUCCESS[number]
    else:
        for prefix, by_brand in _BINS:
            if number.startswith(prefix):
                brand = by_brand
                break

    if number in _3DS:
        three_d = _3DS[number]
    if number in _DISPUTES:
        dispute = _DISPUTES[number]
    if number in _REFUNDS:
        refund_async = _REFUNDS[number]
    if token in _CVC_FAIL_TOKENS and cvc_provided:
        cvc_check = "fail"

    return CardBehavior(
        number=number,
        brand=brand or "visa",
        funding=funding,
        display_brand=brand or "visa",
        country="US",
        charge_decline=decline,
        attach_declines=attach_declines,
        cvc_check=cvc_check,
        dispute=dispute,
        refund_async=refund_async,
        three_d_secure=three_d,
    )


_PAN = re.compile(r"^\d{12,19}$")


def is_pan(value: str) -> bool:
    return bool(_PAN.fullmatch(value))
