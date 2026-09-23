"""Enumerate the real Stripe MCP's operation catalogue.

    python -m tools_dev.enumerate_catalogue              # full run, resume by default
    python -m tools_dev.enumerate_catalogue --sample 50  # probe 50 random uncovered ops

For every ``operationId`` in the committed ``spec3.json``, calls the real Stripe
MCP's ``stripe_api_details`` and records the verdict:

- **catalogued** — the MCP returned a details document; record ``required_permissions``.
- **absent** — the MCP answered "Operation '...' is not available."
- **error** — anything else (transport fault, unexpected shape).

Results are written to ``src/seahaven_stripe_world/spec/mcp_catalogue.jsonl``,
one JSON record per line, appended and flushed as they arrive. A partial file is
a valid partial result, and a re-run reads the ``op`` set already present and
probes only the gaps.

The script also records the live server's tool list as a ``{"kind": "tools", ...}``
record (functional spec section 4.1.1, architecture section 4.2).

**This is the only step in the project that requires a live Stripe MCP connection.**
CI never runs it; every later phase tests against committed artifacts.

Environment:
    Requires ``STRIPE_MCP_ENDPOINT`` and ``STRIPE_MCP_TOKEN`` (or reads from
    ``.env``). Connects to the real Stripe MCP via its Streamable HTTP transport.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SPEC_PATH = REPO / "src" / "seahaven_stripe_world" / "spec" / "spec3.json"
CATALOGUE_PATH = REPO / "src" / "seahaven_stripe_world" / "spec" / "mcp_catalogue.jsonl"

NOT_AVAILABLE_PREFIX = "Operation '"
NOT_AVAILABLE_SUFFIX = "' is not available."


def _load_operation_ids() -> list[str]:
    """Every operationId in the committed full spec, sorted."""
    spec = json.loads(SPEC_PATH.read_text())
    op_ids: set[str] = set()
    for path_item in spec["paths"].values():
        for method in ("get", "post", "delete", "put", "patch"):
            operation = path_item.get(method)
            if isinstance(operation, dict) and "operationId" in operation:
                op_ids.add(operation["operationId"])
    return sorted(op_ids)


def _load_existing(path: Path) -> dict[str, dict[str, Any]]:
    """Read the existing catalogue, keyed by operation id."""
    existing: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return existing
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        if record.get("kind") == "tools":
            continue
        op = record.get("op")
        if op:
            existing[op] = record
    return existing


def _call_stripe_mcp_details(
    endpoint: str, token: str, op_id: str, stripe_context: str, livemode: bool
) -> dict[str, Any]:
    """Call stripe_api_details on the real MCP via Streamable HTTP.

    Returns the parsed JSON result from the MCP tool call.  Raises on
    transport-level failures.
    """
    import urllib.request

    # Build the MCP tool call request
    call_id = f"enum-{op_id}"
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {
                "name": "stripe_api_details",
                "arguments": {
                    "stripe_api_operation_id": op_id,
                    "stripe_context": stripe_context,
                    "livemode": livemode,
                },
            },
        }
    ).encode()

    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _list_tools(endpoint: str, token: str) -> list[str]:
    """List the tools available on the MCP server."""
    import urllib.request

    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": "list-tools",
            "method": "tools/list",
            "params": {},
        }
    ).encode()

    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())

    tools = result.get("result", {}).get("tools", [])
    return [t["name"] for t in tools]


def _classify_response(result: dict[str, Any], op_id: str) -> dict[str, Any]:
    """Classify an MCP tool call response into a catalogue record."""
    probed = datetime.now(UTC).strftime("%Y-%m-%d")

    # Check for error responses
    error = result.get("error")
    if error:
        return {"op": op_id, "verdict": "error", "detail": str(error), "probed": probed}

    # The result is in result.result.content
    tool_result = result.get("result", {})
    content = tool_result.get("content", [])

    # Check if it is an error (isError flag)
    if tool_result.get("isError"):
        # The text content contains the error message
        text = ""
        for item in content:
            if item.get("type") == "text":
                text = item.get("text", "")
                break
        if NOT_AVAILABLE_PREFIX in text and NOT_AVAILABLE_SUFFIX in text:
            return {"op": op_id, "verdict": "absent", "probed": probed}
        return {"op": op_id, "verdict": "error", "detail": text[:200], "probed": probed}

    # Parse the successful response to extract required_permissions
    text = ""
    for item in content:
        if item.get("type") == "text":
            text = item.get("text", "")
            break

    if not text:
        return {"op": op_id, "verdict": "error", "detail": "empty response", "probed": probed}

    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        # Not JSON — might be the "not available" plain text
        if NOT_AVAILABLE_PREFIX in text:
            return {"op": op_id, "verdict": "absent", "probed": probed}
        return {"op": op_id, "verdict": "error", "detail": "non-json", "probed": probed}

    # A details document means catalogued
    permissions = doc.get("required_permissions", [])
    record: dict[str, Any] = {"op": op_id, "verdict": "catalogued", "probed": probed}
    if permissions:
        record["permissions"] = permissions
    return record


def _load_env() -> None:
    """Load .env if present (simple key=value, no quoting)."""
    env_path = REPO / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Enumerate the real Stripe MCP's operation catalogue."
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        metavar="N",
        help="Probe only N randomly-selected uncovered operations instead of all",
    )
    args = parser.parse_args(argv)

    _load_env()
    endpoint = os.environ.get("STRIPE_MCP_ENDPOINT")
    token = os.environ.get("STRIPE_MCP_TOKEN")
    stripe_context = os.environ.get("STRIPE_MCP_CONTEXT", "")
    livemode = os.environ.get("STRIPE_MCP_LIVEMODE", "false").lower() == "true"

    if not endpoint or not token:
        print(
            "Set STRIPE_MCP_ENDPOINT and STRIPE_MCP_TOKEN in the environment or .env",
            file=sys.stderr,
        )
        return 1
    if not stripe_context:
        print(
            "Set STRIPE_MCP_CONTEXT to the account's stripe_context value",
            file=sys.stderr,
        )
        return 1

    all_ops = _load_operation_ids()
    print(f"spec3.json has {len(all_ops)} operation ids")

    existing = _load_existing(CATALOGUE_PATH)
    uncovered = [
        op for op in all_ops
        if op not in existing or existing[op].get("verdict") == "error"
    ]
    print(f"catalogue has {len(existing)} records, {len(uncovered)} uncovered")

    if not uncovered:
        print("nothing to do — all operations already probed")
        return 0

    # Record the tool list first
    try:
        tools = _list_tools(endpoint, token)
        print(f"live tool list: {tools}")
        with open(CATALOGUE_PATH, "a") as f:
            record = {
                "kind": "tools",
                "tools": sorted(tools),
                "probed": datetime.now(UTC).strftime("%Y-%m-%d"),
            }
            f.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
            f.flush()
    except Exception as exc:
        print(f"warning: could not list tools: {exc}", file=sys.stderr)

    # Determine which operations to probe
    if args.sample is not None:
        to_probe = random.sample(uncovered, min(args.sample, len(uncovered)))
        to_probe.sort()
        # Write a sampled marker
        with open(CATALOGUE_PATH, "a") as f:
            marker = {
                "kind": "sample_marker",
                "sampled": True,
                "sample_size": len(to_probe),
                "total_uncovered": len(uncovered),
                "probed": datetime.now(UTC).strftime("%Y-%m-%d"),
            }
            f.write(json.dumps(marker, sort_keys=True, ensure_ascii=False) + "\n")
            f.flush()
        print(f"sampling {len(to_probe)} of {len(uncovered)} uncovered operations")
    else:
        to_probe = uncovered
        print(f"probing all {len(to_probe)} uncovered operations")

    # Probe each operation
    succeeded = 0
    failed = 0
    for i, op_id in enumerate(to_probe):
        try:
            result = _call_stripe_mcp_details(endpoint, token, op_id, stripe_context, livemode)
            record = _classify_response(result, op_id)
        except Exception as exc:
            record = {
                "op": op_id,
                "verdict": "error",
                "detail": f"transport: {exc}",
                "probed": datetime.now(UTC).strftime("%Y-%m-%d"),
            }
            failed += 1
        else:
            if record["verdict"] == "error":
                failed += 1
            else:
                succeeded += 1

        # Append and flush immediately
        with open(CATALOGUE_PATH, "a") as f:
            f.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
            f.flush()

        verdict = record["verdict"]
        progress = f"[{i + 1}/{len(to_probe)}]"
        print(f"  {progress} {op_id}: {verdict}")

    print(f"\ndone: {succeeded} succeeded, {failed} errors out of {len(to_probe)} probed")
    if failed > 0:
        print(
            "warning: error records are NOT treated as absent — re-run to retry them",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
