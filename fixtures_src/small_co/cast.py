"""Wrenfield Labs' cast: 45 hand-written customers, in components/small_co.md §4's order.

Every instant is UTC. A member's first beats (its card, then its subscription) sit at its
`created` instant; `story.schedule` creates the customer there first. The driver does the
renewals, trial ends, dunning and expiry; this file says only what people chose to do.
"""

from dataclasses import dataclass

from fixtures_src.replay import cards
from fixtures_src.replay.cards import Card as TestCard

__all__ = [
    "CAST",
    "Beat",
    "BillPending",
    "CancelAtPeriodEnd",
    "CancelNow",
    "Card",
    "ChangePlan",
    "InvoiceItem",
    "Member",
    "OneOffPayment",
    "Refund",
    "Subscribe",
    "t",
]


def t(minute: str) -> str:
    """`2026-03-02T15:45` -> the canonical `2026-03-02T15:45:00.000Z`."""
    return f"{minute}:00.000Z"


@dataclass(frozen=True)
class Card:
    """Add a card and make it the customer's invoice default."""

    at: str
    card: TestCard
    exp: tuple[int, int]


@dataclass(frozen=True)
class Subscribe:
    at: str
    price: str  # a catalog key
    trial_days: int | None = None
    coupon: str | None = None


@dataclass(frozen=True)
class ChangePlan:
    at: str
    price: str
    proration: str


@dataclass(frozen=True)
class CancelAtPeriodEnd:
    at: str
    feedback: str
    comment: str | None = None


@dataclass(frozen=True)
class CancelNow:
    at: str
    feedback: str
    comment: str | None = None


@dataclass(frozen=True)
class Refund:
    """Refund the member's latest succeeded charge, in full or by `amount` cents."""

    at: str
    amount: int | None = None


@dataclass(frozen=True)
class InvoiceItem:
    at: str
    price: str


@dataclass(frozen=True)
class BillPending:
    """Invoice the member's pending items now, on their own invoice, and pay it."""

    at: str


@dataclass(frozen=True)
class OneOffPayment:
    """A payment intent confirmed with the member's default card."""

    at: str
    amount: int
    description: str


Beat = (
    Card
    | Subscribe
    | ChangePlan
    | CancelAtPeriodEnd
    | CancelNow
    | Refund
    | InvoiceItem
    | BillPending
    | OneOffPayment
)


@dataclass(frozen=True)
class Member:
    key: str
    name: str
    email: str | None
    description: str
    created: str
    beats: tuple[Beat, ...]


VISA = cards.GOOD[0]  # 4242
VISA_DEBIT = cards.GOOD[1]  # 5556
MASTERCARD = cards.GOOD[2]  # 4444
AMEX = cards.GOOD[4]
DISCOVER = cards.GOOD[5]
TRIAL = 14


def _m(
    key: str,
    name: str,
    description: str,
    email: str,
    created: str,
    card: tuple[TestCard, tuple[int, int]] | None,
    *beats: Beat,
    price: str | None = None,
    trial: bool = False,
    coupon: str | None = None,
) -> Member:
    """A member: customer at `created`, then the card, then (with `price`) the subscription, all
    at `created`, then the later beats."""
    at = t(created)
    opening: list[Beat] = []
    if card is not None:
        opening.append(Card(at, card[0], card[1]))
    if price is not None:
        opening.append(Subscribe(at, price, TRIAL if trial else None, coupon))
    return Member(key, name, email, description, at, (*opening, *beats))


_founding = "founding_annual"
_L = "LAUNCH"
_T = "TEACHWELL25"

