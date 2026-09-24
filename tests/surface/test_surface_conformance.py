"""Surface conformance: the registered tool list, each tool's JSON schema and
each description compared against the captured real ones (architecture section 8.1).

The cheapest test in the project and the one covering the largest cluster of
blatant tells. Failure prints a diff so the gap is visible at a glance.

Each tool's schema and description are compared against the captured real server's.
"""

import json
from pathlib import Path
from typing import Any

_DATA = Path(__file__).parent / "real_tool_schemas.json"

# The eight tools this world registers (functional spec section 4.1).
_REGISTERED_TOOLS = (
    "stripe_api_read",
    "stripe_api_write",
    "stripe_api_search",
    "stripe_api_details",
    "list_available_accounts_or_orgs",
    "manage_stripe_accounts",
    "stripe_analytics",
    "get_stripe_account_info",
)

# The three tools deliberately not built (functional spec section 4.1.2).
_NOT_BUILT = (
    "search_stripe_documentation",
    "stripe_implementation_planner",
    "send_stripe_mcp_feedback",
)


def _load_real_schemas() -> dict[str, Any]:
    return json.loads(_DATA.read_text())


def _world_tools() -> dict[str, Any]:
    from seahaven_stripe_world.world import world

    return dict(world.tools)


def _compare_schema(tool_name: str, real: dict[str, Any], actual_schema: dict[str, Any]) -> None:
    """Assert the actual tool schema matches the real captured schema."""
    real_params = real["parameters"]
    real_props = real_params["properties"]
    real_required = set(real_params["required"])

    actual_props = actual_schema.get("properties", {})
    # Filter out ctx which is internal
    actual_required = {r for r in actual_schema.get("required", []) if r != "ctx"}

    # Property key set must match
    assert set(actual_props.keys()) == set(real_props.keys()), (
        f"{tool_name}: property keys differ.\n"
        f"  Expected: {sorted(real_props.keys())}\n"
        f"  Got:      {sorted(actual_props.keys())}"
    )

    # Required list must match
    assert actual_required == real_required, (
        f"{tool_name}: required differs.\n"
        f"  Expected: {sorted(real_required)}\n"
        f"  Got:      {sorted(actual_required)}"
    )

    # Per-property checks
    for prop_name, real_prop in real_props.items():
        actual_prop = actual_props[prop_name]
        # Type
        if "type" in real_prop:
            actual_type = actual_prop.get("type")
            # Handle anyOf union types
            if actual_type is None and "anyOf" in actual_prop:
                types = [m.get("type") for m in actual_prop["anyOf"] if "type" in m]
                if len(types) == 1:
                    actual_type = types[0]
            assert actual_type == real_prop["type"], (
                f"{tool_name}.{prop_name}: type differs. "
                f"Expected {real_prop['type']!r}, got {actual_type!r}"
            )
        # Default — must match in both directions: present if real has it,
        # absent if real does not (a spurious default is itself a tell).
        if "default" in real_prop:
            assert "default" in actual_prop, (
                f"{tool_name}.{prop_name}: missing default {real_prop['default']!r}"
            )
            assert actual_prop["default"] == real_prop["default"], (
                f"{tool_name}.{prop_name}: default differs. "
                f"Expected {real_prop['default']!r}, got {actual_prop['default']!r}"
            )
        else:
            assert "default" not in actual_prop, (
                f"{tool_name}.{prop_name}: spurious default "
                f"{actual_prop['default']!r} — real schema has none"
            )
        # Minimum / maximum
        if "minimum" in real_prop:
            assert actual_prop.get("minimum") == real_prop["minimum"], (
                f"{tool_name}.{prop_name}: minimum differs"
            )
        if "maximum" in real_prop:
            assert actual_prop.get("maximum") == real_prop["maximum"], (
                f"{tool_name}.{prop_name}: maximum differs"
            )
        # Examples
        if "examples" in real_prop:
            assert actual_prop.get("examples") == real_prop["examples"], (
                f"{tool_name}.{prop_name}: examples differ"
            )
        # Nested properties (e.g. human_confirmation.approval_token)
        if "properties" in real_prop:
            actual_nested = actual_prop.get("properties", {})
            assert set(actual_nested.keys()) == set(real_prop["properties"].keys()), (
                f"{tool_name}.{prop_name}: nested property keys differ.\n"
                f"  Expected: {sorted(real_prop['properties'].keys())}\n"
                f"  Got:      {sorted(actual_nested.keys())}"
            )
            for nested_name, nested_real in real_prop["properties"].items():
                nested_actual = actual_nested[nested_name]
                if "type" in nested_real:
                    assert nested_actual.get("type") == nested_real["type"], (
                        f"{tool_name}.{prop_name}.{nested_name}: type differs. "
                        f"Expected {nested_real['type']!r}, got {nested_actual.get('type')!r}"
                    )


# --- Data file sanity ---------------------------------------------------------


def test_real_tool_schemas_present() -> None:
    """The captured real schemas file exists and has 10 entries for the 10
    real Stripe MCP tools plus get_stripe_account_info."""
    schemas = _load_real_schemas()
    assert len(schemas) == 10 + 1, f"expected 11 entries, got {len(schemas)}"
    # All real tools present
    for name in (
        "stripe_api_read",
        "stripe_api_write",
        "stripe_api_search",
        "stripe_api_details",
        "list_available_accounts_or_orgs",
        "manage_stripe_accounts",
        "stripe_analytics",
        "search_stripe_documentation",
        "stripe_implementation_planner",
        "send_stripe_mcp_feedback",
        "get_stripe_account_info",
    ):
        assert name in schemas, f"missing {name}"


