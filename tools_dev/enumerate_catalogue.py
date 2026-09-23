"""Enumerate the real Stripe MCP's operation catalogue.

    STRIPE_SECRET_KEY=sk_test_... python -m tools_dev.enumerate_catalogue
    python -m tools_dev.enumerate_catalogue --sample 50

For every ``operationId`` in the committed ``spec3.json``, calls the real Stripe
MCP's ``stripe_api_details`` and records the verdict:

- **catalogued** — the MCP returned a details document; record ``required_permissions``.
- **absent** — the MCP answered "Operation '...' is not available."
- **key_restricted** — the MCP answered "... is not available with secret key type."
  The operation is catalogued for OAuth sessions; probe it over OAuth and record
  it as ``catalogued`` with ``"key_restricted": true, "via": "oauth"``.
- **error** — anything else (transport fault, unexpected shape).

Results are written to ``src/seahaven_stripe_world/spec/mcp_catalogue.jsonl``,
one JSON record per line, appended and flushed as they arrive. A partial file is
a valid partial result, and a re-run reads the ``op`` set already present and
probes only the gaps (error-verdict records are re-probed).

The script also records the live server's tool list as a ``{"kind": "tools", ...}``
record (functional spec section 4.1.1, architecture section 4.2).

**This is the only step in the project that requires a live Stripe MCP connection.**
CI never runs it; every later phase tests against committed artifacts.

Authentication:
    Set ``STRIPE_SECRET_KEY`` in the environment or in ``.env``. A test-mode key
    (``sk_test_`` or ``rk_test_`` prefix) is required; live-mode keys are refused.
    Stripe's hosted MCP at ``https://mcp.stripe.com`` accepts the secret key as a
    Bearer token directly.

    A key-authenticated session exposes a different tool surface from an OAuth
    one: ``stripe_api_details`` takes only ``stripe_api_operation_id`` (no
    ``stripe_context``/``livemode``), and ``list_available_accounts_or_orgs`` is
    absent. The recorded ``tools`` line therefore reflects the key surface.

    Override the endpoint with ``STRIPE_MCP_ENDPOINT`` if needed (default:
    ``https://mcp.stripe.com``).
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

DEFAULT_ENDPOINT = "https://mcp.stripe.com"
TEST_KEY_PREFIXES = ("sk_test_", "rk_test_")
PROTOCOL_VERSION = "2025-06-18"

NOT_AVAILABLE_PREFIX = "Operation '"
NOT_AVAILABLE_SUFFIX = "' is not available."
KEY_RESTRICTED_SUFFIX = "' is not available with secret key type."


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
        if record.get("kind") in ("tools", "sample_marker"):
            continue
        op = record.get("op")
        if op:
            existing[op] = record
    return existing


def _parse_sse_response(raw: bytes, request_id: str | None = None) -> Any:
    """Parse a ``text/event-stream`` body and return the JSON-RPC message.

    SSE frames are delimited by blank lines.  Each frame may contain
    ``event:`` and ``data:`` fields.  We extract the ``data:`` payload from
    the first ``message`` event whose JSON-RPC ``id`` matches *request_id*
    (or the first message event if *request_id* is ``None``).
    """
    text = raw.decode("utf-8", errors="replace")
    current_event = ""
    current_data_lines: list[str] = []

    for line in text.split("\n"):
        if line == "":
            # End of frame — process if it is a message event with data
            if current_data_lines and current_event in ("message", ""):
                payload = "\n".join(current_data_lines)
                try:
                    parsed = json.loads(payload)
                except json.JSONDecodeError:
                    pass
                else:
                    if request_id is None or parsed.get("id") == request_id:
                        return parsed
            current_event = ""
            current_data_lines = []
            continue

        if line.startswith("event:"):
            current_event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            current_data_lines.append(line[len("data:") :].strip())

    # Handle final frame without trailing blank line
    if current_data_lines and current_event in ("message", ""):
        payload = "\n".join(current_data_lines)
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            pass
        else:
            if request_id is None or parsed.get("id") == request_id:
                return parsed

    msg = "no matching JSON-RPC message found in SSE stream"
    raise RuntimeError(msg)


class _McpSession:
    """Minimal MCP Streamable HTTP client with protocol handshake."""

    def __init__(self, endpoint: str, token: str) -> None:
        self.endpoint = endpoint
        self.token = token
        self.session_id: str | None = None
        self.negotiated_version: str = PROTOCOL_VERSION
        self._next_id = 0

    def _alloc_id(self) -> str:
        self._next_id += 1
        return str(self._next_id)

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
        }
        if self.session_id is not None:
            headers["Mcp-Session-Id"] = self.session_id
        if self.negotiated_version:
            headers["MCP-Protocol-Version"] = self.negotiated_version
        return headers

    def _send(
        self,
        body: dict[str, Any],
        *,
        expect_response: bool = True,
    ) -> Any:
        """Send a JSON-RPC message and optionally read the response."""
        import urllib.request

        encoded = json.dumps(body, ensure_ascii=False).encode()
        req = urllib.request.Request(
            self.endpoint,
            data=encoded,
            headers=self._headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            # Capture session id from any response
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self.session_id = sid

            if not expect_response:
                return None

            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read()

            if content_type.startswith("text/event-stream"):
                return _parse_sse_response(raw, body.get("id"))
            return json.loads(raw)

    def request(self, method: str, params: Any) -> Any:
        """Send a JSON-RPC request and return the parsed response."""
        rpc_id = self._alloc_id()
        return self._send(
            {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params},
        )

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification (no ``id``, no response expected)."""
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        self._send(body, expect_response=False)

    def initialize(self) -> dict[str, Any]:
        """Perform the MCP initialize / initialized handshake."""
        result = self._send(
            {
                "jsonrpc": "2.0",
                "id": self._alloc_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "enumerate_catalogue",
                        "version": "0.1.0",
                    },
                },
            },
        )
        # Record the negotiated version from the server
        server_version = result.get("result", {}).get("protocolVersion", PROTOCOL_VERSION)
        self.negotiated_version = server_version
        # Send the initialized notification
        self.notify("notifications/initialized", {})
        return result

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a tool on the MCP server."""
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        return result  # type: ignore[no-any-return]

    def list_tools(self) -> list[str]:
        """List the tools available on the MCP server."""
        result = self.request("tools/list", {})
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
        # Catalogued, but hidden from secret-key sessions (Balance, Issuing and
        # Payouts reads). The OAuth surface this world mirrors does serve them, so
        # these need an OAuth probe; they are not re-probed on resume.
        if KEY_RESTRICTED_SUFFIX in text:
            return {"op": op_id, "verdict": "key_restricted", "probed": probed}
        return {"op": op_id, "verdict": "error", "detail": text[:200], "probed": probed}

    # The details document arrives as ``structuredContent``; ``content`` carries
    # a Markdown rendering of it, not JSON.
    structured = tool_result.get("structuredContent")
    if isinstance(structured, dict) and structured.get("id") == op_id:
        record = {"op": op_id, "verdict": "catalogued", "probed": probed}
        if structured.get("required_permissions"):
            record["permissions"] = structured["required_permissions"]
        return record

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


def _validate_key(key: str) -> None:
    """Refuse live-mode keys. Only test keys are accepted."""
    if not any(key.startswith(prefix) for prefix in TEST_KEY_PREFIXES):
        print(
            "error: STRIPE_SECRET_KEY must be a test-mode key (sk_test_... or rk_test_...).\n"
            "Live-mode keys are refused for safety.",
            file=sys.stderr,
        )
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Enumerate the real Stripe MCP's operation catalogue.",
        epilog="Set STRIPE_SECRET_KEY in the environment or .env (test-mode key required).",
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
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    endpoint = os.environ.get("STRIPE_MCP_ENDPOINT", DEFAULT_ENDPOINT)

    if not key:
        print(
            "error: STRIPE_SECRET_KEY is not set.\n"
            "\n"
            "Set it in the environment or in .env with a test-mode Stripe key:\n"
            "  STRIPE_SECRET_KEY=sk_test_...\n"
            "\n"
            "Then run:\n"
            "  python -m tools_dev.enumerate_catalogue",
            file=sys.stderr,
        )
        return 1

    _validate_key(key)

    print(f"endpoint: {endpoint}")

    # Open an MCP session with the server
    session = _McpSession(endpoint, key)
    print("initializing MCP session...")
    try:
        session.initialize()
    except Exception as exc:
        print(f"error: MCP initialize handshake failed: {exc}", file=sys.stderr)
        return 1
    print(f"protocol version: {session.negotiated_version}")

    all_ops = _load_operation_ids()
    print(f"spec3.json has {len(all_ops)} operation ids")

    existing = _load_existing(CATALOGUE_PATH)
    uncovered = [
        op for op in all_ops if op not in existing or existing[op].get("verdict") == "error"
    ]
    print(f"catalogue has {len(existing)} records, {len(uncovered)} uncovered")

    if not uncovered:
        print("nothing to do — all operations already probed")
        return 0

    # Record the tool list first
    try:
        tools = session.list_tools()
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
            result = session.call_tool(
                "stripe_api_details",
                {"stripe_api_operation_id": op_id},
            )
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
