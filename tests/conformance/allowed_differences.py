"""The allow-list: every difference between a recorded real-API response and
this world's replay that is *permitted*, each with its reason.

This file is the precise statement of how faithful this world is, and it is
reviewed as a design document (architecture.md §10). Anything undeclared
fails a replay: the fix is either the handler or a reviewed entry here —
never a silent pass.

Two sections, deliberately different shapes:

- `ALLOWED_DIFFERENCES` — per-path entries the tree diff consults. A path
  may differ arbitrarily (`predicate is None`) or only when the predicate
  holds. An entry also covers the path's presence on one side only: a key
  this world omits where real Stripe emits it is a difference *at* that path,
  not an untouchable structural fact.
- `STRUCTURAL_DIFFERENCES` — differences that never produce an observable
  field diff within one replay and so have no path to hang an entry on.
  Prose, reviewed the same way.

Path grammar (`components/conformance.md` "The allow-list"): patterns split
on `.`, list indices are ordinary segments (`body.data[3].id`), and in each
pattern segment `*` is the only wildcard — `*` alone is one segment, `**`
alone is any depth, a segment like `*_at` matches by suffix, and a segment
like `data[3]` matches only itself (brackets are literal, not a character
class).
"""

import re
from collections.abc import Callable
from typing import Any

__all__ = [
    "ALLOWED_DIFFERENCES",
    "STRUCTURAL_DIFFERENCES",
    "AllowedDifference",
    "allowed",
    "match_path",
]

# --- Per-path entries ------------------------------------------------------------


class AllowedDifference:
    """One permitted difference. `path` is a pattern (see the module
    docstring); `reason` is the reviewable content; `scenario` scopes the
    entry to one scenario when the difference is not global; `predicate`
    narrows an arbitrary difference to a checked one."""

    __slots__ = ("path", "predicate", "reason", "scenario")

    def __init__(
        self,
        path: str,
        reason: str,
        scenario: str | None = None,
        predicate: Callable[[Any, Any], bool] | None = None,
    ) -> None:
        self.path = path
        self.reason = reason
        self.scenario = scenario
        self.predicate = predicate

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        scope = "every scenario" if self.scenario is None else self.scenario
        return f"AllowedDifference({self.path!r}, {scope})"


def _both_false(recorded: Any, replayed: Any) -> bool:
    return recorded is False and replayed is False


def _both_cus_ids(recorded: Any, replayed: Any) -> bool:
    """A `customer`-shaped reference field: both sides are freshly minted ids
    of the same shape, never equal — the `**.id` rule on the field it lands
    on."""
    return (
        isinstance(recorded, str)
        and isinstance(replayed, str)
        and recorded.startswith("cus_")
        and replayed.startswith("cus_")
    )


_ID_IN_URL = re.compile(
    r"\b(?:cus|pm|ch|pi|in|sub|si|ii|cn|re|dp|seti|txn|po|cbtxn|evt|price|prod|promo|txr|sub_sched)_[A-Za-z0-9]+\b"
)


def _same_url_modulo_ids(recorded: Any, replayed: Any) -> bool:
    """A list envelope's `url` embeds the caller's path ids; strip every
    id-shaped token and what remains must be identical."""
    if not isinstance(recorded, str) or not isinstance(replayed, str):
        return False
    return _ID_IN_URL.sub("<id>", recorded) == _ID_IN_URL.sub("<id>", replayed)


#: The one message form whose entire variable content is the id it names.
#: Predicate-narrowed so every other error message stays byte-exact: only
#: `No such customer: '<cus_id>'` on both sides passes. Real Stripe ids vary
#: in length (14 here against this world's minted 24), so the form — not the
#: length — is the test.
_NO_SUCH_CUSTOMER = re.compile(r"^No such customer: 'cus_[A-Za-z0-9]+'$")


def _no_such_customer_modulo_id(recorded: Any, replayed: Any) -> bool:
    if not isinstance(recorded, str) or not isinstance(replayed, str):
        return False
    return bool(_NO_SUCH_CUSTOMER.fullmatch(recorded) and _NO_SUCH_CUSTOMER.fullmatch(replayed))


