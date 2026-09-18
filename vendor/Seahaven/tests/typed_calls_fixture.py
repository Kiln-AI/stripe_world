"""Calls by function reference: a module `ty` checks and nothing runs.

Architecture section 8.1 is a promise about a *type checker*, and the only way to
hold a type checker to a promise is to check something with it. This module calls
the committed composite world's tools by reference, and every line of it is an
assertion:

- `assert_type` pins the result type each call resolves to. If the `Concatenate` +
  `ParamSpec` overload stops resolving `R`, these fail.
- The calls that must *not* type-check carry a `# ty: ignore[...]`, which ty
  reports as unused if the day comes that it stops flagging them. So the file
  fails whichever way the overloads break: too loose, or too tight.

It is inside the repository's own `ty check`, which is the CI gate section 16
asks for, and `test_typed_call.py` runs ty over it as well so that the gate is
also a test. Nothing imports it and nothing calls these functions.
"""

from typing import Annotated, Any, assert_type

from emporium.tools.billing import record_charge_owner, settle_order
from payments.tools.charges import create_charge, list_charges
from pydantic import Field
from shop.tools.checkout import place_order

import seahaven
from seahaven.call import Handler

# A world of its own, for the one shape the committed tree has not got: an
# argument whose wire name is not its parameter's. `ParamSpec` can only spell the
# parameter, so this is the call `Tool.arguments` has to translate.
aliases = seahaven.World(
    name="aliases",
    version="1.0.0",
    schema="CREATE TABLE moves (id TEXT PRIMARY KEY) STRICT;",
    state_format="seahaven.state/1",
)


@aliases.tool
def transfer(
    ctx: seahaven.Ctx, from_: Annotated[str, Field(alias="from")], to: str
) -> dict[str, str]:
    """Move money: the agent sends `from`, the function receives `from_`."""
    return {"from": from_, "to": to}


class EmporiumWorlds(seahaven.Worlds):
    """`emporium`'s children, declared for a type checker (architecture section 8.3)."""

    payments: seahaven.WorldHandle
    payments_eu: seahaven.WorldHandle
    shop: seahaven.WorldHandle


def at_the_instance(inst: seahaven.Instance) -> None:
    """The agent's flat surface, reached by the functions behind it."""
    assert_type(inst.call(settle_order, total=10), dict[str, Any])
    assert_type(inst.call(record_charge_owner, charge_id="c1", owner_id="o1"), dict[str, Any])
    # A name is still a name, and answers `Any`: nothing about it says what it returns.
    assert_type(inst.call("shop_place_order", total=10), Any)

    # The parameter's name, which is the only one a `ParamSpec` can spell, and
    # not the `from` the tool list publishes.
    assert_type(inst.call(transfer, from_="a", to="b"), dict[str, str])
    assert_type(inst.call(transfer, "a", "b"), dict[str, str])

    inst.call(settle_order, total="ten")  # ty: ignore[no-matching-overload]
    inst.call(settle_order, totl=10)  # ty: ignore[no-matching-overload]
    inst.call(settle_order)  # ty: ignore[no-matching-overload]


def in_its_own_middleware(
    ctx: seahaven.Ctx[EmporiumWorlds], call: seahaven.Call, next_: Handler
) -> Any:
    """Binding a call keeps the parameter a world declared, which is what declaring it is for."""
    assert_type(ctx.with_call(call), seahaven.Ctx[EmporiumWorlds])
    return next_(ctx.with_call(call), call)


def through_a_handle(ctx: seahaven.Ctx[EmporiumWorlds]) -> None:
    """An added world's own tools, typed the same way, through the node that owns them."""
    assert_type(ctx.worlds.payments.call(create_charge, amount=100), dict[str, Any])
    assert_type(ctx.worlds.payments_eu.call(list_charges), list[dict[str, Any]])
    # Positionally, which is what `*args: P.args` makes legal and `Tool.arguments` binds.
    assert_type(ctx.worlds.shop.call(place_order, 100), dict[str, Any])

    ctx.worlds.payments.call(create_charge, amount="lots")  # ty: ignore[no-matching-overload]
