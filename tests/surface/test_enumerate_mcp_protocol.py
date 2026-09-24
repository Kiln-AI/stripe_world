"""Offline tests for the MCP protocol helpers in enumerate_catalogue.

These verify SSE parsing, session header construction, and key validation
without a live server connection.
"""

from __future__ import annotations

import pytest
from tools_dev.enumerate_catalogue import (
    PROTOCOL_VERSION,
    _McpSession,
    _parse_sse_response,
    _validate_key,
)

# ---------------------------------------------------------------------------
# SSE parsing
# ---------------------------------------------------------------------------


class TestParseSSE:
    """Test ``_parse_sse_response`` against sample SSE payloads."""

    def test_simple_message_event(self) -> None:
        """A single ``event: message`` frame with a JSON-RPC result."""
        raw = b'event: message\ndata: {"jsonrpc":"2.0","id":"1","result":{"tools":[]}}\n\n'
        parsed = _parse_sse_response(raw, "1")
        assert parsed["id"] == "1"
        assert parsed["result"] == {"tools": []}

    def test_id_filtering(self) -> None:
        """When multiple frames exist, only the matching id is returned."""
        raw = (
            b"event: message\n"
            b'data: {"jsonrpc":"2.0","id":"1","result":"first"}\n'
            b"\n"
            b"event: message\n"
            b'data: {"jsonrpc":"2.0","id":"2","result":"second"}\n'
            b"\n"
        )
        parsed = _parse_sse_response(raw, "2")
        assert parsed["result"] == "second"

    def test_no_event_field_defaults_to_message(self) -> None:
        """Frames without an explicit ``event:`` line are treated as messages."""
        raw = b'data: {"jsonrpc":"2.0","id":"x","result":"ok"}\n\n'
        parsed = _parse_sse_response(raw, "x")
        assert parsed["result"] == "ok"

    def test_multi_line_data(self) -> None:
        """Multiple ``data:`` lines in a frame are joined with newlines."""
        # The JSON is split across two data lines (unusual but valid SSE)
        raw = b'event: message\ndata: {"jsonrpc":"2.0","id":"1",\ndata: "result":"joined"}\n\n'
        parsed = _parse_sse_response(raw, "1")
        assert parsed["result"] == "joined"

    def test_no_trailing_blank_line(self) -> None:
        """A frame without a trailing blank line is still processed."""
        raw = b'event: message\ndata: {"jsonrpc":"2.0","id":"1","result":"ok"}\n'
        parsed = _parse_sse_response(raw, "1")
        assert parsed["result"] == "ok"

    def test_no_match_raises(self) -> None:
        """RuntimeError if no frame matches the requested id."""
        raw = b'event: message\ndata: {"jsonrpc":"2.0","id":"1","result":"ok"}\n\n'
        with pytest.raises(RuntimeError, match="no matching"):
            _parse_sse_response(raw, "999")

    def test_none_request_id_returns_first(self) -> None:
        """With ``request_id=None``, the first message event is returned."""
        raw = (
            b"event: message\n"
            b'data: {"jsonrpc":"2.0","id":"1","result":"first"}\n'
            b"\n"
            b"event: message\n"
            b'data: {"jsonrpc":"2.0","id":"2","result":"second"}\n'
            b"\n"
        )
        parsed = _parse_sse_response(raw)
        assert parsed["result"] == "first"

    def test_non_message_events_skipped(self) -> None:
        """Events that are not ``message`` are ignored."""
        raw = (
            b"event: ping\n"
            b"data: {}\n"
            b"\n"
            b"event: message\n"
            b'data: {"jsonrpc":"2.0","id":"1","result":"real"}\n'
            b"\n"
        )
        parsed = _parse_sse_response(raw, "1")
        assert parsed["result"] == "real"


# ---------------------------------------------------------------------------
# Session header construction
# ---------------------------------------------------------------------------


class TestMcpSessionHeaders:
    """Verify ``_McpSession._headers()`` includes the required fields."""

    def test_accept_header(self) -> None:
        session = _McpSession("https://example.com", "sk_test_xxx")
        headers = session._headers()
        assert "application/json" in headers["Accept"]
        assert "text/event-stream" in headers["Accept"]

    def test_authorization_header(self) -> None:
        session = _McpSession("https://example.com", "sk_test_xxx")
        headers = session._headers()
        assert headers["Authorization"] == "Bearer sk_test_xxx"

    def test_session_id_absent_before_init(self) -> None:
        session = _McpSession("https://example.com", "sk_test_xxx")
        headers = session._headers()
        assert "Mcp-Session-Id" not in headers

    def test_session_id_present_after_set(self) -> None:
        session = _McpSession("https://example.com", "sk_test_xxx")
        session.session_id = "test-session-123"
        headers = session._headers()
        assert headers["Mcp-Session-Id"] == "test-session-123"

    def test_protocol_version_header(self) -> None:
        session = _McpSession("https://example.com", "sk_test_xxx")
        headers = session._headers()
        assert headers["MCP-Protocol-Version"] == PROTOCOL_VERSION


# ---------------------------------------------------------------------------
# Key validation
# ---------------------------------------------------------------------------


class TestKeyValidation:
    def test_sk_test_accepted(self) -> None:
        _validate_key("sk_test_abc123")  # should not raise

    def test_rk_test_accepted(self) -> None:
        _validate_key("rk_test_abc123")  # should not raise

    def test_live_key_refused(self) -> None:
        with pytest.raises(SystemExit):
            _validate_key("sk_live_abc123")

    def test_rk_live_refused(self) -> None:
        with pytest.raises(SystemExit):
            _validate_key("rk_live_abc123")

    def test_arbitrary_string_refused(self) -> None:
        with pytest.raises(SystemExit):
            _validate_key("not_a_stripe_key")
