"""Verbatim tool descriptions from the real Stripe MCP server.

Each constant is copied exactly from the captured schemas in
``tests/surface/real_tool_schemas.json`` (probed 2026-09-22).  A test
asserts byte-identity so an accidental edit fails rather than silently
reopening a blatant tell (architecture section 2.3).

``@world.tool(description=...)`` uses these; docstrings then describe
the *implementation* for a human reader.
"""

from typing import Final

__all__ = [
    "GET_STRIPE_ACCOUNT_INFO",
    "LIST_AVAILABLE_ACCOUNTS_OR_ORGS",
    "MANAGE_STRIPE_ACCOUNTS",
    "STRIPE_API_DETAILS",
    "STRIPE_API_READ",
    "STRIPE_API_SEARCH",
    "STRIPE_API_WRITE",
]

STRIPE_API_READ: Final = (
    "Read data from any Stripe API GET operation:\n"
    "1. Use stripe_api_search to find the operation ID.\n"
    "2. Use stripe_api_details to understand its parameters "
    "(required for operations with nested object fields like address, metadata, or restrictions).\n"
    "3. Call this tool with the stripe_api_operation_id and a parameters object containing "
    "path and query parameters. For mutations (POST/PATCH/PUT/DELETE), "
    "use stripe_api_write instead.\n"
    "Monetary values in responses are in the smallest currency unit "
    "(e.g. 1000 = $10.00 USD for most currencies)."
)

STRIPE_API_WRITE: Final = (
    "Write data using any Stripe API POST, PATCH, PUT, or DELETE operation:\n"
    "1. Use stripe_api_search to find the operation ID.\n"
    "2. Use stripe_api_details to understand its parameters "
    "(required for operations with nested object fields like address, metadata, or restrictions).\n"
    "3. Call this tool with the stripe_api_operation_id and a parameters object containing "
    "path, query, and body parameters. For read operations (GET), use stripe_api_read instead.\n"
    "Monetary values in parameters should be in the smallest currency unit "
    "(e.g. 1000 = $10.00 USD for most currencies)."
)

STRIPE_API_SEARCH: Final = (
    "Search for Stripe API operations by providing an intent and a resource to operate on.\n"
    "\n"
    "For the resource, use a specific, descriptive phrase "
    '(e.g. "issuing card transactions", "payout methods", "outbound payments") '
    "rather than a single generic noun — Stripe has many similar resources, "
    "and the extra qualifiers help disambiguate between them rather than causing a miss. "
    'Only fall back to the bare core noun (e.g. "transactions") '
    "if a specific multi-word search returns no results.\n"
    "\n"
    "Returns matching operations with their HTTP method, path, summary, "
    "and top-level parameter names with types. Does not include parameter descriptions, "
    "enum values, or nested object fields — use stripe_api_details to get those "
    "before calling the relevant Stripe API execution tool."
)

STRIPE_API_DETAILS: Final = (
    "Get detailed parameter information for a specific Stripe API operation.\n"
    "Provide the stripe_api_operation_id from stripe_api_search results to see all "
    "path, query, and body parameters with their types, descriptions, "
    "and whether they are required.\n"
    "\n"
    "Use before calling the relevant Stripe API execution tool for operations "
    "with nested object fields.\n"
    "\n"
    'The response may include an "LLM Context" section with usage guidance '
    "specific to the operation — follow any instructions there."
)

LIST_AVAILABLE_ACCOUNTS_OR_ORGS: Final = (
    "Lists all Stripe accounts in this session with their stripe_context "
    "and livemode values.\n"
    "- Call this first to get stripe_context and livemode before any "
    "account-specific operation.\n"
    "- After calling, ask the user which account to use unless already specified.\n"
    "- Warn the user before switching between testmode and livemode."
)

MANAGE_STRIPE_ACCOUNTS: Final = (
    "Returns a URL to the Stripe Dashboard where users can add accounts, "
    "remove accounts, or change permissions for this session.\n"
    "- Use when the user wants to add, remove, or modify permissions for "
    "an account.\n"
    "- Call this directly — no need to call list_available_accounts_or_orgs "
    "first.\n"
    "- Present the URL to the user and wait for them to confirm they "
    "completed their changes.\n"
    "- After confirmation, call list_available_accounts_or_orgs to sync "
    "the updated account list."
)

# get_stripe_account_info is not on the live Stripe MCP server (functional spec
# section 4.1.1). Its description is this world's own, not a captured verbatim.
GET_STRIPE_ACCOUNT_INFO: Final = (
    "Retrieve the Stripe account object for this session's account. "
    "Returns billing-relevant fields: business profile, capabilities, "
    "default currency, and payout settings."
)
