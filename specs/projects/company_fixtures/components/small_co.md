---
status: complete
---

# Component: `small_co` (Wrenfield Labs)

## Purpose and scope

Builds the `small_co` fixture: the account, the catalog, a hand-written cast of 45 customers, weekly
payouts, and the checks. Functional spec §4 is the contract; this document is the recipe.

**Not in scope:** the replay machinery (`replay.md`) and any world change.

## Public interface

```python
# fixtures_src/small_co/__init__.py
def build(inst: seahaven.Instance) -> None:
    """Fill a blank test-mode instance with Wrenfield Labs' history up to NOW, then check it."""

EXPECTATIONS: tuple[Expectation, ...]          # §6

# fixtures_src/small_co/cast.py
CAST: tuple[Member, ...]                       # the 45 members, in §4's order
```

`build`:

```python
with inst.bulk() as ctx:
    rng = random.Random(ctx.ids.random.getrandbits(64))   # drawn even though the cast is fixed: the
                                                          # protocol is the same in both builders
    install_account(ctx, accounts.WRENFIELD)
    tl = Timeline(ctx, until=NOW)
    client = Client()
    subs = Subscriptions(tl, client, SmallCoHooks())
    catalog = Catalog()                                   # filled by its own steps (§2)
    catalog.schedule(tl, client)
    story.schedule(tl, client, subs, catalog, CAST)
    Payouts(tl, client, destination=accounts.WRENFIELD.bank_account_id).schedule(mondays())
    tl.run()
    refresh_balance_statuses(ctx, now=NOW)
    trim_events(ctx, now=NOW)
check_fixture(inst.inspect(), now=NOW, expect=EXPECTATIONS)
```

`SmallCoHooks`: `before_boundary`/`after_boundary` do nothing (card changes are beats at their own
instants); `on_retry` returns False (nobody in this story recovers before `NOW`); `on_open_invoice`
is never reached (no `send_invoice`).

## 1. Timeline conventions

All instants are UTC, canonical (`2026-03-02T15:45:00.000Z`). The timeline runs from
`2025-08-25T14:00:00.000Z` (account opened) to `NOW`. Payouts run every Monday at `15:00Z` from
2025-09-08 to 2026-08-31.

## 2. Catalog (`catalog.py`)

`Catalog` maps keys to the ids the API returns, so the cast refers to prices by key.

| Instant | Step |
|---|---|
| 2025-08-26T15:00Z | product **Wrenfield Solo**; price `founding_annual`: $96/year, nickname "Founding member", `lookup_key: founding_annual` |
| 2026-02-23T16:00Z | product **Wrenfield Studio**; product **Done-for-you setup**; prices `solo_monthly` $12/month, `solo_annual` $120/year (on Solo), `studio_monthly` $29/month, `studio_annual` $290/year (on Studio), `setup` $49 one-time (on Done-for-you setup); each with the nickname and `lookup_key` of functional spec §4.2 |
| 2026-02-27T18:00Z | coupon `LAUNCH`: `percent_off` 50, `duration: repeating`, `duration_in_months` 3, `redeem_by` 2026-03-31T23:59:59Z, name "Launch: 50% off for 3 months" |
| 2026-03-02T09:00Z | `POST /v1/prices/{founding_annual}` with `active: false` |
| 2026-07-13T16:00Z | coupon `TEACHWELL25`: `percent_off` 25, `duration: once`, name "Teachwell newsletter: 25% off" |

## 3. Cast data model (`cast.py`)

```python
@dataclass(frozen=True)
class Member:
    key: str                  # stable handle, used by the guide and tests
    name: str
    email: str | None
    description: str          # what they teach; studios name the studio
    created: str              # customer creation instant
    beats: tuple[Beat, ...]   # in instant order, all at or after `created`

# Beats (frozen dataclasses, each with `at: str`):
Card(at, card: replay.cards.Card, exp: tuple[int, int])         # add_card(..., default=True)
Subscribe(at, price: str, trial_days: int | None = None, coupon: str | None = None)
ChangePlan(at, price: str, proration: str)                     # update items[0] to price
CancelAtPeriodEnd(at, feedback: str, comment: str | None = None)
CancelNow(at, feedback: str, comment: str | None = None)
Refund(at, amount: int | None = None)                          # the latest succeeded charge
InvoiceItem(at, price: str)
OneOffPayment(at, amount: int, description: str)               # PI create + confirm with the default card
```