ALLOWED_DIFFERENCES: list[AllowedDifference] = [
    AllowedDifference(
        "**.id",
        "Ids are freshly minted per world instance from the seeded stream "
        "(architecture.md §4.4); they never equal the recorded real-Stripe id "
        "by construction, so comparing them tests nothing.",
    ),
    AllowedDifference(
        "**.created",
        "The instance clock and the record-time wall clock are different "
        "instants; equality was never meaningful.",
    ),
    AllowedDifference(
        "**.*_at",
        "Every `*_at` timestamp field (`canceled_at`, `available_on`-shaped "
        "windows, …) differs for the same clock reason as `created`. Suffix-"
        "matched per field so a blanket `**` cannot hide a non-timestamp.",
    ),
    AllowedDifference(
        "**.invoice_prefix",
        "Stripe mints a random 8-character prefix per customer and so does "
        "this world (`components/data_model.md` §3.13): same shape, different "
        "random draws — the id rule in miniature.",
    ),
    AllowedDifference(
        "**.livemode",
        "Both sides are always false — recorded from test mode, emitted as a "
        "constant here. Predicate mode: if either side is ever anything but "
        "false, that is a real divergence, not a permitted difference.",
        predicate=_both_false,
    ),
    AllowedDifference(
        "**.request_log_url",
        "A Stripe-dashboard URL tied to the recording account; this world "
        "does not model a dashboard. Recorded values are already normalized "
        "to a placeholder by the redaction pass, and the field is absent on "
        "this side.",
    ),
    AllowedDifference(
        "**.fingerprint",
        "Card fingerprints are derived per recording account on real Stripe "
        "and per content digest here (billing/magic_cards.py): same number -> "
        "same fingerprint within each world, never equal across the divide — "
        "the id rule in miniature, with the same stability property.",
    ),
    AllowedDifference(
        "**.shared_payment_granted_token",
        "The live API at the pinned version emits fields its own spec does "
        "not declare (recorded on payment_method, Phase 6); this world emits "
        "the spec's property set, which is the authority for object shape.",
    ),
    AllowedDifference(
        "**.customer",
        "Reference-valued fields carry ids minted per instance "
        "(architecture.md §4.4): the `**.id` rule on the field name a "
        "reference lands on. Predicate mode so a non-id divergence on any "
        "`customer` field still fails.",
        predicate=_both_cus_ids,
    ),
    AllowedDifference(
        "**.url",
        "A list envelope's `url` echoes the caller's path, scoped paths "
        "included, so it embeds the same freshly minted ids. Predicate mode: "
        "with every id-shaped token masked, recorded and replayed must be "
        "identical.",
        predicate=_same_url_modulo_ids,
    ),
    AllowedDifference(
        "**.error.message",
        "A `resource_missing` message names the id it could not find, and "
        "the ids differ per instance — the `**.id` rule where the id is the "
        "message's whole variable content. Predicate mode admits exactly the "
        "`No such customer: '<cus_id>'` form on both sides; every other "
        "message stays byte-exact.",
        predicate=_no_such_customer_modulo_id,
    ),
    # Scenario 6's attachment refusal: the recorded decline envelope carries
    # network chatter and a form-boundary artifact this world deliberately
    # omits. Scoped here so the entries can never mask a regression on any
    # other decline.
    AllowedDifference(
        "body.error.param",
        "The recorded envelope answers param as an empty string (a form-"
        "encoding artifact); this world omits a param it has no value for.",
        scenario="06_customers_payment_methods",
    ),
    AllowedDifference(
        "body.error.advice_code",
        "Real Stripe decorates the decline with advice_code try_again_later; "
        "no advice model exists here to source one from.",
        scenario="06_customers_payment_methods",
    ),
    AllowedDifference(
        "body.error.network_decline_code",
        "Issuer network decline codes are network state this world does not model.",
        scenario="06_customers_payment_methods",
    ),
    # Scenario 3 exists to record what a malformed Stripe-Version answers.
    # The world deliberately serves one fixed version with no header channel
    # and no negotiation (functional spec §6.5), so the whole response —
    # status and body — differs, scoped to this scenario alone so the entry
    # can never mask a regression elsewhere.
    AllowedDifference(
        "status",
        "Real Stripe answers a malformed Stripe-Version with 400; this world "
        "serves the one pinned version and cannot see the header "
        "(functional spec §6.5).",
        scenario="03_malformed_stripe_version",
    ),
    AllowedDifference(
        "body",
        "The recorded body is the malformed-version error envelope; the "
        "replayed body is the normal pinned-version response for the same "
        "request.",
        scenario="03_malformed_stripe_version",
    ),
]

# --- Structural differences ------------------------------------------------------