def test_full_spec_committed() -> None:
    """spec3.json exists in the package and its sha256 matches the MANIFEST."""
    spec_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "seahaven_stripe_world"
        / "spec"
        / "spec3.json"
    )
    assert spec_path.is_file(), f"{spec_path} does not exist"
    import hashlib

    sha = hashlib.sha256(spec_path.read_bytes()).hexdigest()
    expected = "f0e0fc8fffbffda45bf5f3df59846443c1d47a3cfcbfae232eedf4743124ebee"
    assert sha == expected, f"spec3.json sha256 {sha} != expected {expected}"


# --- Registered tool set guard ------------------------------------------------

# The full set of tools this world registers (functional spec section 4.1).
_EXPECTED_NOW = {
    "stripe_api_read",
    "stripe_api_write",
    "stripe_api_search",
    "stripe_api_details",
    "stripe_analytics",
    "get_stripe_account_info",
    "list_available_accounts_or_orgs",
    "manage_stripe_accounts",
}


def test_registered_tool_set_matches_expected() -> None:
    """No unintended tool registration (e.g. ``call_stripe``, which is
    deliberately unregistered per functional spec section 2.5)."""
    tools = _world_tools()
    # controller_run_sql is a framework tool, not ours.
    ours = {n for n in tools if n != "controller_run_sql"}
    assert ours == _EXPECTED_NOW


# --- Per-tool surface conformance (xfailed until the tools are rebuilt) --------


def test_ts_04_read_takes_operation_id() -> None:
    """TS-04: stripe_api_read uses stripe_api_operation_id, not path."""
    tools = _world_tools()
    assert "stripe_api_read" in tools
    real = _load_real_schemas()["stripe_api_read"]
    tool = tools["stripe_api_read"]
    assert tool.description == real["description"]
    _compare_schema("stripe_api_read", real, tool.schema)


def test_ts_05_write_takes_operation_id() -> None:
    """TS-05: stripe_api_write uses stripe_api_operation_id, not method+path."""
    tools = _world_tools()
    assert "stripe_api_write" in tools
    real = _load_real_schemas()["stripe_api_write"]
    tool = tools["stripe_api_write"]
    assert tool.description == real["description"]
    _compare_schema("stripe_api_write", real, tool.schema)


def test_ts_06_search_takes_intent_resource() -> None:
    """TS-06/DT-01: stripe_api_search takes intent+resource+limit, not query."""
    tools = _world_tools()
    assert "stripe_api_search" in tools
    real = _load_real_schemas()["stripe_api_search"]
    tool = tools["stripe_api_search"]
    assert tool.description == real["description"]
    _compare_schema("stripe_api_search", real, tool.schema)


def test_ts_07_details_takes_operation_id() -> None:
    """TS-07/DT-13: stripe_api_details takes stripe_api_operation_id."""
    tools = _world_tools()
    assert "stripe_api_details" in tools
    real = _load_real_schemas()["stripe_api_details"]
    tool = tools["stripe_api_details"]
    assert tool.description == real["description"]
    _compare_schema("stripe_api_details", real, tool.schema)


def test_ts_03_list_accounts_registered() -> None:
    """TS-03: list_available_accounts_or_orgs is registered."""
    tools = _world_tools()
    assert "list_available_accounts_or_orgs" in tools
    real = _load_real_schemas()["list_available_accounts_or_orgs"]
    tool = tools["list_available_accounts_or_orgs"]
    assert tool.description == real["description"]
    _compare_schema("list_available_accounts_or_orgs", real, tool.schema)


def test_ts_19_manage_accounts_registered() -> None:
    """TS-19: manage_stripe_accounts is registered."""
    tools = _world_tools()
    assert "manage_stripe_accounts" in tools
    real = _load_real_schemas()["manage_stripe_accounts"]
    tool = tools["manage_stripe_accounts"]
    assert tool.description == real["description"]
    _compare_schema("manage_stripe_accounts", real, tool.schema)


def test_ts_21_analytics_registered() -> None:
    """TS-21: stripe_analytics is registered."""
    tools = _world_tools()
    assert "stripe_analytics" in tools
    real = _load_real_schemas()["stripe_analytics"]
    tool = tools["stripe_analytics"]
    assert tool.description == real["description"]
    _compare_schema("stripe_analytics", real, tool.schema)


def test_ts_08_read_description_matches() -> None:
    """TS-08: stripe_api_read description matches the real server's."""
    tools = _world_tools()
    real = _load_real_schemas()["stripe_api_read"]
    assert tools["stripe_api_read"].description == real["description"]


def test_ts_24_write_description_matches() -> None:
    """TS-24: stripe_api_write description matches the real server's."""
    tools = _world_tools()
    real = _load_real_schemas()["stripe_api_write"]
    assert tools["stripe_api_write"].description == real["description"]


def test_ts_09_search_description_matches() -> None:
    """TS-09: stripe_api_search description matches the real server's."""
    tools = _world_tools()
    real = _load_real_schemas()["stripe_api_search"]
    assert tools["stripe_api_search"].description == real["description"]


def test_ts_25_details_description_matches() -> None:
    """TS-25: stripe_api_details description matches the real server's."""
    tools = _world_tools()
    real = _load_real_schemas()["stripe_api_details"]
    assert tools["stripe_api_details"].description == real["description"]


# --- Tools deliberately not built (functional spec section 4.1.2) -------------


def test_not_built_tools_absent() -> None:
    """The three tools deliberately not built are not registered."""
    tools = _world_tools()
    for name in _NOT_BUILT:
        assert name not in tools, f"{name} should not be registered"