`story.schedule` turns each member into steps: one step at `created` that creates the customer
(`create_customer` with `name`, `email`, `description`), then one step per beat. A `Subscribe` step
calls `client.subscribe(...)` with the price id from the catalog, then `subs.follow(...)`; every
other subscription beat acts on the member's current subscription. Beats at the same instant run in
listed order.

## 4. The cast

Instants are UTC. Cards are named by role (`replay.cards`); `exp` is month/year. "Trial" is
`trial_days=14` and every trial gets the pause-without-card setting. The driver does the renewals,
trial ends, dunning and expiry; the "State at `NOW`" column is what the build must produce.

### Founding members (subscribe at `created` to `founding_annual`)

| # | key | Name, description | email | created | Card | Further beats | State at `NOW` |
|---|---|---|---|---|---|---|---|
| 1 | `daniela_ruiz` | Daniela Ruiz, violin teacher | daniela@ruizviolin.com | 2025-09-03T16:20Z | 4242, 11/2028 | | active; renews 2026-09-03 |
| 2 | `marcus_bell` | Marcus Bell, SAT and ACT tutor | marcus@bellprep.net | 2025-09-04T13:05Z | Mastercard 4444, 03/2027 | | active; renews 09-04 |
| 3 | `priya_natarajan` | Priya Natarajan, piano teacher | priya@natarajanpiano.com | 2025-09-05T18:40Z | Amex, 07/2028 | 2026-08-11T20:15Z CancelAtPeriodEnd(`other`, "Retiring from teaching") | active, cancels 09-05 |
| 4 | `owen_fitzgerald` | Owen Fitzgerald, running coach | owen@fitzrunning.co | 2025-09-06T14:10Z | 4242, 08/2026 | 2026-08-18T17:45Z Card(DECLINING, 08/2030) | active; renews 09-06 on a declining card |
| 5 | `hana_kobayashi` | Hana Kobayashi, math tutor | hana@kobayashimath.com | 2025-09-07T01:30Z | Discover, 02/2029 | | active; renews 09-07 |
| 6 | `tomasz_wojcik` | Tomasz Wójcik, guitar teacher | tomasz@wojcikguitar.com | 2025-09-08T22:15Z | Visa debit, 05/2027 | | active; renews 09-08 |

### Launch day and the `LAUNCH` wave (subscribe at `created`, coupon `LAUNCH`, no trial, unless noted)