STRUCTURAL_DIFFERENCES: list[str] = [
    "Idempotency-key retention: real Stripe evicts a key once it is at least "
    "24 hours old — a floor, not a fixed TTL; this world's clock never "
    "advances, so a key never expires within a rollout (functional spec "
    "§6.1). No single replay can observe the difference.",
    "Search result freshness: real Stripe search lags writes; this world's "
    "search is exact (functional spec §3.3). Not exercised before the search "
    "phase; declared here so the difference is on record before it exists.",
    "The stripe-version response header: the real API echoes it and the tool "
    "return here is {status, body} with no header channel — an agent using "
    "Stripe's own MCP tools sees JSON, not HTTP, on both sides (functional "
    "spec §6.5).",
    "A malformed Stripe-Version is a request-header fault this world cannot "
    "express: one version is served, the version is not an agent-facing "
    "parameter, and the recorded behavior lives in cassette 03 rather than "
    "in an implementation (functional spec §6.5; scenario-scoped allow-list "
    "entries carry the replay difference).",
    "Scalar stringification at the form boundary: real Stripe form-encodes "
    "parameters, so a JSON number or boolean arrives as a string and fails "
    "validation as that string (email=5 -> 'Invalid email address: 5'); this "
    "surface takes JSON and type-checks it instead (functional spec §2.2 — "
    "no form encoding exists in this project). Only observable on type-fault "
    "requests, which the scenarios deliberately do not record.",
    "Stripe's nullable annotations are not authoritative where a recording "
    "contradicts them: at the pinned version a fresh customer carries "
    "default_source: null and invoice_settings.default_payment_method: null "
    "while the spec types both non-nullable (probed on the sandbox account, "
    "Phase 4, 2026-09-19). Schema conformance carries exactly two "
    "NULLABLE_DESPITE_SPEC entries on that evidence "
    "(tests/schema_conformance/validate.py); this is the general declaration.",
    "setup_intent.usage is read as a closed set (on_session / off_session) "
    "by this project's ruling — the spec types it a bare string whose "
    "description names two values without calling them exhaustive. The "
    "validator enforces the closed set and marks it DECLARED_OVERRIDES so "
    "the reading is never presented as documented fact (functional spec §4; "
    "spec/enums.py).",
    "Dunning's unpaid outcome leaves invoices in draft: two Stripe "
    "documentation pages say draft, while spec3.json's own prose says "
    "'closed', which was never a real invoice.status value. The docs win "
    "(functional spec §7); observable when the invoices phase's cassettes "
    "land, declared here now.",
    "Raw card numbers are accepted here and refused on the recording "
    "account: newer Stripe accounts block raw PANs by default ('Sending "
    "credit card numbers directly to the Stripe API is generally unsafe', "
    "recorded Phase 6), while the magic-card table is this world's spec'd "
    "failure-injection mechanism and needs the raw path. Cassettes create "
    "cards through tok_* tokens; unit tests exercise the numbers.",
    "The 54 payment rails the pruner drops are creatable and answered as "
    "`{}` under their own type key (recorded on klarna), exactly as "
    "components/data_model.md §3.9 stubs them; the pruned spec cannot "
    "declare those properties, so the schema-conformance validator carries "
    "the one declared stub exception (tests/schema_conformance/validate.py).",
    "A customer created or updated with a nonzero balance is stamped with "
    "the account default currency — the recording account's is 'cad', this "
    "world's single static account defaults to 'usd' until an account "
    "object exists (Phase 21). Probed both ways: balance=0 leaves currency "
    "null, and `currency` is not itself a settable parameter.",
    "Token card expiries are frozen at the recorded (9, 2027): real Stripe "
    "rolls a token's default expiry forward with wall time, and a frozen "
    "clock cannot follow (billing/magic_cards.py::TOKEN_EXPIRY).",
]

# --- Matching --------------------------------------------------------------------

_SEGMENT_CACHE: dict[str, re.Pattern[str]] = {}


def _segment(pattern_segment: str) -> re.Pattern[str]:
    """One pattern segment as a regex: only `*` is a wildcard (within the
    segment); everything else — including a literal `data[3]` — is escaped,
    because fnmatch would read the brackets as a character class and a
    bracketed pattern segment would then match nothing at all."""
    compiled = _SEGMENT_CACHE.get(pattern_segment)
    if compiled is None:
        compiled = re.compile(re.escape(pattern_segment).replace(re.escape("*"), "[^.]*"))
        _SEGMENT_CACHE[pattern_segment] = compiled
    return compiled


def match_path(pattern: str, path: str) -> bool:
    """True when `path` (the diff walker's dotted/bracketed path) matches
    `pattern`. `**` consumes any number of segments; every other segment is
    matched with `*` as its only wildcard (`*` alone matches exactly one
    segment, `*_at` a suffix, `data[3]` only itself)."""
    pat = pattern.split(".")
    seg = path.split(".")

    def walk(index_pat: int, index_seg: int) -> bool:
        if index_pat == len(pat):
            return index_seg == len(seg)
        if pat[index_pat] == "**":
            # `**` at the end matches everything remaining; otherwise try
            # consuming 0..n segments.
            return any(walk(index_pat + 1, i) for i in range(index_seg, len(seg) + 1))
        if index_seg == len(seg):
            return False
        return bool(_segment(pat[index_pat]).fullmatch(seg[index_seg])) and walk(
            index_pat + 1, index_seg + 1
        )

    return walk(0, 0)


def allowed(path: str, recorded: Any, replayed: Any, scenario: str) -> AllowedDifference | None:
    """The entry that permits this difference, if one does.

    Global entries apply everywhere; a scenario-scoped entry applies only
    when its scenario name matches. A predicate entry permits the difference
    only while the predicate holds — a path being listed is not enough."""
    for entry in ALLOWED_DIFFERENCES:
        if entry.scenario is not None and entry.scenario != scenario:
            continue
        if not match_path(entry.path, path):
            continue
        if entry.predicate is not None and not entry.predicate(recorded, replayed):
            continue
        return entry
    return None
