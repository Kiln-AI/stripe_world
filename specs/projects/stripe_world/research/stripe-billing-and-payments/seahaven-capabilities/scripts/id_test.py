import seahaven

SCHEMA = "CREATE TABLE t (id TEXT PRIMARY KEY) STRICT;"
world = seahaven.World(name="idtest", version="1.0.0", schema=SCHEMA, state_format="seahaven.state/1")


def stripe_id(ctx: seahaven.Ctx, prefix: str) -> str:
    """Stripe-shaped id built on the seeded stream: prefix + 24 base58-ish chars."""
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    return prefix + "".join(ctx.ids.random.choice(alphabet) for _ in range(24))


@world.tool
def make_customer(ctx: seahaven.Ctx) -> dict[str, str]:
    return {"id": stripe_id(ctx, "cus_")}


def run():
    with world.instance(seed=7) as inst:
        a = inst.call("make_customer")["id"]
    with world.instance(seed=7) as inst:
        b = inst.call("make_customer")["id"]
    print("a:", a)
    print("b:", b)
    assert a == b, "not deterministic!"
    print("OK: deterministic, Stripe-shaped id achievable on top of ctx.ids.random")


run()
