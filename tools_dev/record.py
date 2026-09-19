"""Record conformance cassettes against real Stripe test mode.

    python -m tools_dev.record --scenario 02_pagination_cursor_against_deleted_id
    python -m tools_dev.record --scenario 02_... --scenario 03_...
    python -m tools_dev.record --all
    python -m tools_dev.record --list

Talks to the real API **exclusively** through `stripe-python`'s
`StripeClient.raw_request` — never the generated per-resource classes — so
request encoding is the SDK's business and this repository contains no form
encoder (functional spec §2.2). One cassette file per `--scenario`, each
recorded independently: a mid-run failure leaves the earlier scenarios'
fresh files on disk untouched, and re-running picks up where it stopped.

Reads `STRIPE_CONFORMANCE_TEST_KEY` from the environment, falling back to the
repo `.env`'s `STRIPE_SECRET_KEY`. Refuses to run — before making any
request — if no key is found or it is not `sk_test_`/`rk_test_`-prefixed.
There is no live-mode escape hatch.

CI never runs this: the suite replays committed cassettes and never imports
`stripe` (enforced by a static test over `tests/conformance/`,
`tests/schema_conformance/` and `src/stripeapi/`).
"""

import argparse
import sys
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# The scenario DSL and the cassette format live across two roots (tools_dev
# at the repo root, conformance under tests/); both entry points — this CLI
# and pytest — need both importable.
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import stripe  # noqa: E402  (recording-only; see the module docstring)
from stripe import StripeClient  # noqa: E402

from conformance import redact  # noqa: E402
from conformance.cassette import Cassette, Step, dump  # noqa: E402
from tools_dev.scenarios import registry  # noqa: E402
from tools_dev.scenarios._dsl import PINNED_VERSION, Recorder, Wire  # noqa: E402
from tools_dev.scenarios._key import RecordRefused, resolve_api_key  # noqa: E402

CASSETTES_DIR = REPO_ROOT / "tests" / "conformance" / "cassettes"


def _as_dict(value: object, label: str) -> dict:
    """The parsed JSON body as a plain dict — `raw_request` hand back SDK
    types this file never needs to keep."""
    if not isinstance(value, dict):
        raise SystemExit(f"recorder transport: {label} did not parse to a JSON object")
    return dict(value)


def record(scenario: str, client: StripeClient) -> Path:
    module = import_module(f"tools_dev.scenarios.{registry()[scenario]}")

    def transport(wire: Wire) -> tuple[int, dict]:
        # `stripe_version` and `idempotency_key` ride as request options,
        # folded into headers by the SDK — the same places the world's own
        # write tool carries them.
        options: dict = {"stripe_version": wire.stripe_version}
        if wire.idempotency_key is not None:
            options["idempotency_key"] = wire.idempotency_key
        try:
            response = client.raw_request(wire.method.lower(), wire.path, **wire.params, **options)
            return response.code, _as_dict(response.data, wire.path)
        except stripe.StripeError as error:
            # `raw_request` raises on non-2xx (`_api_requestor` interprets
            # every error status as an exception); the envelope is on the
            # exception, and headers are dropped here — never stored.
            body = error.json_body or {"error": {"type": "api_error"}}
            return int(error.http_status or 0), _as_dict(body, wire.path)

    recorder = Recorder(transport)
    module.record(recorder)

    steps: list[Step] = []
    findings: list[str] = []
    for captured in recorder.captured:
        step = Step(
            seq=captured.seq,
            method=captured.method,
            path=captured.pattern,
            path_refs=captured.path_refs,
            params=captured.declared_params,
            idempotency_key=captured.idempotency_key,
            binds_as=captured.binds_as,
            recorded_status=captured.status,
            recorded_body=captured.body,
            recorded_stripe_version=captured.stripe_version,
        )
        scrubbed, step_findings = redact.scrub(step)
        steps.append(scrubbed)
        findings.extend(f"[{scenario} step {captured.seq}] {finding}" for finding in step_findings)
    if findings:
        for finding in findings:
            print(f"  REDACTION FINDING: {finding}", file=sys.stderr)
        raise SystemExit(
            f"{scenario}: {len(findings)} redaction finding(s); cassette NOT written.\n"
            "Fix the scenario script — never commit a recording that needed a scrub."
        )
    cassette = Cassette(
        scenario=scenario,
        description=module.DESCRIPTION,
        recorded_at=datetime.now(UTC).isoformat(timespec="seconds"),
        stripe_version_pin=PINNED_VERSION,
        steps=tuple(steps),
    )
    path = CASSETTES_DIR / f"{scenario}.json"
    dump(cassette, path)
    _cleanup(module, recorder, client)
    return path


def _cleanup(module: object, recorder: Recorder, client: StripeClient) -> None:
    """Delete what the scenario created, so a re-record starts clean.

    A scenario isolates itself with its own email cohort, but a leftover
    cohort from an earlier run doubles the matches on a re-record's filtered
    lists — exactly the pollution a failed run leaves behind. Cleanup calls
    are made outside `Recorder.step()`, so they never become cassette steps
    (`components/conformance.md`, "Time-dependent preconditions" precedent).
    Best-effort: a delete that fails is printed, not raised.

    A scenario opts in with `CLEANUP = {"<object>": "/v1/<collection>"}` —
    the discriminator-to-URL map is per scenario because it is not derivable
    (`customer` -> `/v1/customers`, but `credit_note` -> `/v1/credit_notes`).
    """
    urls: dict[str, str] = getattr(module, "CLEANUP", {})
    if not urls:
        return
    for captured in recorder.captured:
        object_name = captured.body.get("object")
        created_id = captured.body.get("id")
        if captured.method != "POST" or object_name not in urls or not isinstance(created_id, str):
            continue
        try:
            status, _ = _raw_delete(client, f"{urls[object_name]}/{created_id}")
        except stripe.StripeError as error:
            print(f"  cleanup: {created_id} raised {type(error).__name__}", file=sys.stderr)
            continue
        if status >= 300:
            print(f"  cleanup: {created_id} answered {status}", file=sys.stderr)


def _raw_delete(client: StripeClient, path: str) -> tuple[int, dict]:
    try:
        response = client.raw_request("delete", path, stripe_version=PINNED_VERSION)
        return response.code, _as_dict(response.data, path)
    except stripe.StripeError as error:
        return int(error.http_status or 0), _as_dict(error.json_body or {}, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scenario", action="append", help="scenario name (repeatable)")
    group.add_argument("--all", action="store_true", help="re-record every registered scenario")
    group.add_argument("--list", action="store_true", help="print scenario names; no network")
    args = parser.parse_args(argv)

    if args.list:
        for name in registry():
            print(name)
        return 0

    scenarios = list(registry()) if args.all else list(args.scenario)
    unknown = [name for name in scenarios if name not in registry()]
    if unknown:
        print(f"unknown scenario(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    try:
        key = resolve_api_key(env_file=REPO_ROOT / ".env")
    except RecordRefused as refused:
        print(f"refusing to record: {refused}", file=sys.stderr)
        return 2

    client = StripeClient(api_key=key)
    for scenario in scenarios:
        path = record(scenario, client)
        print(f"recorded {path.name}: {len(path.read_text().splitlines())} lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