CAST: tuple[Member, ...] = (
    # --- Founding members ----------------------------------------------------------------------
    _m("daniela_ruiz", "Daniela Ruiz", "Violin teacher", "daniela@ruizviolin.com",
       "2025-09-03T16:20", (VISA, (11, 2028)), price=_founding),
    _m("marcus_bell", "Marcus Bell", "SAT and ACT tutor", "marcus@bellprep.net",
       "2025-09-04T13:05", (MASTERCARD, (3, 2027)), price=_founding),
    _m("priya_natarajan", "Priya Natarajan", "Piano teacher", "priya@natarajanpiano.com",
       "2025-09-05T18:40", (AMEX, (7, 2028)),
       CancelAtPeriodEnd(t("2026-08-11T20:15"), "other", "Retiring from teaching"),
       price=_founding),
    _m("owen_fitzgerald", "Owen Fitzgerald", "Running coach", "owen@fitzrunning.co",
       "2025-09-06T14:10", (VISA, (8, 2026)),
       Card(t("2026-08-18T17:45"), cards.DECLINING, (8, 2030)),
       price=_founding),
    _m("hana_kobayashi", "Hana Kobayashi", "Math tutor", "hana@kobayashimath.com",
       "2025-09-07T01:30", (DISCOVER, (2, 2029)), price=_founding),
    _m("tomasz_wojcik", "Tomasz Wójcik", "Guitar teacher", "tomasz@wojcikguitar.com",
       "2025-09-08T22:15", (VISA_DEBIT, (5, 2027)), price=_founding),
    # --- Launch day and the LAUNCH wave -------------------------------------------------------
    _m("founder_test", "Test (do not bill)", "Founder test account", "test+launch@wrenfield.app",
       "2026-03-02T13:02", (VISA, (12, 2030)),
       OneOffPayment(t("2026-03-02T13:05"), 100, "Launch smoke test"),
       Refund(t("2026-03-02T13:20"))),
    _m("amara_osei", "Amara Osei", "Reading tutor", "amara@oseireading.com",
       "2026-03-02T15:45", (VISA, (6, 2028)), price="solo_monthly", coupon=_L),
    _m("ben_carlisle", "Ben Carlisle", "Guitar teacher", "ben@carlisleguitar.com",
       "2026-03-02T19:10", (VISA, (10, 2027)),
       CancelAtPeriodEnd(t("2026-06-15T02:00"), "too_expensive"),
       price="solo_monthly", coupon=_L),
    _m("lucas_moreau", "Lucas Moreau", "Cello teacher", "lucas@moreaucello.com",
       "2026-03-03T02:10", (MASTERCARD, (1, 2029)), price="solo_monthly", coupon=_L),
    _m("tom_okonkwo", "Tom Okonkwo", "Riverbend Music School", "tom@riverbendmusic.org",
       "2026-03-03T15:30", (AMEX, (9, 2027)), price="studio_monthly", coupon=_L),
    _m("sarah_goldberg", "Sarah Goldberg", "LSAT tutor", "sarah@goldberglsat.com",
       "2026-03-04T17:25", (AMEX, (4, 2028)), price="solo_monthly", coupon=_L),
    _m("anika_sharma", "Anika Sharma", "Sharma Tutoring Collective", "anika@sharmatutoring.com",
       "2026-03-04T21:29", (MASTERCARD, (12, 2027)),
       Subscribe(t("2026-03-04T21:30"), "studio_monthly", coupon=_L),
       # Neither subscription creation nor a renewal sweeps pending items in this world, so
       # the setup fee is invoiced on its own right after she subscribes.
       InvoiceItem(t("2026-03-04T21:31"), "setup"),
       BillPending(t("2026-03-04T21:32"))),
    _m("olivia_grant", "Olivia Grant", "SAT tutor", "olivia@grantprep.com",
       "2026-03-05T14:00", (MASTERCARD, (5, 2028)),
       CancelNow(t("2026-05-20T19:00"), "switched_service"),
       price="solo_monthly", coupon=_L),
    _m("diego_herrera", "Diego Herrera", "Youth soccer coach", "diego@herrerafutbol.com",
       "2026-03-05T23:50", (VISA_DEBIT, (2, 2027)), price="solo_monthly", coupon=_L),
    _m("mei_lin", "Mei Lin", "Mandarin tutor", "mei@linmandarin.com",
       "2026-03-06T12:15", (VISA, (8, 2028)), price="solo_monthly"),
    _m("ellen_park", "Ellen Park", "Yoga teacher", "ellen@parkyoga.studio",
       "2026-03-07T16:00", (AMEX, (11, 2027)), price="solo_annual", coupon=_L),
    _m("paolo_ricci", "Paolo Ricci", "Ricci Voice Studio", "paolo@riccivoice.com",
       "2026-03-08T19:00", (VISA, (7, 2029)), price="studio_annual", coupon=_L),
    # --- After launch -------------------------------------------------------------------------
    _m("marco_bianchi", "Marco Bianchi", "Italian tutor", "marco@bianchiitaliano.com",
       "2026-04-03T17:00", (VISA, (3, 2028)),
       CancelNow(t("2026-04-12T15:30"), "unused"),
       price="solo_monthly", trial=True),
    _m("elena_petrova", "Elena Petrova", "Petrova Dance Academy", "elena@petrovadance.com",
       "2026-04-10T17:00", (MASTERCARD, (6, 2027)),
       InvoiceItem(t("2026-08-28T14:30"), "setup"),
       price="studio_monthly"),
    _m("jordan_blake", "Jordan Blake", "Personal trainer", "jordan@blakestrength.com",
       "2026-04-14T20:40", (MASTERCARD, (9, 2028)), price="solo_monthly", trial=True),
    _m("yuki_tanaka", "Yuki Tanaka", "Art teacher", "yuki@tanakaart.studio",
       "2026-04-18T21:20", (AMEX, (1, 2028)),
       CancelAtPeriodEnd(t("2026-07-30T16:00"), "too_expensive"),
       price="studio_monthly", trial=True),
    _m("claire_dubois", "Claire Dubois", "Dubois Language Studio", "claire@duboislanguages.com",
       "2026-04-22T18:45", (VISA, (10, 2028)), price="studio_monthly", trial=True),
    _m("rachel_stein", "Rachel Stein", "Test-prep tutor", "rachel@steintutoring.com",
       "2026-04-27T02:30", None),
    _m("leo_marchetti", "Leo Marchetti", "Chemistry tutor", "leo@marchettichem.com",
       "2026-04-30T10:00", (VISA, (4, 2027)),
       Card(t("2026-08-20T18:30"), cards.DECLINING, (11, 2029)),
       price="solo_monthly"),
    _m("chris_nguyen", "Chris Nguyen", "Math and science tutoring", "chris@nguyentutoring.com",
       "2026-05-05T19:30", (VISA, (2, 2028)), price="solo_monthly"),
    _m("nadia_haddad", "Nadia Haddad", "French tutor", "nadia@haddadfrench.com",
       "2026-05-12T15:00", (MASTERCARD, (7, 2027)),
       ChangePlan(t("2026-08-20T16:00"), "solo_monthly", "always_invoice"),
       price="studio_monthly"),
    _m("rosa_delgado", "Rosa Delgado", "Spanish tutor", "rosa@delgadospanish.com",
       "2026-05-15T22:10", (VISA, (12, 2028)),
       CancelAtPeriodEnd(t("2026-07-01T01:00"), "other", "Moved to an in-person school"),
       price="solo_monthly"),
    _m("fatima_rahman", "Fatima Rahman", "Biology tutor", "fatima@rahmanbio.com",
       "2026-05-21T14:05", (VISA, (5, 2029)), price="solo_monthly", trial=True),
    _m("june_harper", "June Harper", "Harper Swim School", "june@harperswim.com",
       "2026-05-28T13:35", (AMEX, (3, 2028)), price="studio_annual"),
    _m("ravi_iyer", "Ravi Iyer", "Coding tutor for kids", "ravi@iyercodes.com",
       "2026-06-03T03:20", (MASTERCARD, (8, 2027)), price="solo_annual"),
    _m("sam_rivera", "Sam Rivera", "Peak Climbing Coaching", "sam@peakclimbcoach.com",
       "2026-06-09T16:05", (VISA_DEBIT, (6, 2028)), price="studio_monthly"),
    _m("chris_nguyen_2", "Chris Nguyen", "Tutoring", "chris.nguyen@cnlessons.com",
       "2026-06-14T20:00", None,
       CancelNow(t("2026-06-17T12:00"), "other", "Duplicate account"),
       price="solo_monthly", trial=True),
    _m("victor_alves", "Victor Alves", "Swim coach", "victor@alvesswim.com",
       "2026-06-22T16:40", (cards.DECLINING, (9, 2028)), price="solo_monthly"),
    _m("jamal_carter", "Jamal Carter", "Drum teacher", "jamal@carterdrums.com",
       "2026-07-10T21:00", (VISA, (1, 2029)),
       ChangePlan(t("2026-07-17T15:30"), "solo_annual", "none"),
       Refund(t("2026-07-17T15:31"), 17000),
       price="studio_annual"),
    _m("kofi_mensah", "Kofi Mensah", "Chess coach", "kofi@mensahchess.com",
       "2026-07-15T18:30", (VISA, (7, 2028)), price="solo_monthly", coupon=_T),
    _m("dev_patel", "Dev Patel", "Physics tutor", "dev@patelphysics.com",
       "2026-07-16T20:45", (VISA, (10, 2027)),
       CancelNow(t("2026-08-25T15:00"), "unused"),
       price="solo_monthly", coupon=_T),
    _m("isabel_santos", "Isabel Santos", "Flute teacher", "isabel@santosflute.com",
       "2026-07-22T13:50", (DISCOVER, (4, 2028)), price="solo_monthly", coupon=_T),
    _m("hannah_weber", "Hannah Weber", "German tutor", "hannah@weberdeutsch.com",
       "2026-07-28T10:10", (VISA, (9, 2029)), price="solo_annual", coupon=_T),
    _m("adaeze_okafor", "Adaeze Okafor", "Piano teacher", "adaeze@okaforpiano.com",
       "2026-08-01T18:00", None, price="solo_monthly", trial=True),
    _m("malik_ortiz", "Malik Ortiz", "Brass teacher", "malik@ortizbrass.com",
       "2026-08-09T23:05", None),
    _m("sofia_lindqvist", "Sofia Lindqvist", "Voice coach", "sofia@lindqvistvoice.com",
       "2026-08-20T15:00", None, price="solo_monthly", trial=True),
    _m("noah_klein", "Noah Klein", "Math tutor", "noah@kleinmath.net",
       "2026-08-24T22:30", (VISA, (5, 2028)), price="studio_monthly", trial=True),
    _m("leila_farah", "Leila Farah", "Oud teacher", "leila@farahmusic.com",
       "2026-08-27T17:20", (MASTERCARD, (2, 2029)), price="solo_monthly", trial=True, coupon=_T),
    _m("grace_adeyemi", "Grace Adeyemi", "Brightside Music Studio",
       "grace@brightsidemusic.studio", "2026-08-30T15:10", (cards.DISPUTE_FRAUDULENT, (6, 2028)),
       price="studio_annual"),
)  # fmt: skip
