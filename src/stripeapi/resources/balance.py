"""`GET /v1/balance`: the computed aggregate over the ledger.

No table, no id, not listable (functional spec §3.4: `balance` is one of the
four objects deliberately not given a table) — `billing/ledger.py::
read_balance` sums the `balance_transactions` rows per currency, split
available/pending on `available_on` against the instance clock, so the
balance cannot drift from the ledger by construction: the "ledger sums
correctly" invariant is a tautology rather than a check of two copies
(architecture §6.4).

Shape probed live (Phase 11): entries carry `{amount, currency,
source_types: {card: amount}}`; the optional buckets
(`instant_available`, `connect_reserved`, `issuing`) and the
`refund_and_dispute_prefunding` block are absent while empty here, where the
recorded account carries zeros in its settlement currency — a declared,
allow-listed difference (this world has no prefunding ledger rows to report).
"""

from typing import TYPE_CHECKING, Any

import seahaven

from stripeapi.billing import ledger
from stripeapi.dispatch.params import ParamSpec

if TYPE_CHECKING:
    from stripeapi.dispatch.response import Request

__all__ = ["BALANCE_READ", "read"]

BALANCE_READ = ParamSpec(
    op_id="GetBalance",
)


def read(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """The one route this resource owns: everything computed, nothing read
    from a table of its own."""
    return ledger.read_balance(ctx)