| # | key | Name, description | email | created | Card | Price and beats | State at `NOW` |
|---|---|---|---|---|---|---|---|
| 7 | `founder_test` | Test (do not bill), "Founder test account" | test+launch@wrenfield.app | 2026-03-02T13:02Z | 4242, 12/2030 | 13:05Z OneOffPayment(100, "Launch smoke test"); 13:20Z Refund() | no subscription; $1.00 refunded |
| 8 | `amara_osei` | Amara Osei, reading tutor | amara@oseireading.com | 2026-03-02T15:45Z | 4242, 06/2028 | `solo_monthly` | active |
| 9 | `ben_carlisle` | Ben Carlisle, guitar teacher | ben@carlisleguitar.com | 2026-03-02T19:10Z | 4242, 10/2027 | `solo_monthly`; 2026-06-15T02:00Z CancelAtPeriodEnd(`too_expensive`) | canceled 2026-07-02 |
| 10 | `lucas_moreau` | Lucas Moreau, cello teacher | lucas@moreaucello.com | 2026-03-03T02:10Z | Mastercard 4444, 01/2029 | `solo_monthly` | active |
| 11 | `tom_okonkwo` | Tom Okonkwo, Riverbend Music School | tom@riverbendmusic.org | 2026-03-03T15:30Z | Amex, 09/2027 | `studio_monthly` | active |
| 12 | `sarah_goldberg` | Sarah Goldberg, LSAT tutor | sarah@goldberglsat.com | 2026-03-04T17:25Z | Amex, 04/2028 | `solo_monthly` | active |
| 13 | `anika_sharma` | Anika Sharma, Sharma Tutoring Collective | anika@sharmatutoring.com | 2026-03-04T21:29Z | Mastercard 4444, 12/2027 | 21:29Z InvoiceItem(`setup`); 21:30Z `studio_monthly` | active; setup paid on her first invoice |
| 14 | `olivia_grant` | Olivia Grant, SAT tutor | olivia@grantprep.com | 2026-03-05T14:00Z | Mastercard 4444, 05/2028 | `solo_monthly`; 2026-05-20T19:00Z CancelNow(`switched_service`) | canceled |
| 15 | `diego_herrera` | Diego Herrera, youth soccer coach | diego@herrerafutbol.com | 2026-03-05T23:50Z | Visa debit, 02/2027 | `solo_monthly` | active |
| 16 | `mei_lin` | Mei Lin, Mandarin tutor | mei@linmandarin.com | 2026-03-06T12:15Z | 4242, 08/2028 | `solo_monthly`, **no coupon** | active |
| 17 | `ellen_park` | Ellen Park, yoga teacher | ellen@parkyoga.studio | 2026-03-07T16:00Z | Amex, 11/2027 | `solo_annual` | active; renews 2027-03-07 |
| 18 | `paolo_ricci` | Paolo Ricci, Ricci Voice Studio | paolo@riccivoice.com | 2026-03-08T19:00Z | 4242, 07/2029 | `studio_annual` | active |

### After launch

