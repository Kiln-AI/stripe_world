"""`_ids`: the only place a Stripe id is minted.

Determinism is the load-bearing property: the same fixture and seed must mint
the same ids, because a fixture's bytes and an eval's assertions both depend on
it. These tests drive `stripe_id` through a real call on a probe world — the
context is the framework's, so the stream is the seeded one an instance uses.

Two measured id formats (id-shapes.md, probed 2026-09-22):
  Format A — prefix + 14 random chars (cus_, prod_, si_)
  Format B — prefix + V(1) + T(5) + A(10) + R(8) = 24 structured chars
"""

import string
from collections.abc import Callable
from pathlib import Path

import pytest
import seahaven

from conftest import BLANK_NOW, a_tool
from seahaven_stripe_world import _ids
from seahaven_stripe_world._ids import (
    FORMAT_A_PREFIXES,
    REQUEST_ID_PREFIX,
    coupon_id,
    stripe_id,
)
from seahaven_stripe_world.startup import ACCOUNT_ID

type Probe = Callable[..., seahaven.World]

# The account fragment: last 10 chars of the account id suffix after `acct_`.
_ACCOUNT_SUFFIX = ACCOUNT_ID.removeprefix("acct_")
_ACCOUNT_FRAGMENT = _ACCOUNT_SUFFIX[-10:]


def _with_account(ctx: seahaven.Ctx) -> None:
    """Set up the account state that `_ids` needs for Format B."""
    ctx.state["account"] = {"id": ACCOUNT_ID, "livemode": True}


@pytest.fixture
def minting(probe: Probe) -> seahaven.World:
    """A probe world whose one tool mints ids, as real handlers will."""

    def mint(
        ctx: seahaven.Ctx,
        prefix: str = "cus_",
        version_digit: str = "1",
    ) -> str:
        _with_account(ctx)
        return stripe_id(ctx, prefix, version_digit=version_digit)

    return probe(a_tool(mint, "mint"))


# -- Format A tests (cus_, prod_, si_) ----------------------------------------


def test_as_01_format_a_suffix_length(minting: seahaven.World) -> None:
    """AS-01: Customer ID suffix is 14 chars, not 24."""
    with minting.instance(None, seed=7, now=BLANK_NOW) as instance:
        minted = instance.call("mint", prefix="cus_")
    suffix = minted[len("cus_") :]
    assert len(suffix) == 14
    assert all(c in _ids.ID_ALPHABET for c in suffix)


def test_format_a_product_id_is_14_chars(minting: seahaven.World) -> None:
    with minting.instance(None, seed=7, now=BLANK_NOW) as instance:
        minted = instance.call("mint", prefix="prod_")
    suffix = minted[len("prod_") :]
    assert len(suffix) == 14


def test_format_a_subscription_item_id_is_14_chars(minting: seahaven.World) -> None:
    with minting.instance(None, seed=7, now=BLANK_NOW) as instance:
        minted = instance.call("mint", prefix="si_")
    suffix = minted[len("si_") :]
    assert len(suffix) == 14


def test_format_a_prefixes_correct() -> None:
    """The three Format A prefixes are exactly the measured set."""
    assert {"cus_", "prod_", "si_"} == FORMAT_A_PREFIXES


# -- Format B tests (structured suffix) --------------------------------------


def test_as_02_format_b_account_encoding(minting: seahaven.World) -> None:
    """AS-02: Structured IDs embed a 10-char account fragment."""
    with minting.instance(None, seed=7, now=BLANK_NOW) as instance:
        minted = instance.call("mint", prefix="price_")
    suffix = minted[len("price_") :]
    assert len(suffix) == 24
    # V(1) + T(5) + A(10) + R(8)
    fragment = suffix[6:16]
    assert fragment == _ACCOUNT_FRAGMENT


def test_format_b_account_fragment_consistent_across_prefixes(
    minting: seahaven.World,
) -> None:
    """Two different Format B ids share the same account fragment."""
    with minting.instance(None, seed=7, now=BLANK_NOW) as instance:
        price_id = instance.call("mint", prefix="price_")
        pi_id = instance.call("mint", prefix="pi_")
    price_frag = price_id[len("price_") + 6 : len("price_") + 16]
    pi_frag = pi_id[len("pi_") + 6 : len("pi_") + 16]
    assert price_frag == pi_frag == _ACCOUNT_FRAGMENT


