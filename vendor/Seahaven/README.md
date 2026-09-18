# Seahaven

**Synthetic worlds for AI agents.** Fake, stateful replicas of the systems your agent works
against, for RL and evals.

[Docs](src/seahaven/docs/index.md) · [PyPI](https://pypi.org/project/seahaven/) · [Kiln AI](https://kiln.tech)

> **Seahaven** *(noun)*
>
> 1. A Python framework for building synthetic worlds for AI agents.
> 2. The town in *The Truman Show*. An entire world built so that one inhabitant believes it is
>    real.

RL and evals need thousands of rollouts, in parallel, each from a known state, each inspectable
afterwards. No real system or staging copy can do that.

Seahaven worlds can: clone the tools your agent uses in production, fork hundreds of private
copies in milliseconds, run an agent in each, see exactly what it changed, then throw them away.

## Features

- **[Stateful](src/seahaven/docs/concepts.md#instance).** Writes change every later read. Each
  instance is its own SQLite database.
- **[Fixtures](src/seahaven/docs/db_schema_and_fixtures.md).** Freeze known starting states like
  `small_startup`, `agency` or `big_co`, and reuse them across evals. Immutable and hash-verified.
- **[Any interface](src/seahaven/docs/authoring.md#writing-a-tool).** Tools for REST
  APIs, sandboxed SQL, search, or any custom format.
- **[Serving](src/seahaven/docs/serving_and_openenv.md).** Hundreds of instances per process, one
  private instance per session, thousands of tool calls per second.
- **[Reproducible](src/seahaven/docs/concepts.md#reproducibility).** Same fixture, same frozen
  clock, same seeded ids: the same run, every time. The clock is frozen in Python and in SQL.
- **[Change log](src/seahaven/docs/state.md).** Every row the agent changed, call by call, in one
  versioned document with the provenance to read it. Grade on state, not on transcripts.
- **[Composable worlds](#composing-worlds).** Add sub-worlds to your world, like a full Stripe
  or Shopify API. Compose, reuse and share worlds.
- **[OpenEnv](src/seahaven/docs/serving_and_openenv.md).** `seahaven serve` is an OpenEnv
  environment. Drive it with any OpenEnv client, in any language, or publish it to Hugging Face.

The [docs index](src/seahaven/docs/index.md) has the full set, and `seahaven docs` prints the
copy that ships with your install.

## Quickstart

**Install:** `uv add seahaven` or `pip install seahaven`. Python 3.14+.

**Scaffold a world:** `uv run seahaven new crm` (replace "crm" with your world's name)

**Build your world:** a schema and a set of tools. Here is an example CRM with one table, a search index and two tools:

```python
import seahaven

world = seahaven.World(
    name="crm",
    version="1.0.0",
    schema="""
    CREATE TABLE contacts (id TEXT PRIMARY KEY, email TEXT NOT NULL, notes TEXT NOT NULL, stage TEXT NOT NULL, updated_at TEXT NOT NULL) STRICT;
    CREATE VIRTUAL TABLE contacts_fts USING fts5(notes, content='contacts');
    """,
    state_format="seahaven.state/1",
)


@world.tool
def create_contact(ctx: seahaven.Ctx, email: str, notes: str = "") -> dict[str, str]:
    """Add a contact to the pipeline as a lead."""
    contact = {
        "id": ctx.ids.uuid(),
        "email": email,
        "notes": notes,
        "stage": "lead",
        "updated_at": ctx.clock.iso(),
    }
    row = ctx.db.execute("INSERT INTO contacts VALUES (?, ?, ?, ?, ?)", *contact.values())
    ctx.db.execute("INSERT INTO contacts_fts (rowid, notes) VALUES (?, ?)", row.last_rowid, notes)
    return contact


@world.tool
def search_stale_leads(ctx: seahaven.Ctx, query: str) -> list[dict[str, str]]:
    """Full-text search over leads nobody has touched in 30 days."""
    return ctx.db.rows(
        "SELECT contacts.* FROM contacts_fts JOIN contacts ON contacts.rowid = contacts_fts.rowid "
        "WHERE contacts_fts MATCH ? AND stage = 'lead' AND updated_at < datetime('now', '-30 days')",
        query,
    )
```

Each tool's signature is the JSON schema an agent sees, and its docstring is the description.

**Run your agent against it:** Every rollout gets it's own database seeded with a copy of a fixture, the same seed replays the
same run, and what the agent changed is a document you grade:

```py
for rollout in range(100):
    with world.instance(
        "big_co", seed=rollout
    ) as world_instance:  # a private copy of the fixture, in ms
        run_agent(world_instance)  # your agent, your harness
        reward = grade(world_instance.state())  # what the agent left behind, as a document
```

**Serve it:** Host an OpenEnv endpoint. Every connection gets its own instance. Any OpenEnv client can connect.

```sh
uv run seahaven serve
```

```py
from seahaven.openenv import SeahavenClient

with SeahavenClient(base_url="http://127.0.0.1:8000") as env:
    env.reset(fixture="big_co", seed=42)
    env.call("create_contact", email="ada@example.com", notes="asked about pricing for 50 seats")
    stale = env.call("search_stale_leads", query="pricing").result
    final_state = env.state()  # the document the eval grades
```

**Example World:** see [ProjectTracker](worlds/projecttracker/), the reference world: a
fictional issue tracker with nine tables, 25 tools, search and three fixtures.

## Composing worlds

A world can **add other worlds**. Build a Stripe world once, a Slack world once, and a company world
that adds both plus its own tables and tools. The agent sees one flat tool list, the company world's
own tools call the added worlds' tools in process, and an eval inspects every store through one SQL
connection. See [docs](src/seahaven/docs/composition.md).

```py
company.add_world(stripe_world.world, name="stripe", tool_prefix="stripe_")
company.add_world(slack_world.world, name="slack", tool_prefix="slack_")


@company.tool
def refund_order(ctx: seahaven.Ctx, charge_id: str, channel: str) -> dict[str, object]:
    """Refund a charge and tell the support channel it is done."""
    refund = ctx.worlds.stripe.call("create_refund", charge_id=charge_id)
    ctx.worlds.slack.call("post_message", channel=channel, text=f"refunded {refund['amount']}")
    return refund
```

## Serving (OpenEnv)

Seahaven's remote lifecycle and transport are [OpenEnv](https://github.com/huggingface/OpenEnv), an open standard for connecting to RL
environments. `seahaven serve` runs one world, creating a unique instance and episode for each
connection. Serve over 100 instances per process. Connect with any OpenEnv client or tool, in any
language, like [Kiln](https://kiln.tech).

See [serving and openenv docs](src/seahaven/docs/serving_and_openenv.md) for more details: the client, the
wire protocol, what a session is, and why a Seahaven observation carries no reward.

## Agentic World Creation

Building a world with an agent? Point it at `uv run seahaven docs` which returns a path to docs it needs. The docs ship inside the package and always match the installed version.

## License

Licensed under [MIT](LICENSE).

## Created by Kiln AI

Seahaven was created by [Kiln AI](https://github.com/Kiln-AI/Kiln). Kiln is an open-source
platform for building, evaluating and optimizing AI systems, and supports Seahaven worlds as the
environments its [evals](https://kiln.tech/features/evals) and
[optimizers](https://kiln.tech/features/auto-optimize) run against.
