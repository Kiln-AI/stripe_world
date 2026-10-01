<p align="center">
  <img width="200" height="150" alt="Stripe World logo, a remix of the Seahaven logo" src="https://github.com/user-attachments/assets/aec87601-05bb-487c-8bff-ed083de83564" />
</p>
<h3 align="center">
  A stateful Stripe sandbox for agent evals, RL and tests<br/>
  Built with <a href="https://github.com/Kiln-AI/Seahaven">Seahaven</a>.
</h3>

<p align="center">
  <a href="#getting-started"><strong>Quick Start</strong></a> •
  <a href="#examples"><strong>Examples</strong></a> •
  <a href="#why-it-works-seahaven"><strong>Built with Seahaven</strong></a>
</p>

Agents that use Stripe need somewhere to practise. Test suites need somewhere to charge cards. Real
Stripe is one shared account you can't reset, and a mock forgets every write the moment it answers.

Stripe World is a stateful copy of Stripe's Billing and Payments API. Run a hundred private Stripe
accounts in one process, each starting from a state you chose, with every row each one changed
logged for you to check.

**Stripe World is a Seahaven demo.** It was built to show what a [Seahaven](https://github.com/Kiln-AI/Seahaven) world can be. If you want a realistic copy of *your* system, for your agents or your tests, that's what Seahaven is for.

> **Not affiliated with Stripe.** Stripe's field names, enum values and id prefixes are reused as API
> vocabulary under the MIT licence of Stripe's OpenAPI spec; see
> [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md).

## How real is it?

- **155 Stripe API operations across 24 tables**, pinned to API version `2026-08-26.dahlia`. Every
  object shape aligned to Stripe's OpenAPI spec.
- **A billing engine, not just CRUD.** Subscriptions move through all eight statuses. Invoices go
  from draft to open to paid, void or uncollectible. Proration, dunning, credit notes and a balance
  ledger behave the way Stripe's do, and Stripe's test cards (`tok_visa`,
  `tok_visa_chargeDeclinedInsufficientFunds`, …) succeed, decline and dispute on cue.
- **Stripe's own MCP tools.** Agents get the same tools as Stripe's MCP server, so an agent that works
  here works against the real thing.
- **Stripe's own API/SDK.** It also speaks Stripe's HTTP API. Point an official Stripe SDK at it and
  your code runs unchanged, apart from webhooks.
- **Checked against the real API.** recordings of real Stripe API sessions are replayed against
  it in CI, and every object it returns is validated against Stripe's OpenAPI spec.

|                                         | Stripe World | Stripe test mode | stripe-mock |
|-----------------------------------------|:------------:|:----------------:|:-----------:|
| Stripe's real object shapes             |      ✅      |        ✅        |     ✅      |
| Stateful across calls                   |      ✅      |        ✅        |     ❌      |
| Billing behaviour: subscriptions, invoices, declines | ✅ |    ✅        |     ❌      |
| A private account for every run         |      ✅      |        ❌        |     ✅      |
| Hundreds of runs in parallel            |      ✅      |  ❌ rate-limited |     ✅      |
| Every run starts from a known state     |      ✅      |        ❌        |     ✅      |
| Reproducible: same ids, same timestamps |      ✅      |        ❌        |     ✅      |
| Every change logged for grading         |      ✅      |   events only    |     ❌      |
| No API key or network needed            |      ✅      |        ❌        |     ✅      |
| Webhook delivery                        | ❌ events are queryable | ✅ |     ❌      |
| All of Stripe's API                     | ❌ Billing and Payments core | ✅ |  ✅      |

[stripe-mock](https://github.com/stripe/stripe-mock) is Stripe's own mock server. It answers every
endpoint with a valid, canned response but keeps no state: a customer you create is not there when
you list customers. Stripe test mode is the real thing, but every run shares one account,
and your code can't reset it.

## Why it works: Seahaven

Stripe World is built on [Seahaven](https://github.com/Kiln-AI/Seahaven), a framework for building
synthetic worlds for agent evals, RL and testing. Seahaven provides:

- **Instances in milliseconds.** Every run gets a private copy of a frozen starting state, a
  *fixture*, with its own SQLite database.
- **Reproducibility.** The clock, ids and randomness are controlled: the same fixture and seed
  replay the same run.
- **A change log.** Every row a run changed is recorded, so you grade the outcome, not the
  transcript.
- **Serving.** OpenEnv for eval and RL harnesses and HTTP for software tests, each hosting hundreds
  of instances in one process; MCP for chat apps; and a web console to drive a world by hand.

Stripe World supplies only what is specific to Stripe: the tables, the API operations and the billing rules.

**[Build a world of your own →](https://github.com/Kiln-AI/Seahaven#quickstart)**

## Examples

Each example runs from a checkout of this repository; [Getting started](#getting-started) has the
three commands that make one.

### Agent evals and RL (OpenEnv)

`seahaven serve` hosts Stripe World as an [OpenEnv](https://github.com/huggingface/OpenEnv)
environment, the open standard for RL environments. Every connection gets its own private Stripe
account. Open `http://127.0.0.1:8000/console` to drive one by hand.

```sh
uv run --extra serve seahaven serve
```

This script, `rollouts.py`, runs 100 episodes at once against that server. Each starts from the same fixture and
is graded on what changed in its account:

```py
import asyncio

from seahaven.openenv import SeahavenClient

TASK = "Sign up jenny@example.com as a customer."


async def run_agent(env, tools, task):
    # Your agent goes here: hand `tools` to your model and run its tool calls with `env.call`.
    # This stand-in makes the two calls a good agent would.
    account = (await env.call("list_available_accounts_or_orgs")).result["accounts"][0]
    await env.call(
        "stripe_api_write",
        stripe_api_operation_id="PostCustomers",
        parameters={"email": "jenny@example.com"},
        stripe_context=account["stripe_context"],
        livemode=account["livemode"],
    )


def grade(state) -> float:
    # Grade what changed in the account, not what the agent said it did.
    rows = state.state["db"]["log"]
    signed_up = any(
        row["table"] == "customers"
        and row["op"] == "insert"
        and row["after"]["email"] == "jenny@example.com"
        for row in rows
    )
    return 1.0 if signed_up else 0.0


async def episode(seed: int) -> float:
    async with SeahavenClient(base_url="http://127.0.0.1:8000") as env:
        # A private Stripe test-mode account, copied from the `empty` fixture.
        await env.reset(fixture="empty", seed=seed, startup={"livemode": False})
        tools = await env.list_tools()  # the eight Stripe MCP tools, as JSON schemas
        await run_agent(env, tools, TASK)
        return grade(await env.state())


async def main():
    rewards = await asyncio.gather(*(episode(seed) for seed in range(100)))
    print(f"{len(rewards)} episodes, mean reward {sum(rewards) / len(rewards)}")


asyncio.run(main())
```

```sh
uv run --extra serve python rollouts.py
```

[Kiln](https://kiln.tech) connects to the same server as an OpenEnv client.

### Software tests with the Stripe SDK (HTTP)

`serve_http.py` serves Stripe's HTTP API. Every `/worlds/<id>` is its own Stripe account, created
by the first request that uses it, so every test can have a fresh one. It holds 100 accounts at
once by default (`--max-instances N`, `0` for no limit), so the fixture below deletes each one
when its test ends.

```sh
uv run serve_http.py --reset-options '{"startup": {"livemode": false}}'
```

Save this as `test_billing.py` and run `uv run pytest test_billing.py`:

```py
import uuid

import httpx
import pytest
import stripe

SERVER = "http://127.0.0.1:8000"


@pytest.fixture
def stripe_client():
    # Every test gets its own private Stripe account, created on first use.
    account = f"{SERVER}/worlds/{uuid.uuid4().hex}"
    yield stripe.StripeClient("sk_test_123", base_addresses={"api": account})
    httpx.delete(account)  # and throws it away afterwards


def test_subscription_charges_the_card(stripe_client):
    customer = stripe_client.v1.customers.create(params={"email": "jenny@example.com"})
    card = stripe_client.v1.payment_methods.create(
        params={"type": "card", "card": {"token": "tok_visa"}}  # Stripe's test cards work
    )
    stripe_client.v1.payment_methods.attach(card.id, params={"customer": customer.id})
    price = stripe_client.v1.prices.create(
        params={
            "currency": "usd",
            "unit_amount": 2000,
            "recurring": {"interval": "month"},
            "product_data": {"name": "Pro plan"},
        }
    )

    subscription = stripe_client.v1.subscriptions.create(
        params={
            "customer": customer.id,
            "items": [{"price": price.id}],
            "default_payment_method": card.id,
            "expand": ["latest_invoice"],
        }
    )

    assert subscription.status == "active"
    assert subscription.latest_invoice.status == "paid"
    assert subscription.latest_invoice.amount_paid == 2000


def test_declined_card(stripe_client):
    card = stripe_client.v1.payment_methods.create(
        params={"type": "card", "card": {"token": "tok_visa_chargeDeclinedInsufficientFunds"}}
    )

    with pytest.raises(stripe.CardError) as declined:
        stripe_client.v1.payment_intents.create(
            params={
                "amount": 2000,
                "currency": "usd",
                "payment_method": card.id,
                "confirm": True,
            }
        )

    assert declined.value.http_status == 402
    assert declined.value.code == "card_declined"
    assert declined.value.user_message == "Your card has insufficient funds."
    assert declined.value.error.decline_code == "insufficient_funds"
```

The second test is failure injection: Stripe's test cards decline here exactly as they do in test
mode, so you can test the unhappy paths that are hard to reach against the real API. Any Stripe SDK
works the same way: point its API base at `http://127.0.0.1:8000/worlds/<id>`, with any key.

### Claude Desktop (MCP)

`seahaven mcp` connects Stripe World to Claude Desktop, or any MCP client, so you can work with it
by hand. Add this to `claude_desktop_config.json`, with the path to your checkout:

```json
{
  "mcpServers": {
    "stripe-world": {
      "command": "uv",
      "args": [
        "run", "--directory", "/path/to/stripe_world", "--extra", "mcp",
        "seahaven", "mcp", "--reset-options", "{\"startup\": {\"livemode\": false}}"
      ]
    }
  }
}
```

Then ask Claude things like:

- *"Set up a Pro plan at $20 a month, and sign up jenny@example.com on it, invoiced monthly."*
- *"Give Jenny 20% off for three months."*
- *"Which invoices haven't been paid yet?"*

Claude gets a fresh test-mode account each time it starts the server, and nothing is kept when it
stops. The world provides 8 of the 10 tools on Stripe's MCP server; the documentation search,
implementation planner and feedback tools are not reproduced.

## Fixtures: save your own starting states

A fixture is a frozen Stripe account that every run starts from. Build one with the same tools an
agent uses, and freeze it:

```py
from seahaven_stripe_world import world

TEST_MODE = {"livemode": False}


def stripe_write(inst, operation, **parameters):
    account = inst.call("list_available_accounts_or_orgs")["accounts"][0]
    return inst.call(
        "stripe_api_write",
        stripe_api_operation_id=operation,
        parameters=parameters,
        stripe_context=account["stripe_context"],
        livemode=account["livemode"],
    )


with world.instance(now="2026-09-01T14:00:00.000Z", clock_mode="fixed", startup=TEST_MODE) as inst:
    pro = stripe_write(
        inst,
        "PostPrices",
        currency="usd",
        unit_amount=2000,
        recurring={"interval": "month"},
        product_data={"name": "Pro plan"},
    )
    for n in range(3):
        customer = stripe_write(inst, "PostCustomers", email=f"customer{n}@example.com")
        stripe_write(
            inst,
            "PostSubscriptions",
            customer=customer["id"],
            items=[{"price": pro["id"]}],
            collection_method="send_invoice",
            days_until_due=30,
        )
    inst.freeze("pro_plan", "Three customers on the $20/month Pro plan, each with a draft invoice.")
```

Then start every run from it. Each gets a private copy in milliseconds, and the same seed replays
the same ids and timestamps:

```py
for seed in range(100):
    with world.instance("pro_plan", seed=seed, clock_mode="tick", startup=TEST_MODE) as inst:
        run_agent(inst)  # your agent
        reward = grade(inst.state())  # a dict; the rows it changed are in ["state"]["db"]["log"]
```

The servers take it too: `env.reset(fixture="pro_plan", ...)` over OpenEnv, and
`uv run serve_http.py --reset-options '{"fixture": "pro_plan", "startup": {"livemode": false}}'`
for the HTTP API. Commit the script that builds a fixture alongside it, so it can be rebuilt.
Ready-made fixtures with richer histories are on the way.

## What's modelled

**Resources:** customers, payment methods, products, prices, coupons, promotion codes, tax rates,
payment intents, charges, refunds, disputes, setup intents, balance, balance transactions,
payouts, subscriptions, subscription items, subscription schedules, invoices, invoice items, credit
notes, customer balance transactions and events.

**Billing engine:**

- **Subscriptions:** all eight statuses and their transitions, trials, cancel now or at period end,
  pause, and incomplete expiry.
- **Invoices:** draft, open, paid, void and uncollectible, with auto-advance and both collection
  methods.
- **Proration:** credit and debit lines, rounded the way Stripe rounds them.
- **Dunning:** retry settings, end-of-schedule outcomes and hard declines.
- **Credit notes:** settled by refund, customer balance or out of band.
- **Balance ledger:** a balance transaction for every money movement, with fees, pending and
  available funds, and payouts.
- **Test cards:** Stripe's test card numbers and tokens for declines, disputes and 3D Secure.

**API behaviour:** search on seven resources with Stripe's query language, idempotency keys,
cursor pagination, `expand[]`, and an event for every state change.

## Getting started

```sh
git clone https://github.com/Kiln-AI/stripe_world.git
cd stripe_world
uv sync
```

Then run any example above from the checkout. To work on the world itself, run the checks CI runs:

```sh
uv run ruff format --check && uv run ruff check
uv run ty check
uv run pytest
uv run seahaven check
```

The [technical reference](docs/technical.md) covers the agent surface, coverage, conformance,
conventions and project layout. Coding agents start at [AGENTS.md](AGENTS.md).

## License

Stripe World is not affiliated with Stripe. Stripe's API vocabulary is used under the MIT licence of
its OpenAPI spec; see [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md).

## Created by Kiln AI

Stripe World and Seahaven are built by the team behind [Kiln](https://kiln.tech), a free app and
open-source library for building better AI products. Kiln connects to any Seahaven world: write
scenarios against a fixture, [evaluate](https://kiln.tech/features/evals) your agent on the state
it leaves behind, then [auto-optimize](https://kiln.tech/features/auto-optimize) prompts and models
against those evals.
