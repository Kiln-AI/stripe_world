"""Per-operation parameter allowlists, coercion, and the five central parameters.

Stripe rejects unknown parameters (`Received unknown parameter: nope`, probed
verbatim this phase), which needs a closed set this world controls rather than
`spec3.json`'s far wider request bodies (`architecture.md` §3.3). `ParamSpec`
is that closed set, one per wired operation.

Strictness matches Seahaven's own argument model: `"5"` is not an integer,
`2.0` is not an integer, `1` is not a boolean. The surface is a JSON tool call
with no form encoding anywhere (functional spec §2.2), so there is no reason
to be lenient and every reason for a mistyped amount to be a clear 400 rather
than a silent cast. Bracket notation appears only in `param` on the way out:
`items[0][price]`, `invoice_settings[default_payment_method]`
(`gap-closure-2026-09-18.md` item 6, and this phase's probe:
`Received unknown parameter: invoice_settings[nope]`).

`expand`, `limit`, `starting_after`, `ending_before` and `metadata` are
declared once here and never redeclared in a `ParamSpec`; the router refuses a
body entry that names one of them. `bind` is pure — it reads no tables, so the
only work discarded when it raises is its own — and `expand[]` paths are
validated here against the route's declared response object, before any row is
written (cross_cutting.md §3.3.1).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

import seahaven

from seahaven_stripe_world import _time
from seahaven_stripe_world._ids import STRIPE_ID_PREFIXES
from seahaven_stripe_world.stripe_errors import (
    StripeApiError,
    invalid_request,
    missing_parameter,
    resource_missing,
    unknown_parameter,
)

if TYPE_CHECKING:
    from seahaven_stripe_world.dispatch.resource import ResourceSpec
    from seahaven_stripe_world.dispatch.response import Request
    from seahaven_stripe_world.dispatch.routes import Route

__all__ = [
    "CENTRAL_PARAMETERS",
    "DEFAULT_LIMIT",
    "ENDING_BEFORE",
    "EXPAND",
    "LIMIT",
    "STARTING_AFTER",
    "MetadataUpdate",
    "Param",
    "ParamSpec",
    "bind",
    "body_of",
]

Kind = Literal[
    "string",
    "integer",
    "boolean",
    "number",
    "id",
    "literal",
    "object",
    "map",
    "array",
    "range",
    "timestamp",
    "int_literal",
    "currency",
]

#: The five parameters lifted out of `Request.params` (§3.4 of the dispatcher
#: design). A handler that reaches for `req.params["expand"]` has a bug, and
#: the `KeyError` is the right kind of loud.
CENTRAL_PARAMETERS = frozenset(("expand", "limit", "starting_after", "ending_before", "metadata"))

#: The documented default and bounds of `limit`, identical on every list
#: endpoint (functional spec §6.2). The live API is laxer than its own spec
#: The documented default. `limit` itself clamps into [1, 100] rather than
#: rejecting: the live API answers `limit=0` (or negative) with one item and
#: `limit=101` with one hundred, both 200 — settled by this world's Phase 5
#: cassettes (scenario 08), which supersede the documented 1-100 contract
#: Phase 3 had implemented as a 400.
DEFAULT_LIMIT = 10
LIMIT_FLOOR = 1
LIMIT_CEILING = 100

METADATA_MAX_KEYS = 50
METADATA_MAX_KEY_LENGTH = 40
METADATA_MAX_VALUE_LENGTH = 500


@dataclass(frozen=True, slots=True)
class Param:
    """One parameter an operation accepts."""

    name: str
    kind: Kind
    required: bool = False
    choices: tuple[str, ...] = ()  # kind="literal"
    id_prefixes: tuple[str, ...] = ()  # kind="id"
    minimum: int | None = None
    maximum: int | None = None
    max_length: int | None = None
    shape: tuple[Param, ...] = ()  # kind="object"
    item: Param | None = None  # kind="array"; the per-currency value for "map"
    unset_with_empty_string: bool = False  # Stripe's "" means "clear this field"
    column: str | None = None  # None means the column shares the name

    @property
    def column_name(self) -> str:
        return self.name if self.column is None else self.column


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """One operation's allowlist: path placeholders plus body parameters."""

    op_id: str
    path: tuple[str, ...] = ()  # placeholder names, in pattern order
    body: tuple[Param, ...] = ()
    expand: bool = True  # 139 of the 148 accept expand[]; the nine stub DELETEs say so
    paginated: bool = False
    metadata: bool = False
    required_one_of: tuple[tuple[str, ...], ...] = ()
    mutually_exclusive: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class MetadataUpdate:
    """Stripe's metadata update semantics as one value object.

    An update is a merge, not a replace; a key sent with `""` (or JSON `null`)
    is deleted; the whole parameter sent as `""` — or `{}`, the natural JSON
    spelling of "no keys" — clears every key (`cross-cutting-semantics/metadata.md`).
    """

    clear: bool
    set: Mapping[str, str]
    unset: frozenset[str]

    def apply(self, current: Mapping[str, str]) -> dict[str, str]:
        if self.clear:
            return {}
        out = {key: value for key, value in current.items() if key not in self.unset}
        out.update(self.set)
        return out


