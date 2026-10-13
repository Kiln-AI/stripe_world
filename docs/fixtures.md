# Fixtures

This world ships three committed fixtures, all frozen at `NOW = 2026-09-01T14:00:00.000Z`:

- **`empty`**: the schema and the sandbox account, nothing else. A live-mode account.
- **`small_co`**: Wrenfield Labs, a small hand-written test-mode account.
- **`big_co`**: Ostrander Systems, a large simulated test-mode account.

Both company fixtures are test-mode Stripe accounts. Open them with no `livemode` (the account
row's mode applies), or with `livemode: false`; asking for live mode is refused. Their history was
replayed through the world's own API dispatcher and billing engine at historical instants
(`fixtures_src/replay/`), so every row, id, invoice number, ledger entry and event was written by
world code at the right time. Events cover the last 30 days before `NOW`, as Stripe's do.

Rebuild a fixture with `rm -rf fixtures/<id> && uv run python fixtures_src/generate.py <id>`.

Each section below describes one company: its story, account identity, catalog, counts, and every
loose end, with the query that finds it and the rows it returns in the committed build. The
queries are landmark blocks (`sql landmark=<fixture>/<name>` followed by a `json rows` block),
checked against the committed fixtures by `tests/test_guide.py`.

## small_co

**Wrenfield Labs**, a two-person company in Asheville, North Carolina, sells a booking page,
lesson reminders and payment records to independent tutors, music teachers and coaches. The
account (`acct_1Pq7WrnFd3kT9mZa`, test mode) opened on 2025-08-25 for a private beta; six founding
members bought a $96/year plan in September 2025; the public launch on 2026-03-02 brought a wave
on the `LAUNCH` coupon, and a teaching newsletter's `TEACHWELL25` code brought more from
2026-07-14. Every one of the 45 customers is written by hand in `fixtures_src/small_co/cast.py`,
keyed by a stable handle (`daniela_ruiz`, `grace_adeyemi`, ...); search them by email.

**Catalog.** Products *Wrenfield Solo*, *Wrenfield Studio* and *Done-for-you setup*; prices by
`lookup_key`: `founding_annual` ($96/yr, archived at launch), `solo_monthly` ($12),
`solo_annual` ($120), `studio_monthly` ($29), `studio_annual` ($290), `setup` ($49 one-time).
Each $49 setup invoice item also carries its own one-off price and product, as Stripe mints them,
so the account holds 5 products and 8 prices. Coupons `LAUNCH` (50% off, 3 months, redeemed 10
times) and `TEACHWELL25` (25% off once, redeemed 5 times). 14-day trials; a trial with no card
pauses at its end. Payouts are manual, each Monday, of the available balance.

**Counts at `NOW`.**

| What | Count |
|---|---|
| customers | 45 (3 with no subscription: the founder's test record and two abandoned checkouts) |
| subscriptions | 42: 29 `active`, 3 `trialing`, 1 `past_due`, 1 `paused`, 7 `canceled`, 1 `incomplete_expired` |
| invoices / charges | 119 / 109 (2 failed) |
| refunds / disputes | 2 / 1 |
| payouts | 19 (18 `paid`, the 2026-08-31 one `pending`) |
| events | 145, all from the last 30 days |

**Loose ends.** Each is one query away; the customer's email is the stable handle.

| Loose end | Who | State |
|---|---|---|
| Open dispute | Grace Adeyemi, Brightside Music Studio (`grace@brightsidemusic.studio`) | $290 Studio annual charge disputed as `fraudulent`, `needs_response`, evidence due 2026-09-07T23:59:59Z |
| Past due, mid-dunning | Leo Marchetti (`leo@marchettichem.com`) | The 2026-08-30 renewal declined on card `...0341`, one retry failed; `attempt_count` 2, next attempt 2026-09-02T22:00Z |
| Trial ending without a card | Sofia Lindqvist (`sofia@lindqvistvoice.com`) | `trialing` until 2026-09-03T15:00Z, then pauses |
| Paused | Adaeze Okafor (`adaeze@okaforpiano.com`) | Trial ended 2026-08-15 with no card |
| Founding renewals this week | Daniela Ruiz, Marcus Bell, Owen Fitzgerald, Hana Kobayashi, Tomasz Wójcik | $96 renewals 09-03 to 09-08; Owen's card (`...0341`) will decline |
| Cancel at period end | Priya Natarajan (`priya@natarajanpiano.com`) | Founding plan ends 2026-09-05 ("Retiring from teaching") |
| Credit balance | Nadia Haddad (`nadia@haddadfrench.com`) | Downgraded Studio to Solo on 2026-08-20, invoiced at once; balance -$12.60 |
| Partial refund | Jamal Carter (`jamal@carterdrums.com`) | $170 of his $290 Studio annual charge refunded when he moved to Solo annual |
| Pending invoice item | Elena Petrova, Petrova Dance Academy (`elena@petrovadance.com`) | $49 setup item, not yet on an invoice |
| Duplicate customer | Chris Nguyen (`chris@nguyentutoring.com`, `chris.nguyen@cnlessons.com`) | Two records; the second trial canceled on day three |
| Founder test | Test (do not bill) (`test+launch@wrenfield.app`) | A $1.00 payment on launch morning, refunded in full |

```sql
-- every subscription with its customer, status and price
SELECT c.name, c.email, s.status, p.lookup_key
FROM subscriptions s
JOIN customers c ON c.id = s.customer
JOIN subscription_items si ON si.subscription = s.id
JOIN prices p ON p.id = si.price
ORDER BY s.created;
```

## big_co
