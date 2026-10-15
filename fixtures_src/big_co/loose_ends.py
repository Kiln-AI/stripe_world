"""The scripted loose ends (components/big_co.md §5): fixed prospects with fixed beats.

A beat is `(kind, at, *args)`; `Lives` runs them instead of the random draws:

- `("card", at, card)`: a new default card (the declining swaps; the next renewal charges it).
- `("disputed_service", at, card, (description, amount))`: a one-off services payment confirmed
  on a dispute test card the customer adds for it, which opens its dispute.
- `("evidence", at, text)`: evidence submitted on the customer's latest dispute.
- `("accept_dispute", at)`: the customer's latest dispute accepted (`/close`), so lost.
- `("cancel_at_period_end", at, feedback)`.
- `("onboarding", at, price_key)`: an onboarding invoice, left to finalize on its own.
- `("pay_open", at)`: the next open `send_invoice` invoice is paid by card at `at`; any later one
  is never paid.
- `("ramp", (seats, end_iso), ...)`: the Enterprise deal is a ramp schedule of these phases.
"""

from dataclasses import replace
from typing import Any

from fixtures_src.big_co.population import Interval, Prospect, Tier, Trial
from fixtures_src.big_co.shape import SERVICES, Shape
from fixtures_src.replay import cards
from fixtures_src.replay.cards import Card

__all__ = ["scripted"]

_EXP = (11, 2029)

_ADDRESSES = {
    "CO": {"line1": "1840 Larkin Ave", "city": "Denver", "state": "CO", "postal_code": "80202"},
    "AZ": {"line1": "512 Quarry Rd", "city": "Phoenix", "state": "AZ", "postal_code": "85004"},
    "TX": {"line1": "77 Kestrel Way", "city": "Austin", "state": "TX", "postal_code": "78701"},
    "FL": {"line1": "2301 Millbrook Dr", "city": "Tampa", "state": "FL", "postal_code": "33602"},
    "GA": {"line1": "908 Halsey Pl", "city": "Atlanta", "state": "GA", "postal_code": "30303"},
    "NC": {"line1": "45 Bramble Ln", "city": "Raleigh", "state": "NC", "postal_code": "27601"},
    "WA": {"line1": "3310 Calder Ave", "city": "Seattle", "state": "WA", "postal_code": "98101"},
    "OR": {"line1": "616 Marlow St", "city": "Portland", "state": "OR", "postal_code": "97204"},
}
_AREA = {"CO": "303", "AZ": "602", "TX": "512", "FL": "813", "GA": "404", "NC": "919",
         "WA": "206", "OR": "503"}  # fmt: skip


def _p(
    key: str,
    company: str,
    domain: str,
    created: str,
    *,
    state: str,
    tier: Tier,
    seats: int,
    interval: Interval = "month",
    card: Card | None = cards.GOOD[0],
    trial: Trial = "none",
    script: tuple[Any, ...] = (),
    enterprise_rate: int | None = None,
) -> Prospect:
    address = dict(_ADDRESSES[state], country="US")
    return Prospect(
        key=key,
        cohort="loose_end",
        created=created,
        company=company,
        domain=domain,
        email=f"billing@{domain}",
        phone=None,  # numbered in `scripted`
        address=address,
        description=None,
        metadata={"crm_account_id": ""} if tier in ("business", "enterprise") else {},
        tax_exempt=False,
        tier=tier,
        interval=interval,
        legacy=False,
        seats=seats,
        vehicles=None,
        sms=False,
        card=card,
        card_exp=_EXP if card is not None else None,
        trial=trial,
        trial_end=None,
        converts=False,
        coupon=None,
        onboarding=None,
        enterprise_rate=enterprise_rate,
        pays_by_card=True,
        scripted=True,
        script=script,
    )