# --- The five central parameters, declared once -------------------------------

EXPAND: Final = Param(
    name="expand", kind="array", item=Param(name="", kind="string", max_length=5000)
)
# Integer-typed only: the 1-100 range is a silent clamp (see DEFAULT_LIMIT),
# not a rejection, so the Param carries no bounds for `bind` to enforce.
LIMIT: Final = Param(name="limit", kind="integer")
# The two cursors carry the spec's own `maxLength: 5000`; `bind` runs every
# cursor value through these, so the bound is enforced rather than declared.
STARTING_AFTER: Final = Param(name="starting_after", kind="string", max_length=5000)
ENDING_BEFORE: Final = Param(name="ending_before", kind="string", max_length=5000)
# `metadata` has no Param: its whole-parameter sentinel, key limits and
# unset-on-empty-value semantics cannot be expressed in one shape, so
# `_metadata_update` owns them.

# --- Coercion ------------------------------------------------------------------

_RANGE_OPS = ("gt", "gte", "lt", "lte")
_BY_PREFIX: Final[dict[str, str]] = {
    prefix: name for name, prefix in STRIPE_ID_PREFIXES.items() if prefix
}


def _bracket(prefix: str, name: str) -> str:
    return f"{prefix}[{name}]" if prefix else name


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _object_name_for(prefixes: tuple[str, ...], fallback: str) -> str:
    """The Stripe object name a path id's prefix identifies, for the message."""
    names = {_BY_PREFIX[p] for p in prefixes if p in _BY_PREFIX}
    if len(names) == 1:
        return names.pop()
    return fallback


def _choices_message(name: str, choices: tuple[str, ...]) -> str:
    """`Invalid tax_exempt: must be one of none, reverse, or exempt` — the
    list shape this phase's live probe returned (Oxford final `or`)."""
    if len(choices) > 2:
        listed = ", ".join(choices[:-1]) + f", or {choices[-1]}"
    elif len(choices) == 2:
        listed = f"{choices[0]} or {choices[1]}"
    else:
        listed = ", ".join(choices)
    return f"Invalid {name}: must be one of {listed}"


def _invalid(kind: str, path: str, message: str) -> StripeApiError:
    return invalid_request(
        message, code=f"parameter_invalid_{kind}", param=path, pre_execution=True
    )


def _check_string(param: Param, value: object, path: str) -> str | None:
    if not isinstance(value, str):
        raise _invalid("string", path, f"Invalid string: {value!r}")
    if value == "":
        if not param.unset_with_empty_string:
            raise invalid_request(
                f"Parameter {path} cannot be empty.",
                code="parameter_invalid_empty",
                param=path,
                pre_execution=True,
            )
        # Stripe's "" means "clear this field": the column goes NULL.
        return None
    if param.max_length is not None and len(value) > param.max_length:
        raise _invalid("string", path, f"Parameter {path} exceeds {param.max_length} characters.")
    return value