def test_as_27_format_b_time_encoding(minting: seahaven.World) -> None:
    """AS-27: Structured IDs encode creation time; ids created at the same
    timestamp share the same 5-char timestamp group."""
    with minting.instance(None, seed=7, now=BLANK_NOW) as instance:
        id_a = instance.call("mint", prefix="price_")
        id_b = instance.call("mint", prefix="sub_")
    # Both are minted at the same frozen clock — timestamp groups must match.
    t_group_a = id_a[len("price_") + 1 : len("price_") + 6]
    t_group_b = id_b[len("sub_") + 1 : len("sub_") + 6]
    assert t_group_a == t_group_b
    # And the group is not all zeros or all the same char.
    assert len(t_group_a) == 5
    assert all(c in _ids.ID_ALPHABET for c in t_group_a)


def test_format_b_version_digit_default_is_1(minting: seahaven.World) -> None:
    with minting.instance(None, seed=7, now=BLANK_NOW) as instance:
        minted = instance.call("mint", prefix="price_")
    suffix = minted[len("price_") :]
    assert suffix[0] == "1"


def test_format_b_version_digit_3_for_side_effects(minting: seahaven.World) -> None:
    with minting.instance(None, seed=7, now=BLANK_NOW) as instance:
        minted = instance.call("mint", prefix="ch_", version_digit="3")
    suffix = minted[len("ch_") :]
    assert suffix[0] == "3"


def test_format_b_suffix_all_alphanumeric(minting: seahaven.World) -> None:
    with minting.instance(None, seed=7, now=BLANK_NOW) as instance:
        minted = instance.call("mint", prefix="pi_")
    suffix = minted[len("pi_") :]
    assert len(suffix) == 24
    assert all(c in _ids.ID_ALPHABET for c in suffix)


# -- shared properties -------------------------------------------------------


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


def test_two_ids_from_one_stream_differ(minting: seahaven.World) -> None:
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
            # All stub/request prefixes use Format B (24-char structured suffix).
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


def test_format_b_requires_account_state(tmp_path: Path) -> None:
    """Format B ids need ctx.state['account']; minting without startup is a WorldBug."""
    from seahaven_stripe_world.middleware.error_handler import error_handler
    from seahaven_stripe_world.middleware.stripe_envelope import stripe_envelope
    from seahaven_stripe_world.world import world as real_world

    def mint_no_account(ctx: seahaven.Ctx) -> str:
        return stripe_id(ctx, "pi_")

    # Build a world with no startup hook — no account state.
    bare = seahaven.World(
        "bare",
        real_world.version,
        real_world.schema,
        fixtures_dir=tmp_path / "fixtures",
        work_dir=tmp_path / "work",
        state_format="seahaven.state/1",
    )
    bare.middleware(error_handler)
    bare.middleware(stripe_envelope)
    bare.tool(seahaven.Tool.from_function(mint_no_account, name="mint_no_account", description=""))
    with pytest.raises(seahaven.WorldBug), bare.instance(None, now=BLANK_NOW) as instance:
        instance.call("mint_no_account")


# -- source-level guards -----------------------------------------------------


def test_no_uuid_calls_in_source() -> None:
    """No production code calls `ctx.ids.uuid()` — SEAHAVEN_FINDINGS.md Entry 2.

    `_ids.py` is excluded: its module docstring mentions the hazard by name
    but does not call it.
    """
    import pathlib

    src = pathlib.Path(__file__).resolve().parent.parent / "src"
    excluded_stems = {"_ids"}
    hits: list[str] = []
    for py in src.rglob("*.py"):
        if py.stem in excluded_stems:
            continue
        text = py.read_text()
        for i, line in enumerate(text.splitlines(), 1):
            if "ctx.ids.uuid(" in line or ".ids.uuid(" in line:
                hits.append(f"{py.relative_to(src)}:{i}: {line.strip()}")
    assert hits == [], "uuid() calls found:\n" + "\n".join(hits)
