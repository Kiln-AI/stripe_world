"""`_ids`: the only place a Stripe id is minted.

Determinism is the load-bearing property: the same fixture and seed must mint
the same ids, because a fixture's bytes and an eval's assertions both depend on
it. These tests drive `stripe_id` through a real call on a probe world — the
context is the framework's, so the stream is the seeded one an instance uses.
"""

import string
from collections.abc import Callable

import pytest
import seahaven

from conftest import BLANK_NOW, a_tool
from stripeapi import _ids
from stripeapi._ids import REQUEST_ID_PREFIX, coupon_id, stripe_id

type Probe = Callable[..., seahaven.World]


@pytest.fixture
def minting(probe: Probe) -> seahaven.World:
    """A probe world whose one tool mints ids, as real handlers will."""

    def mint(ctx: seahaven.Ctx, prefix: str = "cus_") -> str:
        return stripe_id(ctx, prefix)

    return probe(a_tool(mint, "mint"))


def test_an_id_is_the_prefix_plus_24_alphanumerics(minting: seahaven.World) -> None:
    with minting.instance(None, seed=7, now=BLANK_NOW) as instance:
        minted = instance.call("mint")
    assert minted.startswith("cus_")
    suffix = minted[len("cus_") :]
    assert len(suffix) == 24
    assert all(c in _ids.ID_ALPHABET for c in suffix)


def test_the_same_seed_mints_the_same_ids(minting: seahaven.World) -> None:
    def first_id() -> str:
        with minting.instance(None, seed=7, now=BLANK_NOW) as instance:
            return str(instance.call("mint", prefix="pi_"))

    assert first_id() == first_id()


def test_different_seeds_mint_different_ids(minting: seahaven.World) -> None:
    with (
        minting.instance(None, seed=1, now=BLANK_NOW) as one,
        minting.instance(None, seed=2, now=BLANK_NOW) as two,
    ):
        assert one.call("mint", prefix="ch_") != two.call("mint", prefix="ch_")


def test_two_prefixes_from_one_stream_differ(minting: seahaven.World) -> None:
    with minting.instance(None, seed=1, now=BLANK_NOW) as instance:
        first = instance.call("mint", prefix="cus_")
        second = instance.call("mint", prefix="cus_")
    assert first != second


def test_an_unknown_prefix_is_a_world_bug(minting: seahaven.World) -> None:
    """A typo (`cu_` for `cus_`) is an authoring mistake; it must surface as one
    rather than as a malformed id an agent could be blamed for. The empty
    prefix is the same mistake from the other side: coupons are minted by
    `coupon_id`, never as a bare 24-character token."""
    with pytest.raises(seahaven.WorldBug), minting.instance(None, now=BLANK_NOW) as instance:
        instance.call("mint", prefix="cu_")
    with pytest.raises(seahaven.WorldBug), minting.instance(None, now=BLANK_NOW) as instance:
        instance.call("mint", prefix="")


def test_the_one_empty_prefix_is_coupons_only() -> None:
    """`coupon` is the one unprefixed id; every other object carries one, and
    no two objects share a prefix."""
    values = [v for v in _ids.STRIPE_ID_PREFIXES.values() if v]
    assert len(values) == len(set(values))
    assert [name for name, prefix in _ids.STRIPE_ID_PREFIXES.items() if prefix == ""] == ["coupon"]
    # The prefixes the data model fixes, spot-checked so a silent rename fails.
    assert _ids.STRIPE_ID_PREFIXES["customer_balance_transaction"] == "cbtxn_"
    assert _ids.STRIPE_ID_PREFIXES["subscription_schedule"] == "sub_sched_"
    assert _ids.STRIPE_ID_PREFIXES["event"] == "evt_"


def test_the_stub_and_request_prefixes_mint_too(minting: seahaven.World) -> None:
    """`ba_`, `card_`, `mandate_`, `setatt_` and `req_` are ids this world
    synthesises; they go through the same function and the same stream."""
    with minting.instance(None, seed=1, now=BLANK_NOW) as instance:
        for prefix in (*sorted(_ids.STUB_ID_PREFIXES), REQUEST_ID_PREFIX):
            minted = instance.call("mint", prefix=prefix)
            assert minted.startswith(prefix)
            assert len(minted) == len(prefix) + 24


def test_coupon_ids_are_caller_suppliable_or_eight_alphanumeric(probe: Probe) -> None:
    def mint_coupon(ctx: seahaven.Ctx, supplied: str | None) -> str:
        return coupon_id(ctx, supplied)

    world = probe(a_tool(mint_coupon, "mint_coupon"))
    with world.instance(None, seed=1, now=BLANK_NOW) as instance:
        assert instance.call("mint_coupon", supplied="TEN_OFF") == "TEN_OFF"
        generated = instance.call("mint_coupon", supplied=None)
        assert len(generated) == 8
        # Mixed case, matching the recorded minted ids (`hbzb1NEf`) — the
        # uppercase-only shape the data-model design guessed was corrected by
        # the Phase 7 recording.
        assert all(c in string.ascii_letters + string.digits for c in generated)