| # | key | Name, description | email | created | Card | Price and beats | State at `NOW` |
|---|---|---|---|---|---|---|---|
| 19 | `marco_bianchi` | Marco Bianchi, Italian tutor | marco@bianchiitaliano.com | 2026-04-03T17:00Z | 4242, 03/2028 | `solo_monthly` trial; 2026-04-12T15:30Z CancelNow(`unused`) | canceled during trial |
| 20 | `elena_petrova` | Elena Petrova, Petrova Dance Academy | elena@petrovadance.com | 2026-04-10T17:00Z | Mastercard 4444, 06/2027 | `studio_monthly`; 2026-08-28T14:30Z InvoiceItem(`setup`) | active; $49 item pending for 09-10 |
| 21 | `jordan_blake` | Jordan Blake, personal trainer | jordan@blakestrength.com | 2026-04-14T20:40Z | Mastercard 4444, 09/2028 | `solo_monthly` trial | active |
| 22 | `yuki_tanaka` | Yuki Tanaka, art teacher | yuki@tanakaart.studio | 2026-04-18T21:20Z | Amex, 01/2028 | `studio_monthly` trial; 2026-07-30T16:00Z CancelAtPeriodEnd(`too_expensive`) | canceled 2026-08-02 |
| 23 | `claire_dubois` | Claire Dubois, Dubois Language Studio | claire@duboislanguages.com | 2026-04-22T18:45Z | 4242, 10/2028 | `studio_monthly` trial | active |
| 24 | `rachel_stein` | Rachel Stein, test-prep tutor | rachel@steintutoring.com | 2026-04-27T02:30Z | none | none (abandoned checkout) | no subscription |
| 25 | `leo_marchetti` | Leo Marchetti, chemistry tutor | leo@marchettichem.com | 2026-04-30T10:00Z | 4242, 04/2027 | `solo_monthly`; 2026-08-20T18:30Z Card(DECLINING, 11/2029) | `past_due`: the 2026-08-30T10:00Z renewal declined, one retry failed, `attempt_count` 2, next attempt after `NOW` |
| 26 | `chris_nguyen` | Chris Nguyen, math and science tutoring | chris@nguyentutoring.com | 2026-05-05T19:30Z | 4242, 02/2028 | `solo_monthly` | active |
| 27 | `nadia_haddad` | Nadia Haddad, French tutor | nadia@haddadfrench.com | 2026-05-12T15:00Z | Mastercard 4444, 07/2027 | `studio_monthly`; 2026-08-20T16:00Z ChangePlan(`solo_monthly`, `always_invoice`) | active on Solo; negative balance (credit) |
| 28 | `rosa_delgado` | Rosa Delgado, Spanish tutor | rosa@delgadospanish.com | 2026-05-15T22:10Z | 4242, 12/2028 | `solo_monthly`; 2026-07-01T01:00Z CancelAtPeriodEnd(`other`, "Moved to an in-person school") | canceled 2026-07-15 |
| 29 | `fatima_rahman` | Fatima Rahman, biology tutor | fatima@rahmanbio.com | 2026-05-21T14:05Z | 4242, 05/2029 | `solo_monthly` trial | active |
| 30 | `june_harper` | June Harper, Harper Swim School | june@harperswim.com | 2026-05-28T13:35Z | Amex, 03/2028 | `studio_annual` | active |
| 31 | `ravi_iyer` | Ravi Iyer, coding tutor for kids | ravi@iyercodes.com | 2026-06-03T03:20Z | Mastercard 4444, 08/2027 | `solo_annual` | active |
| 32 | `sam_rivera` | Sam Rivera, Peak Climbing Coaching | sam@peakclimbcoach.com | 2026-06-09T16:05Z | Visa debit, 06/2028 | `studio_monthly` | active |
| 33 | `chris_nguyen_2` | Chris Nguyen, tutoring | chris.nguyen@cnlessons.com | 2026-06-14T20:00Z | none | `solo_monthly` trial; 2026-06-17T12:00Z CancelNow(`other`, "Duplicate account") | canceled; the duplicate of #26 |
| 34 | `victor_alves` | Victor Alves, swim coach | victor@alvesswim.com | 2026-06-22T16:40Z | DECLINING, 09/2028 | `solo_monthly` | `incomplete_expired` at 2026-06-23T16:40Z |
| 35 | `jamal_carter` | Jamal Carter, drum teacher | jamal@carterdrums.com | 2026-07-10T21:00Z | 4242, 01/2029 | `studio_annual`; 2026-07-17T15:30Z ChangePlan(`solo_annual`, `none`); 15:31Z Refund(17000) | active on Solo annual; $170 refunded |
| 36 | `kofi_mensah` | Kofi Mensah, chess coach | kofi@mensahchess.com | 2026-07-15T18:30Z | 4242, 07/2028 | `solo_monthly`, coupon `TEACHWELL25` | active |
| 37 | `dev_patel` | Dev Patel, physics tutor | dev@patelphysics.com | 2026-07-16T20:45Z | 4242, 10/2027 | `solo_monthly`, `TEACHWELL25`; 2026-08-25T15:00Z CancelNow(`unused`) | canceled |
| 38 | `isabel_santos` | Isabel Santos, flute teacher | isabel@santosflute.com | 2026-07-22T13:50Z | Discover, 04/2028 | `solo_monthly`, `TEACHWELL25` | active |
| 39 | `hannah_weber` | Hannah Weber, German tutor | hannah@weberdeutsch.com | 2026-07-28T10:10Z | 4242, 09/2029 | `solo_annual`, `TEACHWELL25` | active |
| 40 | `adaeze_okafor` | Adaeze Okafor, piano teacher | adaeze@okaforpiano.com | 2026-08-01T18:00Z | none | `solo_monthly` trial | `paused` since 2026-08-15T18:00Z |
| 41 | `malik_ortiz` | Malik Ortiz, brass teacher | malik@ortizbrass.com | 2026-08-09T23:05Z | none | none (abandoned checkout) | no subscription |
| 42 | `sofia_lindqvist` | Sofia Lindqvist, voice coach | sofia@lindqvistvoice.com | 2026-08-20T15:00Z | none | `solo_monthly` trial | `trialing`; ends 2026-09-03T15:00Z, will pause |
| 43 | `noah_klein` | Noah Klein, math tutor | noah@kleinmath.net | 2026-08-24T22:30Z | 4242, 05/2028 | `studio_monthly` trial | `trialing`; ends 09-07 |
| 44 | `leila_farah` | Leila Farah, oud teacher | leila@farahmusic.com | 2026-08-27T17:20Z | Mastercard 4444, 02/2029 | `solo_monthly` trial, `TEACHWELL25` | `trialing`; ends 09-10 |
| 45 | `grace_adeyemi` | Grace Adeyemi, Brightside Music Studio | grace@brightsidemusic.studio | 2026-08-30T15:10Z | DISPUTE_FRAUDULENT, 06/2028 | `studio_annual` | active; dispute `needs_response`, `fraudulent`, evidence due 2026-09-07T23:59:59Z |

