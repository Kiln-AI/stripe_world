"""The allow-list matcher, its scoping and its predicate mode — unit tests
on the machinery the diff trusts."""

from conformance.allowed_differences import ALLOWED_DIFFERENCES, allowed, match_path


def test_exact_match() -> None:
    assert match_path("body.livemode", "body.livemode")
    assert not match_path("body.livemode", "body.livemode_extra")
    assert not match_path("body.livemode", "body.settings.livemode")


def test_single_segment_wildcard() -> None:
    assert match_path("body.*.id", "body.customer.id")
    assert match_path("body.*.id", "body.default_source.id")
    assert not match_path("body.*.id", "body.customer.address.id")  # two segments deep
    assert not match_path("body.*.id", "body.id")


def test_any_depth_wildcard_including_list_indices() -> None:
    assert match_path("**.id", "body.id")
    assert match_path("**.id", "body.customer.id")
    assert match_path("**.id", "body.data[3].id")
    assert match_path("**.id", "body.data[0].lines.data[2].id")
    assert not match_path("**.id", "body.customer")
    assert match_path("**.id", "id")  # `**` may consume zero segments too


def test_suffix_segments_match_timestamps() -> None:
    assert match_path("**.*_at", "body.canceled_at")
    assert match_path("**.*_at", "body.data[0].finalized_at")
    assert not match_path("**.*_at", "body.data[0].created")
    assert not match_path("**.*_at", "body.attack")


def test_bracketed_segments_are_literal_not_a_character_class() -> None:
    """A pattern segment pinning a list index matches that index and nothing
    else — `data[3]` is three literal characters in brackets, not fnmatch's
    character class, which would match no segment at all."""
    assert match_path("**.data[3].id", "body.data[3].id")
    assert not match_path("**.data[3].id", "body.data[4].id")
    assert not match_path("**.data[3].id", "body.data[33].id")
    assert not match_path("**.data[3].id", "body.data.id")
    assert match_path("body.data[3]", "body.data[3]")


def test_scenario_scoping_does_not_leak() -> None:
    """The entry that exists for scenario 03 is exact-named; assert both the
    leak and the non-leak with entries that really are in the table."""
    assert allowed("status", 400, 200, "03_malformed_stripe_version") is not None
    assert allowed("status", 400, 200, "09_error_envelope_400s") is None


def test_predicate_mode_rejects_where_the_predicate_fails() -> None:
    """`**.livemode` permits only both-false: a livemode that is ever true on
    either side is a real divergence, not a permitted difference."""
    assert allowed("body.livemode", False, False, "any_scenario") is not None
    assert allowed("body.livemode", False, True, "any_scenario") is None
    assert allowed("body.data[2].livemode", False, False, "any_scenario") is not None


def test_an_allow_listed_path_covers_key_presence() -> None:
    """Real Stripe emits `request_log_url`; this world omits it. A missing
    key is a difference *at* that path, so the entry covers it."""
    assert allowed("body.error.request_log_url", "https://…", None, "any") is not None


def test_every_entry_declares_a_reason_and_known_scenario() -> None:
    """The reasons are the reviewable content: an entry without one is a
    silent pass, and an entry naming a scenario that does not exist can
    never fire. And no entry is the bare any-depth blanket this file refuses
    to carry."""
    from tools_dev.scenarios import registry

    names = set(registry())
    for entry in ALLOWED_DIFFERENCES:
        assert entry.reason.strip(), entry.path
        assert len(entry.reason) > 20, f"{entry.path}: a reason, not a label"
        assert entry.path != "**", "a bare any-depth entry hides everything"
        if entry.scenario is not None:
            assert entry.scenario in names, f"{entry.path}: unknown scenario {entry.scenario}"
