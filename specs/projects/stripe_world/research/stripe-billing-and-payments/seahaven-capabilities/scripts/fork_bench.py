"""Ad-hoc probe: build a fixture with N customer-shaped rows, freeze it, and
measure how long it takes to fork (create a fresh instance from) that fixture,
repeated many times. Answers project_overview.md's "large fixture must still
fork in milliseconds" constraint for a Stripe-shaped customers table.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/user/stripe_world/vendor/Seahaven/src")
import seahaven

N_CUSTOMERS = 20000
N_CHARGES_PER_CUSTOMER = 5  # ~15,000 charges too

SCHEMA = """
CREATE TABLE customers (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    name TEXT NOT NULL,
    balance INTEGER NOT NULL DEFAULT 0,
    metadata TEXT NOT NULL CHECK (json_valid(metadata)),
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE charges (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers (id),
    amount INTEGER NOT NULL,
    currency TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;

CREATE INDEX charges_by_customer ON charges (customer_id);
"""

world = seahaven.World(
    name="forkbench",
    version="1.0.0",
    schema=SCHEMA,
    fixtures_dir=Path(__file__).parent / "fixtures",
    state_format="seahaven.state/1",
)


def build(fixture_id: str) -> None:
    t0 = time.perf_counter()
    with world.instance(now="2026-06-01T09:00:00.000Z") as inst:
        with inst.bulk() as ctx:
            customers = [
                (
                    f"cus_{i:08d}",
                    f"user{i}@example.com",
                    f"Customer {i}",
                    0,
                    '{"plan":"pro"}',
                    "2026-05-01T00:00:00.000Z",
                )
                for i in range(N_CUSTOMERS)
            ]
            ctx.db.executemany(
                "INSERT INTO customers (id, email, name, balance, metadata, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                customers,
            )
            charges = [
                (
                    f"ch_{i:08d}_{j}",
                    f"cus_{i:08d}",
                    1999,
                    "usd",
                    "succeeded",
                    "2026-05-02T00:00:00.000Z",
                )
                for i in range(N_CUSTOMERS)
                for j in range(N_CHARGES_PER_CUSTOMER)
            ]
            ctx.db.executemany(
                "INSERT INTO charges (id, customer_id, amount, currency, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                charges,
            )
        fixture = inst.freeze(
            fixture_id, f"{N_CUSTOMERS} customers, {N_CUSTOMERS * N_CHARGES_PER_CUSTOMER} charges."
        )
    build_time = time.perf_counter() - t0
    size_bytes = fixture.state_path.stat().st_size
    print(
        f"build+freeze: {build_time * 1000:.1f} ms, "
        f"fixture file size: {size_bytes / 1_048_576:.2f} MiB"
    )


def bench_fork(fixture_id: str, repeats: int) -> None:
    times = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        with world.instance(fixture_id) as inst:
            row = inst.inspect().one("SELECT count(*) AS n FROM customers")
            assert row is not None and row["n"] == N_CUSTOMERS
        times.append(time.perf_counter() - t0)
    times.sort()
    n = len(times)
    print(
        f"fork x{repeats}: min={times[0]*1000:.2f}ms "
        f"p50={times[n//2]*1000:.2f}ms "
        f"p90={times[int(n*0.9)]*1000:.2f}ms "
        f"max={times[-1]*1000:.2f}ms"
    )


if __name__ == "__main__":
    fixtures_dir = Path(__file__).parent / "fixtures" / "big2"
    if not fixtures_dir.exists():
        build("big2")
    else:
        print("fixture already built, skipping build")
    bench_fork("big2", repeats=50)
