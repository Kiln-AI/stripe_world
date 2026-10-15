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

Rebuild a fixture with `rm -rf fixtures/<id> && uv run python fixtures_src/generate.py <id>`, then
refresh the guide's documented rows with `uv run python -m fixtures_src.guide --write` and review
the diff (`uv run python -m fixtures_src.guide` only checks them).

Each section below describes one company: its story, account identity, catalog, counts, and every
loose end, with the query that finds it and the rows it returns in the committed build. The
queries are landmark blocks (`sql landmark=<fixture>/<name>` followed by a `json rows` block),
checked against the committed fixtures by `tests/test_guide.py`. Times in JSON columns
(`evidence_details`, `current_phase`, schedule phases) are Unix seconds, as the API stores them
there; every other timestamp column is canonical UTC text.

## Known gaps

Both companies were replayed through this world as it is, so they show three of its known
differences from Stripe. Each is filed as an issue. The fixtures avoid it where they can and live
with it where they cannot:

- **A renewal bills the period that just ended**
  ([#12](https://github.com/Kiln-AI/stripe_world/issues/12)). Each renewal invoice's line
  repeats the service period the previous invoice billed, so the current period is on no line.
  The invoice a trial's end creates is stamped with the new period, so the renewal after it has
  the same span. The fixtures' consistency check allows that one pair, an identical span on a
  trial-end invoice and the renewal right after it, and no other overlap.
- **Repeating coupons never end, and `times_redeemed` stays 0**
  ([#13](https://github.com/Kiln-AI/stripe_world/issues/13)). No coupon in either fixture is
  `repeating`: the launch and switch-over codes are 50% and 25% off the first invoice
  (`duration: once`), and the association codes are `forever`. Every coupon reports
  `times_redeemed: 0`. To count redemptions, count the invoices that carry the coupon.
- **Pending invoice items are never swept into a subscription invoice**
  ([#14](https://github.com/Kiln-AI/stripe_world/issues/14)). A one-time fee is invoiced on its
  own invoice, as `small_co`'s first setup fee was. An item left pending stays pending: nothing
  bills it at the customer's next renewal.

Disputes in both fixtures come from one-off payments: this world opens a dispute test card's
dispute when it confirms a PaymentIntent. A subscription's own charges are never disputed.

## small_co

**Wrenfield Labs**, a two-person company in Asheville, North Carolina, sells a booking page,
lesson reminders and payment records to independent tutors, music teachers and coaches. The
account (`acct_1Pq7WrnFd3kT9mZa`, test mode) opened on 2025-08-25 for a private beta; six founding
members bought a $96/year plan in September 2025; the public launch on 2026-03-02 brought a wave
on the `LAUNCH` coupon, and a teaching newsletter's `TEACHWELL25` code brought more from
2026-07-14. Every one of the 45 customers is written by hand in `fixtures_src/small_co/cast.py`,
keyed by a stable handle (`daniela_ruiz`, `grace_adeyemi`, ...); search them by email.

**Opening it.** `world.instance("small_co")` (or `env.reset(fixture="small_co")`, or
`--fixture small_co` for the HTTP server) opens the account in test mode; the tools address it as
`stripe_context="acct_1Pq7WrnFd3kT9mZa"`, `livemode=False`. Its display name is *Wrenfield
Labs*, its business name *Wrenfield*, at `https://wrenfield.app`, support
`support@wrenfield.app`, Asheville, NC 28801, timezone `America/New_York`; payouts go to one test
bank account (`STRIPE TEST BANK`, `…6789`).

**Catalog.** Products *Wrenfield Solo*, *Wrenfield Studio* and *Done-for-you setup*; prices by
`lookup_key`: `founding_annual` ($96/yr, archived at launch), `solo_monthly` ($12),
`solo_annual` ($120), `studio_monthly` ($29), `studio_annual` ($290), `setup` ($49 one-time).
Each $49 setup invoice item also carries its own one-off price and product, as Stripe mints them,
so the account holds 5 products and 8 prices. Coupons: `LAUNCH` (50% off the first invoice, for
signups through 2026-03-31) and `TEACHWELL25` (25% off the first invoice). 14-day trials; a
trial with no card pauses at its end. Cards, charged automatically. No tax rates. Payouts are
manual, each Monday at 15:00Z, of the available balance.

**Counts at `NOW`.** All 30 days of events are kept, and nothing older.

```sql landmark=small_co/counts
SELECT what, n FROM (
  SELECT 1 AS k, 'customers' AS what, count(*) AS n FROM customers
  UNION ALL SELECT 2, 'customers with no subscription', count(*) FROM customers c
    WHERE NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.customer = c.id)
  UNION ALL SELECT 3, 'subscriptions', count(*) FROM subscriptions
  UNION ALL SELECT 4, 'invoices', count(*) FROM invoices
  UNION ALL SELECT 5, 'charges', count(*) FROM charges
  UNION ALL SELECT 6, 'failed charges', count(*) FROM charges WHERE status = 'failed'
  UNION ALL SELECT 7, 'refunds', count(*) FROM refunds
  UNION ALL SELECT 8, 'disputes', count(*) FROM disputes
  UNION ALL SELECT 9, 'payouts', count(*) FROM payouts
  UNION ALL SELECT 10, 'pending payouts', count(*) FROM payouts WHERE status = 'pending'
  UNION ALL SELECT 11, 'products', count(*) FROM products
  UNION ALL SELECT 12, 'prices', count(*) FROM prices
  UNION ALL SELECT 13, 'coupons', count(*) FROM coupons
  UNION ALL SELECT 14, 'events', count(*) FROM events
) ORDER BY k
```

```json rows
[
  {"what": "customers", "n": 45},
  {"what": "customers with no subscription", "n": 3},
  {"what": "subscriptions", "n": 42},
  {"what": "invoices", "n": 119},
  {"what": "charges", "n": 110},
  {"what": "failed charges", "n": 2},
  {"what": "refunds", "n": 2},
  {"what": "disputes", "n": 1},
  {"what": "payouts", "n": 19},
  {"what": "pending payouts", "n": 1},
  {"what": "products", "n": 5},
  {"what": "prices", "n": 8},
  {"what": "coupons", "n": 2},
  {"what": "events", "n": 149}
]
```

Subscriptions by status.

```sql landmark=small_co/status_counts
SELECT status, count(*) AS n FROM subscriptions GROUP BY status ORDER BY status
```

```json rows
[
  {"status": "active", "n": 29},
  {"status": "canceled", "n": 7},
  {"status": "incomplete_expired", "n": 1},
  {"status": "past_due", "n": 1},
  {"status": "paused", "n": 1},
  {"status": "trialing", "n": 3}
]
```

### Loose ends

Each is one query away; the customer's email is the stable handle.

| Loose end | Who | State |
|---|---|---|
| Open dispute | Grace Adeyemi, Brightside Music Studio (`grace@brightsidemusic.studio`) | Bought Studio annual on 2026-08-30 with her own card, then paid $49 for *Done-for-you setup* with a second card that afternoon. That payment is disputed as `fraudulent`, `needs_response`, evidence due 2026-09-07T23:59:59Z |
| Past due, mid-dunning | Leo Marchetti (`leo@marchettichem.com`) | The 2026-08-30 renewal declined on card `...0341`, one retry failed; `attempt_count` 2, next attempt 2026-09-02T22:00Z |
| Trial ending without a card | Sofia Lindqvist (`sofia@lindqvistvoice.com`) | `trialing` until 2026-09-03T15:00Z, then pauses |
| Paused | Adaeze Okafor (`adaeze@okaforpiano.com`) | Trial ended 2026-08-15 with no card |
| Founding renewals this week | Daniela Ruiz, Marcus Bell, Owen Fitzgerald, Hana Kobayashi, Tomasz Wójcik | $96 renewals 09-03 to 09-08; Owen's card (`...0341`) will decline |
| Cancel at period end | Priya Natarajan (`priya@natarajanpiano.com`) | Founding plan ends 2026-09-05 ("Retiring from teaching") |
| Credit balance | Nadia Haddad (`nadia@haddadfrench.com`) | Downgraded Studio to Solo on 2026-08-20, invoiced at once; balance -$12.60 |
| Partial refund | Jamal Carter (`jamal@carterdrums.com`) | $170 of his $290 Studio annual charge refunded when he moved to Solo annual |
| Pending invoice item | Elena Petrova, Petrova Dance Academy (`elena@petrovadance.com`) | A $49 setup item ordered 2026-08-28, not yet on an invoice ([#14](https://github.com/Kiln-AI/stripe_world/issues/14): her renewal will not pick it up) |
| Duplicate customer | Chris Nguyen (`chris@nguyentutoring.com`, `chris.nguyen@cnlessons.com`) | Two records; the second trial canceled on day three |
| Founder test | Test (do not bill) (`test+launch@wrenfield.app`) | A $1.00 payment on launch morning, refunded in full |

The open dispute and the payment it disputes.

```sql landmark=small_co/open_dispute
SELECT c.name, c.email, d.id, d.reason, d.amount, ch.description AS disputed_payment,
       datetime(json_extract(d.evidence_details, '$.due_by'), 'unixepoch') AS due_by
FROM disputes d
JOIN charges ch ON ch.id = d.charge
JOIN customers c ON c.id = ch.customer
WHERE d.status = 'needs_response'
ORDER BY d.id
```

```json rows
[
  {"name": "Grace Adeyemi", "email": "grace@brightsidemusic.studio", "id": "du_3x0hP2nFd3kT9mZaZMA5AmXz", "reason": "fraudulent", "amount": 4900, "disputed_payment": "Done-for-you setup", "due_by": "2026-09-07T23:59:59.000Z"}
]
```

A subscription past due, mid-dunning.

```sql landmark=small_co/past_due
SELECT c.name, c.email, i.number, i.amount_due, i.attempt_count, i.next_payment_attempt
FROM subscriptions s
JOIN customers c ON c.id = s.customer
JOIN invoices i ON i.id = s.latest_invoice
WHERE s.status = 'past_due'
ORDER BY s.id
```

```json rows
[
  {"name": "Leo Marchetti", "email": "leo@marchettichem.com", "number": "I2VA3RTL-0005", "amount_due": 1200, "attempt_count": 2, "next_payment_attempt": "2026-09-02T22:00:00.000Z"}
]
```

Trials that will pause at their end, for want of a card.

```sql landmark=small_co/trial_ending_without_card
SELECT c.name, c.email, s.trial_end,
       json_extract(s.trial_settings, '$.end_behavior.missing_payment_method') AS at_trial_end
FROM subscriptions s
JOIN customers c ON c.id = s.customer
WHERE s.status = 'trialing'
  AND json_extract(c.invoice_settings, '$.default_payment_method') IS NULL
ORDER BY s.trial_end, s.id
```

```json rows
[
  {"name": "Sofia Lindqvist", "email": "sofia@lindqvistvoice.com", "trial_end": "2026-09-03T15:00:00.000Z", "at_trial_end": "pause"}
]
```

A trial already paused.

```sql landmark=small_co/paused
SELECT c.name, c.email, s.trial_start, s.trial_end
FROM subscriptions s
JOIN customers c ON c.id = s.customer
WHERE s.status = 'paused'
ORDER BY s.id
```

```json rows
[
  {"name": "Adaeze Okafor", "email": "adaeze@okaforpiano.com", "trial_start": "2026-08-01T18:00:00.000Z", "trial_end": "2026-08-15T18:00:00.000Z"}
]
```

The founding members' renewals due in the next seven days (09-03 to 09-08), with the card each
will be charged on.

```sql landmark=small_co/renewals_this_week
SELECT c.name, si.current_period_end AS renews_at, p.unit_amount,
       json_extract(pm.rail, '$.last4') AS card
FROM subscriptions s
JOIN customers c ON c.id = s.customer
JOIN subscription_items si ON si.subscription = s.id
JOIN prices p ON p.id = si.price
JOIN payment_methods pm ON pm.id = json_extract(c.invoice_settings, '$.default_payment_method')
WHERE s.status = 'active' AND NOT s.cancel_at_period_end
  AND p.lookup_key = 'founding_annual'
  AND si.current_period_end < '2026-09-09T00:00:00.000Z'
ORDER BY si.current_period_end, s.id
```

```json rows
[
  {"name": "Daniela Ruiz", "renews_at": "2026-09-03T16:20:00.000Z", "unit_amount": 9600, "card": "4242"},
  {"name": "Marcus Bell", "renews_at": "2026-09-04T13:05:00.000Z", "unit_amount": 9600, "card": "4444"},
  {"name": "Owen Fitzgerald", "renews_at": "2026-09-06T14:10:00.000Z", "unit_amount": 9600, "card": "0341"},
  {"name": "Hana Kobayashi", "renews_at": "2026-09-07T01:30:00.000Z", "unit_amount": 9600, "card": "1117"},
  {"name": "Tomasz Wójcik", "renews_at": "2026-09-08T22:15:00.000Z", "unit_amount": 9600, "card": "5556"}
]
```

A subscription set to end at its period end. `canceled_at` is the instant the customer asked, as
on Stripe; `cancel_at` is when it ends.

```sql landmark=small_co/cancel_at_period_end
SELECT c.name, c.email, s.cancel_at, s.canceled_at,
       json_extract(s.cancellation_details, '$.feedback') AS feedback,
       json_extract(s.cancellation_details, '$.comment') AS comment
FROM subscriptions s
JOIN customers c ON c.id = s.customer
WHERE s.status = 'active' AND s.cancel_at_period_end
ORDER BY s.id
```

```json rows
[
  {"name": "Priya Natarajan", "email": "priya@natarajanpiano.com", "cancel_at": "2026-09-05T18:40:00.000Z", "canceled_at": "2026-08-11T20:15:00.000Z", "feedback": "other", "comment": "Retiring from teaching"}
]
```

A customer credit balance, and the downgrade that made it.

```sql landmark=small_co/credit_balance
SELECT c.name, c.email, c.balance, t.type, t.amount, t.created
FROM customers c
JOIN customer_balance_transactions t ON t.customer = c.id
WHERE c.balance < 0
ORDER BY t.x_seq
```

```json rows
[
  {"name": "Nadia Haddad", "email": "nadia@haddadfrench.com", "balance": -1260, "type": "applied_to_invoice", "amount": -1260, "created": "2026-08-20T16:00:00.000Z"}
]
```

A charge refunded in part.

```sql landmark=small_co/partial_refund
SELECT c.name, c.email, ch.amount, ch.amount_refunded, r.created AS refunded_at
FROM refunds r
JOIN charges ch ON ch.id = r.charge
JOIN customers c ON c.id = ch.customer
WHERE ch.amount_refunded < ch.amount
ORDER BY r.id
```

```json rows
[
  {"name": "Jamal Carter", "email": "jamal@carterdrums.com", "amount": 29000, "amount_refunded": 17000, "refunded_at": "2026-07-17T15:31:00.000Z"}
]
```

A one-time invoice item not yet on an invoice.

```sql landmark=small_co/pending_invoice_item
SELECT c.name, c.email, ii.id, ii.amount, ii.description, ii.date
FROM invoiceitems ii
JOIN customers c ON c.id = ii.customer
WHERE ii.invoice IS NULL
ORDER BY ii.id
```

```json rows
[
  {"name": "Elena Petrova", "email": "elena@petrovadance.com", "id": "ii_1wzxainFd3kT9mZaX0wJyYpy", "amount": 4900, "description": "Done-for-you setup", "date": "2026-08-28T14:30:00.000Z"}
]
```

Two customer records for one person, the first one first.

```sql landmark=small_co/duplicate_customers
SELECT a.name, a.email AS first_email, sa.status AS first_status,
       b.email AS second_email, sb.status AS second_status,
       json_extract(sb.cancellation_details, '$.comment') AS second_closed_because
FROM customers a
JOIN customers b ON b.name = a.name AND b.created > a.created
LEFT JOIN subscriptions sa ON sa.customer = a.id
LEFT JOIN subscriptions sb ON sb.customer = b.id
ORDER BY a.created, a.id
```

```json rows
[
  {"name": "Chris Nguyen", "first_email": "chris@nguyentutoring.com", "first_status": "active", "second_email": "chris.nguyen@cnlessons.com", "second_status": "canceled", "second_closed_because": "Duplicate account"}
]
```

The founder's own test record: a $1.00 payment on launch morning, refunded the same day.

```sql landmark=small_co/founder_test
SELECT c.name, c.email, ch.amount, ch.amount_refunded, ch.description, ch.created,
       (SELECT count(*) FROM subscriptions s WHERE s.customer = c.id) AS subscriptions
FROM customers c
JOIN charges ch ON ch.customer = c.id
WHERE c.name = 'Test (do not bill)'
ORDER BY ch.id
```

```json rows
[
  {"name": "Test (do not bill)", "email": "test+launch@wrenfield.app", "amount": 100, "amount_refunded": 100, "description": "Launch smoke test", "created": "2026-03-02T13:05:00.000Z", "subscriptions": 0}
]
```

### The cast

Every customer, in the order they signed up, with their subscription's status and plan.

```sql landmark=small_co/cast
SELECT c.name, c.email, c.description, s.status, p.lookup_key
FROM customers c
LEFT JOIN subscriptions s ON s.customer = c.id
LEFT JOIN subscription_items si ON si.subscription = s.id
LEFT JOIN prices p ON p.id = si.price
ORDER BY c.created, c.id
```

```json rows
[
  {"name": "Daniela Ruiz", "email": "daniela@ruizviolin.com", "description": "Violin teacher", "status": "active", "lookup_key": "founding_annual"},
  {"name": "Marcus Bell", "email": "marcus@bellprep.net", "description": "SAT and ACT tutor", "status": "active", "lookup_key": "founding_annual"},
  {"name": "Priya Natarajan", "email": "priya@natarajanpiano.com", "description": "Piano teacher", "status": "active", "lookup_key": "founding_annual"},
  {"name": "Owen Fitzgerald", "email": "owen@fitzrunning.co", "description": "Running coach", "status": "active", "lookup_key": "founding_annual"},
  {"name": "Hana Kobayashi", "email": "hana@kobayashimath.com", "description": "Math tutor", "status": "active", "lookup_key": "founding_annual"},
  {"name": "Tomasz Wójcik", "email": "tomasz@wojcikguitar.com", "description": "Guitar teacher", "status": "active", "lookup_key": "founding_annual"},
  {"name": "Test (do not bill)", "email": "test+launch@wrenfield.app", "description": "Founder test account", "status": null, "lookup_key": null},
  {"name": "Amara Osei", "email": "amara@oseireading.com", "description": "Reading tutor", "status": "active", "lookup_key": "solo_monthly"},
  {"name": "Ben Carlisle", "email": "ben@carlisleguitar.com", "description": "Guitar teacher", "status": "canceled", "lookup_key": "solo_monthly"},
  {"name": "Lucas Moreau", "email": "lucas@moreaucello.com", "description": "Cello teacher", "status": "active", "lookup_key": "solo_monthly"},
  {"name": "Tom Okonkwo", "email": "tom@riverbendmusic.org", "description": "Riverbend Music School", "status": "active", "lookup_key": "studio_monthly"},
  {"name": "Sarah Goldberg", "email": "sarah@goldberglsat.com", "description": "LSAT tutor", "status": "active", "lookup_key": "solo_monthly"},
  {"name": "Anika Sharma", "email": "anika@sharmatutoring.com", "description": "Sharma Tutoring Collective", "status": "active", "lookup_key": "studio_monthly"},
  {"name": "Olivia Grant", "email": "olivia@grantprep.com", "description": "SAT tutor", "status": "canceled", "lookup_key": "solo_monthly"},
  {"name": "Diego Herrera", "email": "diego@herrerafutbol.com", "description": "Youth soccer coach", "status": "active", "lookup_key": "solo_monthly"},
  {"name": "Mei Lin", "email": "mei@linmandarin.com", "description": "Mandarin tutor", "status": "active", "lookup_key": "solo_monthly"},
  {"name": "Ellen Park", "email": "ellen@parkyoga.studio", "description": "Yoga teacher", "status": "active", "lookup_key": "solo_annual"},
  {"name": "Paolo Ricci", "email": "paolo@riccivoice.com", "description": "Ricci Voice Studio", "status": "active", "lookup_key": "studio_annual"},
  {"name": "Marco Bianchi", "email": "marco@bianchiitaliano.com", "description": "Italian tutor", "status": "canceled", "lookup_key": "solo_monthly"},
  {"name": "Elena Petrova", "email": "elena@petrovadance.com", "description": "Petrova Dance Academy", "status": "active", "lookup_key": "studio_monthly"},
  {"name": "Jordan Blake", "email": "jordan@blakestrength.com", "description": "Personal trainer", "status": "active", "lookup_key": "solo_monthly"},
  {"name": "Yuki Tanaka", "email": "yuki@tanakaart.studio", "description": "Art teacher", "status": "canceled", "lookup_key": "studio_monthly"},
  {"name": "Claire Dubois", "email": "claire@duboislanguages.com", "description": "Dubois Language Studio", "status": "active", "lookup_key": "studio_monthly"},
  {"name": "Rachel Stein", "email": "rachel@steintutoring.com", "description": "Test-prep tutor", "status": null, "lookup_key": null},
  {"name": "Leo Marchetti", "email": "leo@marchettichem.com", "description": "Chemistry tutor", "status": "past_due", "lookup_key": "solo_monthly"},
  {"name": "Chris Nguyen", "email": "chris@nguyentutoring.com", "description": "Math and science tutoring", "status": "active", "lookup_key": "solo_monthly"},
  {"name": "Nadia Haddad", "email": "nadia@haddadfrench.com", "description": "French tutor", "status": "active", "lookup_key": "solo_monthly"},
  {"name": "Rosa Delgado", "email": "rosa@delgadospanish.com", "description": "Spanish tutor", "status": "canceled", "lookup_key": "solo_monthly"},
  {"name": "Fatima Rahman", "email": "fatima@rahmanbio.com", "description": "Biology tutor", "status": "active", "lookup_key": "solo_monthly"},
  {"name": "June Harper", "email": "june@harperswim.com", "description": "Harper Swim School", "status": "active", "lookup_key": "studio_annual"},
  {"name": "Ravi Iyer", "email": "ravi@iyercodes.com", "description": "Coding tutor for kids", "status": "active", "lookup_key": "solo_annual"},
  {"name": "Sam Rivera", "email": "sam@peakclimbcoach.com", "description": "Peak Climbing Coaching", "status": "active", "lookup_key": "studio_monthly"},
  {"name": "Chris Nguyen", "email": "chris.nguyen@cnlessons.com", "description": "Tutoring", "status": "canceled", "lookup_key": "solo_monthly"},
  {"name": "Victor Alves", "email": "victor@alvesswim.com", "description": "Swim coach", "status": "incomplete_expired", "lookup_key": "solo_monthly"},
  {"name": "Jamal Carter", "email": "jamal@carterdrums.com", "description": "Drum teacher", "status": "active", "lookup_key": "solo_annual"},
  {"name": "Kofi Mensah", "email": "kofi@mensahchess.com", "description": "Chess coach", "status": "active", "lookup_key": "solo_monthly"},
  {"name": "Dev Patel", "email": "dev@patelphysics.com", "description": "Physics tutor", "status": "canceled", "lookup_key": "solo_monthly"},
  {"name": "Isabel Santos", "email": "isabel@santosflute.com", "description": "Flute teacher", "status": "active", "lookup_key": "solo_monthly"},
  {"name": "Hannah Weber", "email": "hannah@weberdeutsch.com", "description": "German tutor", "status": "active", "lookup_key": "solo_annual"},
  {"name": "Adaeze Okafor", "email": "adaeze@okaforpiano.com", "description": "Piano teacher", "status": "paused", "lookup_key": "solo_monthly"},
  {"name": "Malik Ortiz", "email": "malik@ortizbrass.com", "description": "Brass teacher", "status": null, "lookup_key": null},
  {"name": "Sofia Lindqvist", "email": "sofia@lindqvistvoice.com", "description": "Voice coach", "status": "trialing", "lookup_key": "solo_monthly"},
  {"name": "Noah Klein", "email": "noah@kleinmath.net", "description": "Math tutor", "status": "trialing", "lookup_key": "studio_monthly"},
  {"name": "Leila Farah", "email": "leila@farahmusic.com", "description": "Oud teacher", "status": "trialing", "lookup_key": "solo_monthly"},
  {"name": "Grace Adeyemi", "email": "grace@brightsidemusic.studio", "description": "Brightside Music Studio", "status": "active", "lookup_key": "studio_annual"}
]
```

## big_co

**Ostrander Systems** is a Series B company in Denver, Colorado, with about 150 employees. Its
product, **Ostrander FieldOps**, is scheduling, dispatch, quoting and a technician app for HVAC,
plumbing and electrical contractors. The account (`acct_1OsTr4ndXq8NvK2e`, test mode) opened on
2025-02-10. In migration week (2025-03-03 to 03-07) the existing customers moved over from a legacy
billing system, each keeping its old renewal date as a `trial_end` and carrying
`metadata.legacy_account_id` (`OS-` plus six digits) and `metadata.customer_since`. Organic and
sales-led signups followed, rising over the 18 months to `NOW`. A six-hour outage on 2026-04-14
was answered with SLA credit notes to Business and Enterprise customers from 2026-04-20 to 04-24.

The history is simulated: each customer's life (trial, conversion, seat changes, upgrades,
churn, declines and dunning, refunds, disputes, credit notes) is drawn from fixed rates by
`fixtures_src/big_co/`, and the loose ends below are scripted on top, so they are present on
every build. The population is **about 380 customers**, a scaled-down version of the about 3,000
the design describes (`fixtures_src/big_co/shape.py` says why): the file stays at 25 MB or less.

**Opening it.** `world.instance("big_co")` (or `env.reset(fixture="big_co")`, or
`--fixture big_co` for the HTTP server) opens the account in test mode; the tools address it as
`stripe_context="acct_1OsTr4ndXq8NvK2e"`, `livemode=False`. Its display name is *Ostrander
Systems*, its business name *Ostrander FieldOps*, at `https://ostranderfieldops.com`, billing
support `billing@ostranderfieldops.com`, +1 720 555 0147, Denver, CO 80202, timezone
`America/Denver`; payouts go to one test bank account (`STRIPE TEST BANK`, `…6789`).

**Catalog.** Seat plans priced per technician (the subscription item's quantity), each monthly
or annual at ten times monthly: *FieldOps Starter* (`starter_monthly` $39, `starter_annual` $390),
*FieldOps Pro* (`pro_monthly` $69, `pro_annual` $690) and *FieldOps Business* (`business_monthly`
$99, `business_annual` $990). *FieldOps Enterprise* has no standard price: each deal gets its own
yearly per-technician price, nicknamed `Enterprise — <company> <year>`. Add-ons on the same
interval: *Fleet Tracking* (`fleet_monthly` $12 per vehicle, `fleet_annual` $120) and *SMS
Reminders* (`sms_monthly` $29 flat, `sms_annual` $290). *Onboarding* packages are one-time
(`onboarding_pro` $499, `onboarding_business` $1,499, `onboarding_enterprise` $4,999), invoiced by
sales on `send_invoice` terms; each invoice item also mints its own one-off price and product, as
Stripe does. *Legacy Starter* (`legacy_starter_monthly` $29) and *Legacy Pro*
(`legacy_pro_monthly` $59) were archived after migration week; part of the migrated cohort still
pays them. Coupons: `SWITCH25` (25% off the first invoice, for customers leaving a competitor),
the trade-association codes `MSPG10`, `FRHA10` and `RMEC10` (10% off forever), and one-off
"Sales concession" amount-off coupons. Sales tax is six manual tax rates (TX 6.25%, WA 6.5%, NY 4%,
PA 6%, OH 5.75%, TN 7%), applied by the customer's state; three public buyers are `tax_exempt`.

**How they pay.** Starter, Pro and Business are charged to a card automatically; self-serve
Starter and Pro signups start a 14-day trial with a card, and Business trials set up by sales
have no card and pause if none is added. Enterprise contracts are `send_invoice`, 30 days, paid by
card or marked paid outside Stripe (check or wire). Dunning is 8 tries over 3 weeks, then
`unpaid`; customer success closes an unpaid subscription 45 days later. Payouts are manual, every
business day at 15:00Z, of the whole available balance.

**One-off services and disputes.** Card customers now and then buy a one-off service (*On-site
crew training day* $899, *Legacy data import* $499, *Custom dispatch report build* $349,
*Technician app rollout session* $249), charged on the spot as a PaymentIntent with that
description and no invoice. Every dispute in the account is on one of these payments: a few were
paid with one of Stripe's dispute test cards, which opens the dispute as the payment confirms.
Subscription renewals are never disputed. Older disputes are settled (won with evidence, or lost by
acceptance or losing evidence); those from the last 21 days are still open or under review. Two
settled ones are scripted, Kestrel Ridge Heating's (`won`) and Millrace Electric's (`lost`), so
every dispute stage is present at this population size.

**Real-account mess.** Duplicate customers (the same company under a variant name, with another
contact email), migrated customers with no email, inconsistent CRM metadata keys
(`crm_account_id`, `CRM ID`, `crmAccountId`, each holding a 15-character `006…` record id),
customer-success notes in `description`, and leftover draft invoices on unpaid subscriptions that
were later closed.

**Counts at `NOW`.** All 30 days of events are kept, and nothing older.

```sql landmark=big_co/counts
SELECT what, n FROM (
  SELECT 1 AS k, 'customers' AS what, count(*) AS n FROM customers
  UNION ALL SELECT 2, 'migrated customers', count(*) FROM customers
    WHERE json_extract(metadata, '$.legacy_account_id') IS NOT NULL
  UNION ALL SELECT 3, 'subscriptions', count(*) FROM subscriptions
  UNION ALL SELECT 4, 'subscription schedules', count(*) FROM subscription_schedules
  UNION ALL SELECT 5, 'invoices', count(*) FROM invoices
  UNION ALL SELECT 6, 'open invoices', count(*) FROM invoices WHERE status = 'open'
  UNION ALL SELECT 7, 'charges', count(*) FROM charges
  UNION ALL SELECT 8, 'failed charges', count(*) FROM charges WHERE status = 'failed'
  UNION ALL SELECT 9, 'refunds', count(*) FROM refunds
  UNION ALL SELECT 10, 'disputes', count(*) FROM disputes
  UNION ALL SELECT 11, 'credit notes', count(*) FROM credit_notes
  UNION ALL SELECT 12, 'payouts', count(*) FROM payouts
  UNION ALL SELECT 13, 'products', count(*) FROM products
  UNION ALL SELECT 14, 'prices', count(*) FROM prices
  UNION ALL SELECT 15, 'coupons', count(*) FROM coupons
  UNION ALL SELECT 16, 'events', count(*) FROM events
) ORDER BY k
```

```json rows
[
  {"what": "customers", "n": 379},
  {"what": "migrated customers", "n": 167},
  {"what": "subscriptions", "n": 378},
  {"what": "subscription schedules", "n": 6},
  {"what": "invoices", "n": 3024},
  {"what": "open invoices", "n": 43},
  {"what": "charges", "n": 2821},
  {"what": "failed charges", "n": 163},
  {"what": "refunds", "n": 71},
  {"what": "disputes", "n": 20},
  {"what": "credit notes", "n": 78},
  {"what": "payouts", "n": 376},
  {"what": "products", "n": 79},
  {"what": "prices", "n": 100},
  {"what": "coupons", "n": 9},
  {"what": "events", "n": 1208}
]
```

### Loose ends

| Loose end | Who | State |
|---|---|---|
| Dispute queue | Arroyo Air & Heat, Tamarack Plumbing, Gridline Electric Co., Sandstone Mechanical | One-off services payments of 2026-08-26 to 08-29 disputed (`fraudulent`, `product_not_received`), `needs_response`, evidence due 2026-09-03 to 09-06 |
| Inquiries and a review | Ridgeway Heating, Larkspur Electric, Coldwater Plumbing Supply | Services payments of 08-15, 08-18 and 08-12: `warning_needs_response`; `warning_under_review` (evidence 08-21); `under_review` (evidence 08-16) |
| Overdue contract | Halvorsen Mechanical Group | 60 seats at $85/tech/month: the $61,200 renewal of 2026-06-18 is open, due 2026-07-18, 45 days past due |
| Ramp about to step | Cascade Comfort Systems | Ramp schedule in its first phase: 80 seats until 2026-09-04, then 120, then 160 from 2026-12-04 to 2027-06-04 |
| Churn risk | Copperline Mechanical | Business annual, 48 technicians, set to cancel at period end on 2026-09-13 (`switched_service`) |
| Every status | Bristlecone Electric (`incomplete`, signed up three hours ago, payment needs authentication); Juniper Flats Plumbing, Kettle Creek Electric, Mesquite Drain & Sewer (`incomplete_expired`); Bellwether Climate Services, Northgate Electrical Contractors, Pinyon Ridge Mechanical (`paused`, card-less sales trials); Foxglove Plumbing, Driftwood Heating & Air, Yellowjacket Electric (`unpaid`); Saltgrass Mechanical, Quillback Plumbing, Harrowgate Electric (`past_due`) | Plus the simulated `trialing`, `active` and `canceled` |
| Drafts waiting to finalize | Thornfield Air Systems, Wolf Creek Plumbing & Heating, Lodestar Electrical Services, Ironbark Mechanical, Bluestem Comfort Co. | $1,499 onboarding invoices created this morning, `automatically_finalizes_at` after `NOW` |
| Payout in flight | | The 2026-08-31 payout arrives 2026-09-02 |

The queries below find each one, with the rows they return in the committed build.

The dispute queue: chargebacks awaiting a response, evidence due within seven days.

```sql landmark=big_co/dispute_queue
SELECT c.name, d.id, d.reason, d.amount, ch.description AS disputed_payment,
       datetime(json_extract(d.evidence_details, '$.due_by'), 'unixepoch') AS due_by
FROM disputes d
JOIN charges ch ON ch.id = d.charge
JOIN customers c ON c.id = ch.customer
WHERE d.status = 'needs_response'
  AND json_extract(d.evidence_details, '$.due_by') <= unixepoch('2026-09-01 14:00:00', '+7 days')
ORDER BY json_extract(d.evidence_details, '$.due_by'), d.id
```

```json rows
[
  {"name": "Arroyo Air & Heat", "id": "du_3wzG2indXq8NvK2eYEkF4hwL", "reason": "fraudulent", "amount": 89900, "disputed_payment": "On-site crew training day", "due_by": "2026-09-03T23:59:59.000Z"},
  {"name": "Tamarack Plumbing", "id": "du_3wzerQndXq8NvK2eoW3vdJHR", "reason": "product_not_received", "amount": 49900, "disputed_payment": "Legacy data import", "due_by": "2026-09-04T23:59:59.000Z"},
  {"name": "Gridline Electric Co.", "id": "du_3wzyIGndXq8NvK2eljGswQjM", "reason": "fraudulent", "amount": 89900, "disputed_payment": "On-site crew training day", "due_by": "2026-09-05T23:59:59.000Z"},
  {"name": "Sandstone Mechanical", "id": "du_3x0PvAndXq8NvK2eyolfeGjX", "reason": "product_not_received", "amount": 24900, "disputed_payment": "Technician app rollout session", "due_by": "2026-09-06T23:59:59.000Z"}
]
```

Every dispute by stage, open and settled.

```sql landmark=big_co/dispute_stages
SELECT status, count(*) AS n FROM disputes GROUP BY status ORDER BY status
```

```json rows
[
  {"status": "lost", "n": 9},
  {"status": "needs_response", "n": 4},
  {"status": "under_review", "n": 1},
  {"status": "warning_needs_response", "n": 1},
  {"status": "warning_under_review", "n": 1},
  {"status": "won", "n": 4}
]
```

Invoices on terms that are past due.

```sql landmark=big_co/overdue_enterprise
SELECT c.name, i.number, i.total, i.due_date
FROM invoices i
JOIN customers c ON c.id = i.customer
WHERE i.status = 'open' AND i.collection_method = 'send_invoice'
  AND i.due_date < '2026-09-01T14:00:00.000Z'
ORDER BY i.due_date, i.id
```

```json rows
[
  {"name": "Halvorsen Mechanical Group", "number": "37JR1KWW-0002", "total": 6120000, "due_date": "2026-07-18T17:00:00.000Z"}
]
```

The Enterprise ramps, the soonest step first.

```sql landmark=big_co/next_ramp_step
SELECT c.name, s.id,
       datetime(json_extract(s.current_phase, '$.end_date'), 'unixepoch') AS steps_at,
       json_extract(s.phases, '$[0].items[0].quantity') AS seats_now,
       json_extract(s.phases, '$[1].items[0].quantity') AS seats_next
FROM subscription_schedules s
JOIN customers c ON c.id = s.customer
WHERE s.status = 'active' AND json_array_length(s.phases) = 3
ORDER BY json_extract(s.current_phase, '$.end_date'), s.id
```

```json rows
[
  {"name": "Cascade Comfort Systems", "id": "sub_sched_1wVAUCndXq8NvK2eVtGeVLWU", "steps_at": "2026-09-04T16:00:00.000Z", "seats_now": 80, "seats_next": 120},
  {"name": "Becerra Mechanical", "id": "sub_sched_1wizasndXq8NvK2eLLOifL8G", "steps_at": "2026-10-12T19:12:02.000Z", "seats_now": 51, "seats_next": 76},
  {"name": "Fitzpatrick & Sons Comfort Systems", "id": "sub_sched_1wncGFndXq8NvK2exqluBYUt", "steps_at": "2026-10-25T13:17:51.000Z", "seats_now": 44, "seats_next": 66},
  {"name": "Wagner Brothers Plumbing", "id": "sub_sched_1wxm7ondXq8NvK2e4pgenpqf", "steps_at": "2026-11-22T13:51:08.000Z", "seats_now": 54, "seats_next": 81}
]
```

Business plans set to end within 30 days, the largest first.

```sql landmark=big_co/churn_risk
SELECT c.name, s.id, si.quantity AS technicians, p.lookup_key, s.cancel_at
FROM subscriptions s
JOIN customers c ON c.id = s.customer
JOIN subscription_items si ON si.subscription = s.id
JOIN prices p ON p.id = si.price
WHERE s.status = 'active' AND s.cancel_at_period_end
  AND p.lookup_key LIKE 'business_%'
  AND s.cancel_at <= '2026-10-01T14:00:00.000Z'
ORDER BY si.quantity DESC, s.id
```

```json rows
[
  {"name": "Copperline Mechanical", "id": "sub_1uxRjMndXq8NvK2eADYbyp7z", "technicians": 48, "lookup_key": "business_annual", "cancel_at": "2026-09-13T15:00:00.000Z"}
]
```

Subscriptions by status: all eight are present.

```sql landmark=big_co/status_counts
SELECT status, count(*) AS n FROM subscriptions GROUP BY status ORDER BY status
```

```json rows
[
  {"status": "active", "n": 220},
  {"status": "canceled", "n": 134},
  {"status": "incomplete", "n": 1},
  {"status": "incomplete_expired", "n": 3},
  {"status": "past_due", "n": 4},
  {"status": "paused", "n": 8},
  {"status": "trialing", "n": 5},
  {"status": "unpaid", "n": 3}
]
```

Draft invoices made in the last hour, waiting on `automatically_finalizes_at`.

```sql landmark=big_co/draft_invoices
SELECT c.name, i.id, i.total, i.created, i.automatically_finalizes_at
FROM invoices i
JOIN customers c ON c.id = i.customer
WHERE i.status = 'draft' AND i.created > '2026-09-01T13:00:00.000Z'
ORDER BY i.created, i.id
```

```json rows
[
  {"name": "Thornfield Air Systems", "id": "in_1x1OAendXq8NvK2eIjGn36yC", "total": 149900, "created": "2026-09-01T13:05:00.000Z", "automatically_finalizes_at": "2026-09-01T14:05:01.000Z"},
  {"name": "Wolf Creek Plumbing & Heating", "id": "in_1x1OKKndXq8NvK2eCZJcRNNe", "total": 149900, "created": "2026-09-01T13:15:00.000Z", "automatically_finalizes_at": "2026-09-01T14:15:01.000Z"},
  {"name": "Lodestar Electrical Services", "id": "in_1x1OU0ndXq8NvK2egQ2S9gHp", "total": 149900, "created": "2026-09-01T13:25:00.000Z", "automatically_finalizes_at": "2026-09-01T14:25:01.000Z"},
  {"name": "Ironbark Mechanical", "id": "in_1x1OdgndXq8NvK2eMBoGZwQl", "total": 149900, "created": "2026-09-01T13:35:00.000Z", "automatically_finalizes_at": "2026-09-01T14:35:01.000Z"},
  {"name": "Bluestem Comfort Co.", "id": "in_1x1OnMndXq8NvK2exXGkU8Qq", "total": 149900, "created": "2026-09-01T13:45:00.000Z", "automatically_finalizes_at": "2026-09-01T14:45:01.000Z"}
]
```

Payouts not yet arrived.

```sql landmark=big_co/pending_payouts
SELECT id, amount, created, arrival_date FROM payouts
WHERE status = 'pending'
ORDER BY created, id
```

```json rows
[
  {"id": "po_1x13UOndXq8NvK2eSpXYQuzh", "amount": 6610105, "created": "2026-08-31T15:00:00.000Z", "arrival_date": "2026-09-02T00:00:00.000Z"}
]
```

Downgrades set for the next renewal.

```sql landmark=big_co/scheduled_downgrades
SELECT c.name, s.id, now_price.lookup_key AS plan_now, later_price.lookup_key AS plan_at_renewal,
       datetime(json_extract(s.phases, '$[0].end_date'), 'unixepoch') AS renews_at
FROM subscription_schedules s
JOIN customers c ON c.id = s.customer
JOIN prices now_price ON now_price.id = json_extract(s.phases, '$[0].items[0].price')
JOIN prices later_price ON later_price.id = json_extract(s.phases, '$[1].items[0].price')
WHERE s.status = 'active' AND json_array_length(s.phases) = 2
ORDER BY renews_at, s.id
```

```json rows
[
  {"name": "Benjamin Zamora Comfort Systems", "id": "sub_sched_1wvQ6lndXq8NvK2emROEywCl", "plan_now": "pro_monthly", "plan_at_renewal": "starter_monthly", "renews_at": "2026-09-09T20:53:29.000Z"},
  {"name": "Nwosu Brothers HVAC", "id": "sub_sched_1x0gWIndXq8NvK2ejRNDsVo5", "plan_now": "pro_monthly", "plan_at_renewal": "starter_monthly", "renews_at": "2026-09-17T21:07:57.000Z"}
]
```

Duplicate customers: the same company under a variant name ("&" or "and", an added "LLC" or
"Inc.", a dropped "Co."), the original first.

```sql landmark=big_co/duplicate_customers
WITH normalized AS (
  SELECT id, name, email, created,
         trim(replace(replace(replace(replace(lower(name), '&', 'and'), ' llc', ''),
                              ' inc.', ''), ' co.', '')) AS company
  FROM customers
)
SELECT a.name AS original, a.email AS original_email, b.name AS duplicate,
       b.email AS duplicate_email
FROM normalized a
JOIN normalized b ON b.company = a.company AND b.created > a.created
ORDER BY a.created, a.id
```

```json rows
[
  {"original": "Callum Guerrero Plumbing", "original_email": "billing@callumguerreroplumbing.com", "duplicate": "Callum Guerrero Plumbing LLC", "duplicate_email": "office@callumguerreroplumbing.com"},
  {"original": "Kemper & Sons Heating & Air", "original_email": "billing@kemperandsonsheatingandair.com", "duplicate": "Kemper and Sons Heating and Air", "duplicate_email": "aaliyah.velasquez@kemperandsonsheatingandair.com"},
  {"original": "Vu Mechanical", "original_email": "office@vumechanical.com", "duplicate": "Vu Mechanical Inc.", "duplicate_email": "abdul.nakagawa@vumechanical.com"}
]
```

The migration cohort beside everyone who signed up since.

```sql landmark=big_co/migration_cohort
SELECT json_extract(metadata, '$.legacy_account_id') IS NOT NULL AS migrated, count(*) AS n
FROM customers
GROUP BY migrated
ORDER BY migrated
```

```json rows
[
  {"migrated": 0, "n": 212},
  {"migrated": 1, "n": 167}
]
```

The SLA credits for the April 14 outage: how many, and their total in cents.

```sql landmark=big_co/sla_credits
SELECT count(*) AS n, sum(total) AS total, min(created) AS first, max(created) AS last
FROM credit_notes
WHERE memo = 'Service credit: April 14 outage'
ORDER BY n
```

```json rows
[
  {"n": 71, "total": 18634292, "first": "2026-04-20T15:00:00.000Z", "last": "2026-04-24T22:27:00.000Z"}
]
```
