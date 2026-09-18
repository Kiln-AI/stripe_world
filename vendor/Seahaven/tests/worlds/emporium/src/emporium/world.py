"""The host: its own table, two payments accounts, and a shop that shares one.

The nodes are `main`, `payments`, `payments_eu` and `shop`. `shop` adds
`payments` with the default store, so it reaches the company's own account: the
route `shop/payments` is an alias of the node `payments`, not a fifth store.
"""

import payments
import shop

import seahaven

world = seahaven.World(
    name="emporium",
    version="0.1.0",
    schema=seahaven.sql_files(__package__, "schema"),
    state_format="seahaven.state/1",
)
world.add_world(payments.world, name="payments", tool_prefix="pay_")
world.add_world(payments.world, name="payments_eu", tool_prefix="eu_", store="eu")
world.add_world(shop.world, name="shop", tool_prefix="shop_")
