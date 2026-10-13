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

## big_co