def _check(
    param: Param,
    value: object,
    path: str,
    id_status: int = 400,
) -> Any:
    """Validate one value depth-first; returns the coerced, engine-ready value.

    Coerced shapes: booleans stay `bool` (the engine stores 0/1), objects and
    arrays stay `dict`/`list` (the engine dumps them), timestamps and range
    bounds become canonical ISO text — the conversion is on the parameter, so
    the SQL stays a text comparison (`components/dispatcher.md` §3.5.1).

    `id_status` is the status an unresolvable id refuses at: **400 for a
    request parameter** and 404 for a path one, both probed at the pinned
    version (`POST …/attach {customer: "cus_nope"}` answers 400
    `resource_missing`; `GET /v1/payment_methods/pm_nope` answers 404).
    """
    kind = param.kind
    if kind == "string":
        return _check_string(param, value, path)
    if kind == "literal":
        if not isinstance(value, str) or value not in param.choices:
            # Recorded (Phase 12, the subscriptions cassette): a bad literal
            # answers type/message/param with NO `code` — unlike every
            # `parameter_invalid_*` shape — so this one refusal is built
            # directly rather than through `_invalid`.
            raise invalid_request(
                _choices_message(path, param.choices), param=path, pre_execution=True
            )
        return value
    if kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            # Unquoted: `Invalid integer: abc` / `Invalid integer: 1.5` — the
            # wire-verbatim shape (Phase 5 cassettes, scenario 09).
            raise _invalid("integer", path, f"Invalid integer: {value}")
        if (param.minimum is not None and value < param.minimum) or (
            param.maximum is not None and value > param.maximum
        ):
            low = "-∞" if param.minimum is None else param.minimum
            high = "∞" if param.maximum is None else param.maximum
            raise _invalid("integer", path, f"Invalid integer: {value}; must be {low} to {high}")
        return value
    if kind == "int_literal":
        # An integer-or-literal union: `tiers[].up_to` is a count or the
        # string `inf` on the wire. A string must be one of `choices`; an
        # integer takes the plain integer checks above.
        if isinstance(value, str):
            if value not in param.choices:
                raise _invalid("string", path, _choices_message(path, param.choices))
            return value
        if not isinstance(value, int) or isinstance(value, bool):
            raise _invalid("integer", path, f"Invalid integer: {value}")
        return value
    if kind == "boolean":
        if not isinstance(value, bool):
            raise _invalid("boolean", path, f"Invalid boolean: {value!r}")
        return value
    if kind == "number":
        if not _is_int(value) and not isinstance(value, float):
            raise _invalid("number", path, f"Invalid number: {value!r}")
        return value
    if kind == "id":
        if value is None and param.unset_with_empty_string:
            # The JSON spelling of Stripe's empty-string clear.
            return None
        text = _check_string(param, value, path)
        if text is None:
            if param.unset_with_empty_string:
                # Stripe's `""` clears the field (probed, Phase 12 CR round:
                # a subscription update with `default_payment_method: ""`
                # answers 200 with the field null); the handler writes the
                # column's NULL. A JSON surface spells the same clear `null`.
                return None
            # An id parameter otherwise never declares the sentinel — an
            # authoring mistake, not an agent error.
            raise seahaven.WorldBug(f"id parameter {path!r} cannot be cleared with an empty string")
        if param.id_prefixes and not text.startswith(param.id_prefixes):
            raise resource_missing(
                _object_name_for(param.id_prefixes, param.name), text, param=path, status=id_status
            )
        return text
    if kind == "timestamp":
        if param.unset_with_empty_string and (value is None or value == ""):
            # Stripe's `""` clears a scheduled timestamp (probed, round 6:
            # `cancel_at: ""` clears the scheduled cancel); the JSON
            # spelling of the same clear is `null`.
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            raise _invalid("integer", path, f"Invalid integer: {value}")
        return _time.from_unix(value)
    if kind == "currency":
        return _check_currency(param, value, path)
    if kind == "range":
        return _check_range(param, value, path)
    if kind == "object":
        return _check_object(param, value, path, id_status)
    if kind == "map":
        return _check_map(param, value, path, id_status)
    if kind == "array":
        if not isinstance(value, list):
            raise _invalid("array", path, f"Invalid array: {value!r}")
        if param.item is None:
            raise seahaven.WorldBug(f"array parameter {path!r} declares no item param")
        return [
            _check(param.item, item, f"{path}[{index}]", id_status)
            for index, item in enumerate(value)
        ]
    raise seahaven.WorldBug(f"unknown parameter kind {kind!r} on {path!r}")