def scripted(shape: Shape) -> list[Prospect]:
    """Every loose end, fixed: no randomness."""
    del shape
    out: list[Prospect] = []
    # Disputed one-off services payments (big_co.md §5 has a dispute card swapped in before the
    # August renewal; by the user's decision in Phase 4, disputes come only from payments the
    # world confirms itself). The instants are those renewals', so the due dates are the spec's.
    training, data_import, report, rollout = SERVICES
    queue = (
        ("Arroyo Air & Heat", "arroyoairandheat.com", "2026-05-26T16:00:00.000Z", "AZ",
         cards.DISPUTE_FRAUDULENT, "2026-08-26T16:00:00.000Z", training),
        ("Tamarack Plumbing", "tamarackplumbing.com", "2026-05-27T18:30:00.000Z", "CO",
         cards.DISPUTE_NOT_RECEIVED, "2026-08-27T18:30:00.000Z", data_import),
        ("Gridline Electric Co.", "gridlineelectric.com", "2026-05-28T15:15:00.000Z", "TX",
         cards.DISPUTE_FRAUDULENT, "2026-08-28T15:15:00.000Z", training),
        ("Sandstone Mechanical", "sandstonemechanical.com", "2026-05-29T20:45:00.000Z", "FL",
         cards.DISPUTE_NOT_RECEIVED, "2026-08-29T20:45:00.000Z", rollout),
    )  # fmt: skip
    for n, (company, domain, created, state, card, paid, service) in enumerate(queue, start=1):
        out.append(
            _p(f"le_queue_{n}", company, domain, created, state=state, tier="pro", seats=6,
               script=(("disputed_service", paid, card, service),))
        )  # fmt: skip
    out.append(
        _p("le_inquiry_open", "Ridgeway Heating", "ridgewayheating.com",
           "2026-06-15T16:00:00.000Z", state="CO", tier="pro", seats=4,
           script=(("disputed_service", "2026-08-15T16:00:00.000Z", cards.DISPUTE_INQUIRY,
                    report),))
    )  # fmt: skip
    out.append(
        _p("le_inquiry_review", "Larkspur Electric", "larkspurelectric.com",
           "2026-06-18T16:00:00.000Z", state="GA", tier="pro", seats=8,
           script=(("disputed_service", "2026-08-18T16:00:00.000Z", cards.DISPUTE_INQUIRY,
                    data_import),
                   ("evidence", "2026-08-21T17:00:00.000Z", "Signed service agreement attached.")))
    )  # fmt: skip
    out.append(
        _p("le_review", "Coldwater Plumbing Supply", "coldwaterplumbingsupply.com",
           "2026-06-12T16:00:00.000Z", state="NC", tier="pro", seats=10,
           script=(("disputed_service", "2026-08-12T16:00:00.000Z", cards.DISPUTE_FRAUDULENT,
                    training),
                   ("evidence", "2026-08-16T17:00:00.000Z", "Login history for the account.")))
    )  # fmt: skip
    # Two settled disputes, one won and one lost. At the scaled population (shape.py) the
    # simulation draws only a handful of disputes, too few to promise every stage of big_co.md
    # §8; these two keep `won` and `lost` present on every build.
    out.append(
        _p("le_dispute_won", "Kestrel Ridge Heating", "kestrelridgeheating.com",
           "2026-03-10T16:00:00.000Z", state="CO", tier="pro", seats=5,
           script=(("disputed_service", "2026-07-08T16:00:00.000Z", cards.DISPUTE_FRAUDULENT,
                    rollout),
                   ("evidence", "2026-07-14T17:00:00.000Z", "winning_evidence")))
    )  # fmt: skip
    out.append(
        _p("le_dispute_lost", "Millrace Electric", "millraceelectric.com",
           "2026-02-17T16:00:00.000Z", state="TX", tier="starter", seats=3,
           script=(("disputed_service", "2026-06-23T16:00:00.000Z", cards.DISPUTE_NOT_RECEIVED,
                    report),
                   ("accept_dispute", "2026-06-29T17:00:00.000Z")))
    )  # fmt: skip
    out.append(
        _p("le_overdue", "Halvorsen Mechanical Group", "halvorsenmechanical.com",
           "2025-06-18T17:00:00.000Z", state="CO", tier="enterprise", seats=60,
           interval="year", enterprise_rate=8500,
           script=(("pay_open", "2025-07-10T16:00:00.000Z"),))
    )  # fmt: skip
    out.append(
        _p("le_ramp", "Cascade Comfort Systems", "cascadecomfortsystems.com",
           "2026-06-04T16:00:00.000Z", state="WA", tier="enterprise", seats=80,
           interval="year", enterprise_rate=8000,
           script=(("ramp", (80, "2026-09-04T16:00:00.000Z"), (120, "2026-12-04T16:00:00.000Z"),
                    (160, "2027-06-04T16:00:00.000Z")),
                   ("pay_open", "2026-06-24T16:00:00.000Z")))
    )  # fmt: skip
    out.append(
        _p("le_churn_risk", "Copperline Mechanical", "copperlinemechanical.com",
           "2025-09-13T15:00:00.000Z", state="CO", tier="business", seats=48, interval="year",
           script=(("cancel_at_period_end", "2026-08-25T19:00:00.000Z", "switched_service"),))
    )  # fmt: skip
    out.append(
        _p("le_incomplete", "Bristlecone Electric", "bristleconeelectric.com",
           "2026-09-01T11:00:00.000Z", state="CO", tier="pro", seats=5,
           card=cards.AUTH_REQUIRED)
    )  # fmt: skip
    for n, (company, created, state) in enumerate(
        (
            ("Juniper Flats Plumbing", "2026-07-08T15:00:00.000Z", "AZ"),
            ("Kettle Creek Electric", "2026-08-03T15:00:00.000Z", "OR"),
            ("Mesquite Drain & Sewer", "2026-08-21T15:00:00.000Z", "TX"),
        ),
        start=1,
    ):
        domain = company.lower().replace(" & ", "and").replace(" ", "") + ".com"
        out.append(
            _p(f"le_expired_{n}", company, domain, created, state=state, tier="starter",
               seats=2, card=cards.AUTH_REQUIRED)
        )  # fmt: skip
    for n, (company, created, state) in enumerate(
        (
            ("Bellwether Climate Services", "2026-07-06T16:00:00.000Z", "GA"),
            ("Northgate Electrical Contractors", "2026-07-27T16:00:00.000Z", "NC"),
            ("Pinyon Ridge Mechanical", "2026-08-10T16:00:00.000Z", "CO"),
        ),
        start=1,
    ):
        domain = company.lower().replace(" ", "") + ".com"
        out.append(
            _p(f"le_paused_{n}", company, domain, created, state=state, tier="business",
               seats=14, card=None, trial="cardless")
        )  # fmt: skip
    for n, (company, created, state, swap) in enumerate(
        (
            ("Foxglove Plumbing", "2026-04-10T15:00:00.000Z", "FL", "2026-07-08T15:00:00.000Z"),
            ("Driftwood Heating & Air", "2026-04-14T15:00:00.000Z", "TX",
             "2026-07-12T15:00:00.000Z"),
            ("Yellowjacket Electric", "2026-04-18T15:00:00.000Z", "AZ",
             "2026-07-16T15:00:00.000Z"),
        ),
        start=1,
    ):  # fmt: skip
        domain = company.lower().replace(" & ", "and").replace(" ", "") + ".com"
        out.append(
            _p(f"le_unpaid_{n}", company, domain, created, state=state, tier="starter",
               seats=3, script=(("card", swap, cards.DECLINING),))
        )  # fmt: skip
    for n, (company, created, state, swap) in enumerate(
        (
            ("Saltgrass Mechanical", "2026-05-25T15:00:00.000Z", "TX", "2026-08-23T15:00:00.000Z"),
            ("Quillback Plumbing", "2026-05-27T15:00:00.000Z", "WA", "2026-08-25T15:00:00.000Z"),
            ("Harrowgate Electric", "2026-05-29T15:00:00.000Z", "CO", "2026-08-27T15:00:00.000Z"),
        ),
        start=1,
    ):
        domain = company.lower().replace(" ", "") + ".com"
        out.append(
            _p(f"le_past_due_{n}", company, domain, created, state=state, tier="pro",
               seats=7, script=(("card", swap, cards.DECLINING),))
        )  # fmt: skip
    for n, (company, state) in enumerate(
        (
            ("Thornfield Air Systems", "CO"),
            ("Wolf Creek Plumbing & Heating", "AZ"),
            ("Lodestar Electrical Services", "TX"),
            ("Ironbark Mechanical", "GA"),
            ("Bluestem Comfort Co.", "NC"),
        )
    ):
        created = f"2026-09-01T13:{n * 10:02d}:00.000Z"
        domain = company.lower().replace(" & ", "and").replace(" ", "").replace(".", "") + ".com"
        out.append(
            _p(f"le_draft_{n + 1}", company, domain, created, state=state, tier="business",
               seats=12 + 3 * n,
               script=(("onboarding", f"2026-09-01T13:{n * 10 + 5:02d}:00.000Z",
                        "onboarding_business"),))
        )  # fmt: skip
    # Numbered in a fixed order: a phone number each, and a CRM id for the sales-led accounts.
    return [
        replace(
            p,
            phone=f"+1{_AREA[str(p.address['state'])]}55501{10 + i:02d}",
            metadata={"crm_account_id": f"006{8100000000 + i:010d}"} if p.metadata else {},
        )
        for i, p in enumerate(out)
    ]