Counts by state, matching functional spec §4.3: active 29 (6 founding, 11 Solo monthly, 4 Solo
annual, 5 Studio monthly, 3 Studio annual), trialing 3, past due 1, paused 1, canceled 7 (six
cancellations plus the duplicate's trial), incomplete expired 1, no subscription 3. `LAUNCH` is
redeemed 10 times and `TEACHWELL25` 5 times.

Where the world's behavior decides a detail the table assumes, Phase 3 confirms it and the table is
corrected, not the world:

- Anika's setup item is created one minute before her subscription, so her first invoice sweeps it.
  If the world's subscription creation does not sweep pending items, the item moves to just after
  the subscribe step and her first renewal sweeps it.
- Leo's single retry follows from the default dunning spacing (14 days ÷ 8 attempts = 1.75 days):
  the 08-30T10:00Z decline schedules 09-01T04:00Z, which runs; the next falls after `NOW`.
- Grace's `due_by` follows the world's `compute_due_by` (end of the UTC day eight days after
  creation).
- Victor's subscription answers `incomplete` under the default `payment_behavior`. If the world
  answers 402 instead, the step uses `client.attempt` and asserts it.

## 5. Payouts

`mondays()` yields every Monday from 2025-09-08 to 2026-08-31 at `15:00Z`. Between the founding
payouts of September 2025 and launch there is no balance, so no payout. The payout made on
2026-08-31 arrives after `NOW` and stays `pending`.

## 6. Expectations

| Name | Count |
|---|---|
| customers | exactly 45 |
| subscriptions | 42: `active` 29, `trialing` 3, `past_due` 1, `paused` 1, `canceled` 7, `incomplete_expired` 1 |
| subscriptions with `cancel_at_period_end` | exactly 1 (Priya) |
| disputes | exactly 1, `needs_response`, `fraudulent` |
| refunds | exactly 2 |
| customers with a negative balance | exactly 1 (Nadia) |
| pending invoice items (`invoice IS NULL`) | exactly 1 (Elena) |
| products / prices / inactive prices / coupons | 3 / 6 / 1 / 2 |
| charges | 80 – 130 |
| payouts | 20 – 40, at least one `pending` |
| events | all within the last 30 days |
| file size | under 2 MB |

## 7. Guide landmarks

`small_co/cast` (every customer with key-bearing fields: name, email, description, subscription
status, price lookup key), `small_co/open_dispute`, `small_co/past_due`,
`small_co/trial_ending_without_card`, `small_co/paused`, `small_co/renewals_this_week`,
`small_co/cancel_at_period_end`, `small_co/credit_balance`, `small_co/partial_refund`,
`small_co/pending_invoice_item`, `small_co/duplicate_customers`, `small_co/founder_test`.

## 8. Test plan (`tests/test_small_co.py`)

| Test | Verifies |
|---|---|
| `test_account_identity` | `get_stripe_account_info` returns Wrenfield's object; `list_available_accounts_or_orgs` returns its id, `livemode` false, name "Wrenfield Labs" |
| `test_opening_live_is_refused` | `world.instance("small_co", startup={"livemode": True})` raises `WorldBug` |
| `test_expectations` | §6 through `check_fixture` (and so the invariants and guards) |
| `test_named_character_states` | one assertion block per character of functional spec §4.4, by email, through `stripe_api_read`/`stripe_api_search` in test mode |
| `test_new_ids_carry_wrenfield` | a customer created through `stripe_api_write` has a `cus_` id, and a payment intent's `pi_` id carries Wrenfield's account fragment |
| `test_tools_carry_on` | finalizing a new invoice for Daniela gets her next invoice number; a new customer's `x_seq` is above every existing one; a payout larger than the available balance is refused; `stripe_api_search` finds Grace by email |