def _check_range(param: Param, value: object, path: str) -> dict[str, str]:
    """The `created`-shaped `{gt, gte, lt, lte}` object or a bare integer.

    `spec3.json` models the two as `anyOf: [range_query_specs, integer]`; both
    become one to-four comparisons against the TEXT timestamp column, in
    canonical form.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return {"eq": _time.from_unix(value)}
    if not isinstance(value, dict):
        raise _invalid("string", path, f"Invalid {param.name}: must be an object or an integer")
    bounds: dict[str, str] = {}
    for key, bound in value.items():
        if key not in _RANGE_OPS:
            raise unknown_parameter(_bracket(path, str(key)))
        if not isinstance(bound, int) or isinstance(bound, bool):
            raise _invalid("integer", _bracket(path, key), f"Invalid integer: {bound}")
        bounds[key] = _time.from_unix(bound)
    if not bounds:
        raise _invalid(
            "string", path, f"Invalid {param.name}: at least one of gt, gte, lt, lte is required"
        )
    return bounds


def _check_object(
    param: Param, value: object, path: str, id_status: int = 400
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        if param.unset_with_empty_string and (value is None or value == ""):
            # Stripe's form encoding clears an object parameter with an
            # empty string; a JSON surface spells the same clear `null`
            # (probed Phase 12: `pause_collection=""` clears live). Both
            # map to the column's NULL.
            return None
        raise _invalid("string", path, f"Invalid {param.name}: must be an object")
    by_name = {shape.name: shape for shape in param.shape}
    for key in value:
        if key not in by_name:
            # Probed verbatim this phase: `Received unknown parameter:
            # invoice_settings[nope]`.
            raise unknown_parameter(_bracket(path, key))
    out: dict[str, Any] = {}
    for shape in param.shape:
        if shape.name in value:
            out[shape.name] = _check(
                shape, value[shape.name], _bracket(path, shape.name), id_status
            )
        elif shape.required:
            raise missing_parameter(_bracket(path, shape.name))
    return out


#: The currency codes a map parameter accepts, transcribed in Stripe's own
#: order from the live `Invalid currency` refusal at the pinned version
#: (Phase 7 probe; see `_check_map` for why it is a constant and not a
#: cassette step).
_SUPPORTED_MAP_CURRENCIES_TEXT = (
    "usd, aed, afn, all, amd, ang, aoa, ars, aud, awg, azn, bam, bbd, bdt"
    ", bgn, bhd, bif, bmd, bnd, bob, brl, bsd, bwp, byn, bzd, cad, cdf, chf"
    ", clp, cny, cop, crc, cve, czk, djf, dkk, dop, dzd, egp, etb, eur, fjd"
    ", fkp, gbp, gel, gip, gmd, gnf, gtq, gyd, hkd, hnl, hrk, htg, huf, idr"
    ", ils, inr, isk, jmd, jod, jpy, kes, kgs, khr, kmf, krw, kwd, kyd, kzt"
    ", lak, lbp, lkr, lrd, lsl, mad, mdl, mga, mkd, mmk, mnt, mop, mur, mvr"
    ", mwk, mxn, myr, mzn, nad, ngn, nio, nok, npr, nzd, omr, pab, pen, pgk"
    ", php, pkr, pln, pyg, qar, ron, rsd, rub, rwf, sar, sbd, scr, sek, sgd"
    ", shp, sle, sos, srd, std, szl, thb, tjs, tnd, top, try, ttd, twd, tzs"
    ", uah, ugx, uyu, uzs, vnd, vuv, wst, xaf, xcd, xcg, xof, xpf, yer, zar"
    ", zmw, usdc, eurc, usdt, open_usd, btn, ghs, eek, lvl, svc, vef, ltl, sll"
    ", mro"
)

_SUPPORTED_MAP_CURRENCIES = frozenset(
    (
        "usd",
        "aed",
        "afn",
        "all",
        "amd",
        "ang",
        "aoa",
        "ars",
        "aud",
        "awg",
        "azn",
        "bam",
        "bbd",
        "bdt",
        "bgn",
        "bhd",
        "bif",
        "bmd",
        "bnd",
        "bob",
        "brl",
        "bsd",
        "bwp",
        "byn",
        "bzd",
        "cad",
        "cdf",
        "chf",
        "clp",
        "cny",
        "cop",
        "crc",
        "cve",
        "czk",
        "djf",
        "dkk",
        "dop",
        "dzd",
        "egp",
        "etb",
        "eur",
        "fjd",
        "fkp",
        "gbp",
        "gel",
        "gip",
        "gmd",
        "gnf",
        "gtq",
        "gyd",
        "hkd",
        "hnl",
        "hrk",
        "htg",
        "huf",
        "idr",
        "ils",
        "inr",
        "isk",
        "jmd",
        "jod",
        "jpy",
        "kes",
        "kgs",
        "khr",
        "kmf",
        "krw",
        "kwd",
        "kyd",
        "kzt",
        "lak",
        "lbp",
        "lkr",
        "lrd",
        "lsl",
        "mad",
        "mdl",
        "mga",
        "mkd",
        "mmk",
        "mnt",
        "mop",
        "mur",
        "mvr",
        "mwk",
        "mxn",
        "myr",
        "mzn",
        "nad",
        "ngn",
        "nio",
        "nok",
        "npr",
        "nzd",
        "omr",
        "pab",
        "pen",
        "pgk",
        "php",
        "pkr",
        "pln",
        "pyg",
        "qar",
        "ron",
        "rsd",
        "rub",
        "rwf",
        "sar",
        "sbd",
        "scr",
        "sek",
        "sgd",
        "shp",
        "sle",
        "sos",
        "srd",
        "std",
        "szl",
        "thb",
        "tjs",
        "tnd",
        "top",
        "try",
        "ttd",
        "twd",
        "tzs",
        "uah",
        "ugx",
        "uyu",
        "uzs",
        "vnd",
        "vuv",
        "wst",
        "xaf",
        "xcd",
        "xcg",
        "xof",
        "xpf",
        "yer",
        "zar",
        "zmw",
        "usdc",
        "eurc",
        "usdt",
        "open_usd",
        "btn",
        "ghs",
        "eek",
        "lvl",
        "svc",
        "vef",
        "ltl",
        "sll",
        "mro",
    )
)


#: The currencies a plain `currency` parameter accepts, transcribed in
#: Stripe's own order from the live payout refusal at the pinned version
#: (Phase 11 probe). A DIFFERENT list from the map-parameter one above: the
#: payout spelling carries no `eurc` / `usdt` / `open_usd` — two live lists,
#: two constants, the same transcription-maintenance declaration.
_SUPPORTED_CURRENCIES_TEXT = (
    "usd, aed, afn, all, amd, ang, aoa, ars, aud, awg, azn, bam, bbd, bdt"
    ", bgn, bhd, bif, bmd, bnd, bob, brl, bsd, bwp, byn, bzd, cad, cdf, chf"
    ", clp, cny, cop, crc, cve, czk, djf, dkk, dop, dzd, egp, etb, eur, fjd"
    ", fkp, gbp, gel, gip, gmd, gnf, gtq, gyd, hkd, hnl, hrk, htg, huf, idr"
    ", ils, inr, isk, jmd, jod, jpy, kes, kgs, khr, kmf, krw, kwd, kyd, kzt"
    ", lak, lbp, lkr, lrd, lsl, mad, mdl, mga, mkd, mmk, mnt, mop, mur, mvr"
    ", mwk, mxn, myr, mzn, nad, ngn, nio, nok, npr, nzd, omr, pab, pen, pgk"
    ", php, pkr, pln, pyg, qar, ron, rsd, rub, rwf, sar, sbd, scr, sek, sgd"
    ", shp, sle, sos, srd, std, szl, thb, tjs, tnd, top, try, ttd, twd, tzs"
    ", uah, ugx, uyu, uzs, vnd, vuv, wst, xaf, xcd, xcg, xof, xpf, yer, zar"
    ", zmw, usdc, btn, ghs, eek, lvl, svc, vef, ltl, sll, mro"
)

_SUPPORTED_CURRENCIES = frozenset(_SUPPORTED_CURRENCIES_TEXT.split(", "))


def _check_currency(param: Param, value: object, path: str) -> str:
    """A plain `currency` parameter: a lowercase three-letter code from the
    supported set, else the live full-list refusal (probed verbatim on
    `POST /v1/payouts`, Phase 11 — the same message shape the map-parameter
    check answers, from the payout endpoint's own shorter list)."""
    if not isinstance(value, str):
        raise _invalid("string", path, f"Invalid string: {value!r}")
    if value not in _SUPPORTED_CURRENCIES:
        # No trailing period: the payout endpoint's spelling ends with the
        # list itself (probed, cassette 11), where the map-parameter refusal
        # closes with one — two live spellings, two shapes.
        raise invalid_request(
            f"Invalid currency: {value}. Stripe currently supports these currencies: "
            f"{_SUPPORTED_CURRENCIES_TEXT}",
            param=path,
            pre_execution=True,
        )
    return value


def _check_map(param: Param, value: object, path: str, id_status: int = 400) -> dict[str, Any]:
    """A currency-keyed map (`currency_options[eur][amount_off]=…`): every key
    a supported three-letter currency code in lowercase, every value checked
    against `item`. The per-currency object is bracket-addressed on the wire,
    so nested `param` spellings come out as `currency_options[eur][amount_off]`.

    The refusals are probed live at the pinned version (Phase 7): a non-object
    answers `Invalid object`; a key whose lowercase form is a supported code
    answers the `Currencies must be lowercase … Use 'eur' instead of 'EUR'`
    hint; anything else answers `Invalid currency: <key>. Stripe currently
    supports these currencies: …` with the full ordered list. The list below is
    transcribed from that live probe (154 codes). A cassette step would bind
    it — the replay diffs Stripe's live message against this constant, the
    same check every other verbatim message gets — and it is declined anyway
    as a maintenance choice: the list churns with Stripe's currency support,
    and a committed step would turn every such churn into a cassette
    re-record. The freshness of the transcription is therefore a declared
    property, recorded in allowed_differences.py, not a replay-checked one.
    """
    if not isinstance(value, dict):
        raise invalid_request("Invalid object", param=path, pre_execution=True)
    if param.item is None:
        raise seahaven.WorldBug(f"map parameter {path!r} declares no item param")
    out: dict[str, Any] = {}
    for key, item in value.items():
        lowered = key.lower()
        if key != lowered and key.lower() in _SUPPORTED_MAP_CURRENCIES:
            raise invalid_request(
                f"Currencies must be lowercase when used as keys in a map. "
                f"Use `{lowered}` instead of `{key}`.",
                param=path,
                pre_execution=True,
            )
        if lowered not in _SUPPORTED_MAP_CURRENCIES:
            raise invalid_request(
                f"Invalid currency: {key}. Stripe currently supports these currencies: "
                f"{_SUPPORTED_MAP_CURRENCIES_TEXT}.",
                param=path,
                pre_execution=True,
            )
        out[lowered] = _check(param.item, item, _bracket(path, lowered), id_status)
    return out


def _metadata_update(raw: object) -> MetadataUpdate:
    """The whole-parameter validation and parse of `metadata`."""
    if raw == "" or raw == {}:  # the wire spelling and the JSON spelling both clear
        return MetadataUpdate(clear=True, set={}, unset=frozenset())
    if not isinstance(raw, dict):
        raise invalid_request(
            "Invalid metadata: must be an object",
            code="parameter_invalid_string",
            param="metadata",
            pre_execution=True,
        )
    if len(raw) > METADATA_MAX_KEYS:
        raise invalid_request(
            f"Invalid metadata: cannot have more than {METADATA_MAX_KEYS} keys",
            param="metadata",
            pre_execution=True,
        )
    set_items: dict[str, str] = {}
    unset: set[str] = set()
    for key, value in raw.items():
        param_path = f"metadata[{key}]"
        if len(key) > METADATA_MAX_KEY_LENGTH:
            raise _invalid("string", param_path, "Invalid metadata key: exceeds 40 characters")
        if value is None or value == "":
            unset.add(key)
            continue
        if not isinstance(value, str):
            raise _invalid("string", param_path, f"Invalid metadata value: {value!r}")
        if len(value) > METADATA_MAX_VALUE_LENGTH:
            raise _invalid("string", param_path, "Invalid metadata value: exceeds 500 characters")
        set_items[key] = value
    return MetadataUpdate(clear=False, set=set_items, unset=frozenset(unset))


# --- The effective body of an operation ----------------------------------------

_LIST_FILTER_KIND: Final[dict[str, Kind]] = {
    "exact": "string",
    "range": "range",
    "literal": "literal",
    "boolean": "boolean",
    "in": "array",
    "json": "object",
}
_list_filter_cache: dict[str, tuple[Param, ...]] = {}


def _list_filter_params(resource: ResourceSpec) -> tuple[Param, ...]:
    cached = _list_filter_cache.get(resource.object)
    if cached is None:
        params = []
        for flt in resource.list_filters:
            # An exact filter with id prefixes is an id parameter: the prefix
            # is checked here (at 400 — query side), while *existence* is the
            # engine's `references` lookup, which bind never does.
            kind = _LIST_FILTER_KIND[flt.kind]
            item: Param | None = None
            shape: tuple[Param, ...] = ()
            if kind == "string" and flt.id_prefixes:
                kind = "id"
            elif kind == "array":
                item = Param(name="", kind="id" if flt.id_prefixes else "string")
            elif kind == "object":
                shape = flt.sub_shape
            params.append(
                Param(
                    name=flt.name,
                    kind=kind,
                    choices=flt.choices,
                    id_prefixes=flt.id_prefixes,
                    required=flt.required,
                    item=item,
                    shape=shape,
                )
            )
        cached = tuple(params)
        _list_filter_cache[resource.object] = cached
    return cached


def body_of(route: Route) -> tuple[Param, ...]:
    """The effective body parameters of a wired route.

    A generated list serves its `ResourceSpec.list_filters` as parameters
    (`components/dispatcher.md` §3.5.1); everything else serves its
    `ParamSpec.body` as declared. The pruner reads the same function for
    `stripe_api_details`' request-body filter, so the documented set and the
    enforced set cannot disagree.
    """
    spec = route.params
    if spec is None:
        raise seahaven.WorldBug(f"route {route.op_id} is not wired")
    if route.action == "list" and route.resource is not None:
        return _list_filter_params(route.resource)
    return spec.body


# --- bind ----------------------------------------------------------------------


def bind(
    route: Route,
    path_values: Sequence[str],
    path: str,
    raw: Mapping[str, Any],
    *,
    idempotency_key: str | None,
) -> Request:
    """Validate `raw` against `route.params`, lift the central five, and build
    the `Request` for one concrete call at `path`.

    Raises `StripeApiError(400/404, …)` for anything wrong, with `param` in
    Stripe's bracket notation. Reads no tables and writes nothing: it runs
    before the call's transaction has anything to roll back.
    """
    # local: response imports serialize.expand, whose registry reaches back
    # into dispatch.resource; loading it lazily keeps `bind` importable first.
    from seahaven_stripe_world.dispatch.response import Page, Request

    spec = route.params
    if spec is None:
        raise seahaven.WorldBug(f"route {route.op_id} is not wired")

    # 1. Path — prefix-checked here so `/v1/customers/ch_123` is a 404 naming
    #    the missing id before any query runs. `ParamSpec.path` carries
    #    placeholder *names*, so the expected prefix comes from a body `Param`
    #    named after the placeholder when an author declares one, else from the
    #    resource the route serves (its own id for the last placeholder, the
    #    scope's parent for a scoping one). The `param` the error names is the
    #    placeholder by default, but live Stripe is per-resource inconsistent
    #    here (probed: customers and charges say `id`, prices and
    #    payment_methods keep the placeholder; nested sub-resource paths keep
    #    the placeholder), so a resource may pin its own spelling and each
    #    slice's recordings do.
    body_by_name = {param.name: param for param in spec.body}
    path_params: dict[str, str] = {}
    last = len(spec.path) - 1
    for index, (name, value) in enumerate(zip(spec.path, path_values, strict=True)):
        param = body_by_name.get(name)
        if param is None:
            prefixes: tuple[str, ...] = ()
            if route.scope is not None and name == route.scope.path_param:
                prefixes = (route.scope.parent.id_prefix,)
            elif route.resource is not None and index == last:
                prefixes = (route.resource.id_prefix,)
            param = Param(name=name, kind="id", id_prefixes=prefixes)
        error_name = name
        if (
            route.resource is not None
            and index == last
            and route.resource.missing_path_param is not None
        ):
            error_name = route.resource.missing_path_param
        # A path id refuses at 404; a request-parameter one at 400 (probed
        # both ways — see `_check`).
        path_params[name] = _check(param, value, error_name, id_status=404)

    # 2. Lift the five. A lifted parameter the operation does not accept is
    #    `parameter_unknown`, the same as any other.
    remaining = dict(raw)
    expand_raw = remaining.pop("expand", None)
    limit_raw = remaining.pop("limit", None)
    starting_after_raw = remaining.pop("starting_after", None)
    ending_before_raw = remaining.pop("ending_before", None)
    metadata_raw = remaining.pop("metadata", None)

    expand: tuple[str, ...] = ()
    if expand_raw is not None:
        if not spec.expand:
            raise unknown_parameter("expand")
        expand = tuple(_check(EXPAND, expand_raw, "expand"))
    if expand:
        # Static path validation, in this layer, with no database access
        # (cross_cutting.md §3.3.1): a bad path is caught before any row is
        # written and before any lookup runs — probed live this round, a bad
        # path outranks both an empty filtered page and a missing resource —
        # and it is pre-execution, so a later idempotency layer will let the
        # agent retry the key with a corrected path.
        from seahaven_stripe_world.serialize.expand import validate_paths

        if route.response_object is None or route.envelope is None:
            # The router refuses this shape at import; reaching here means a
            # route was built outside the table.
            raise seahaven.WorldBug(
                f"route {route.op_id} accepts expand but declares no response_object/envelope"
            )
        validate_paths(
            expand, object_name=route.response_object, is_list=(route.envelope == "list")
        )
    page: Page | None = None
    if spec.paginated:
        limit = DEFAULT_LIMIT if limit_raw is None else _check(LIMIT, limit_raw, "limit")
        # The silent clamp the live API applies (see DEFAULT_LIMIT): 0 and
        # negatives become 1, anything above 100 becomes 100.
        limit = max(LIMIT_FLOOR, min(LIMIT_CEILING, limit))
        starting_after = (
            None
            if starting_after_raw is None
            else _check(STARTING_AFTER, starting_after_raw, "starting_after")
        )
        ending_before = (
            None
            if ending_before_raw is None
            else _check(ENDING_BEFORE, ending_before_raw, "ending_before")
        )
        # Both cursors present is *not* rejected here: the live API resolves
        # each cursor first and only then refuses the pair (probed with a
        # bogus + a real id — the bogus one's 400 wins; Phase 5 cassettes,
        # scenario 09), so the exclusivity check lives in `page()` /
        # `page_embedded()`, after resolution.
        page = Page(limit=limit, starting_after=starting_after, ending_before=ending_before)
    else:
        for name, value in (
            ("limit", limit_raw),
            ("starting_after", starting_after_raw),
            ("ending_before", ending_before_raw),
        ):
            if value is not None:
                raise unknown_parameter(name)
    metadata = None
    if metadata_raw is not None:
        if not spec.metadata:
            raise unknown_parameter("metadata")
        metadata = _metadata_update(metadata_raw)

    # 3. Allowlist. 4. Required. 5. Coerce, depth-first.
    body = body_of(route)
    by_name = {param.name: param for param in body}
    for key in remaining:
        if key not in by_name:
            raise unknown_parameter(key)
    params: dict[str, Any] = {}
    for param in body:
        if param.name in remaining:
            params[param.column_name] = _check(param, remaining[param.name], param.name)
        elif param.required:
            raise missing_parameter(param.name)

    # 6. Cross-parameter declarations.
    for group in spec.required_one_of:
        if not any(name in remaining for name in group):
            raise invalid_request(f"One of {' or '.join(group)} is required", pre_execution=True)
    for group in spec.mutually_exclusive:
        present = [name for name in group if name in remaining]
        if len(present) > 1:
            raise invalid_request(
                f"Parameters {', '.join(present)} are mutually exclusive",
                param=present[1],
                pre_execution=True,
            )

    return Request(
        method=route.method,
        path=path,
        route=route,
        path_params=path_params,
        params=params,
        expand=expand,
        metadata=metadata,
        page=page,
        idempotency_key=idempotency_key,
    )
