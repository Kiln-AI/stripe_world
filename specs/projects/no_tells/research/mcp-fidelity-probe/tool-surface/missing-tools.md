# Missing and Extra Tools

## Tools present in real Stripe MCP, absent from our world (6)

### 1. `list_available_accounts_or_orgs`

**No parameters.** Returns `{"accounts": [{"stripe_context": "acct_...", "livemode": false, "name": "..."}]}`.

Every other tool in the real MCP requires `stripe_context` and `livemode` as input — values
only available from this tool's output. An agent's first call is always this tool. Our world
has no equivalent bootstrapping step; our tools accept no context parameters.

**Description (verbatim):** "Lists all Stripe accounts in this session with their stripe_context and livemode values.\n- Call this first to get stripe_context and livemode before any account-specific operation.\n- After calling, ask the user which account to use unless already specified.\n- Warn the user before switching between testmode and livemode."

### 2. `manage_stripe_accounts`

**No parameters.** Returns `{"reconsent_url": "https://access.stripe.com/mcp/oauth2/authorize/sessions/oases_..."}`.

A link to the Stripe Dashboard where the user can modify account permissions. Our world
has no equivalent.

**Description (verbatim):** "Returns a URL to the Stripe Dashboard where users can add accounts, remove accounts, or change permissions for this session.\n- Use when the user wants to add, remove, or modify permissions for an account.\n- Call this directly — no need to call list_available_accounts_or_orgs first.\n- Present the URL to the user and wait for them to confirm they completed their changes.\n- After confirmation, call list_available_accounts_or_orgs to sync the updated account list."

### 3. `stripe_analytics`

**Complex multi-intent tool.** Required: `intent` (enum of 6 values), `stripe_context`, `livemode`. Optional: `params` (with sub-fields depending on intent).

Intents: `execute_query_run`, `retrieve_query_run`, `search_query_tables`, `retrieve_query_table`, `execute_query_template`, `retrieve_query_template`.

Provides SQL-based reporting against Stripe Sigma tables (Trino 414). Includes both custom SQL
and pre-built metric templates (gross volume, churn, MRR, subscribers).

**Description (verbatim, truncated):** "This tool is for analyzing Stripe Sigma and Metrics data (e.g. about revenue, charges, products, invoices, subscriptions, disputes, transactions, tax tables, payments etc) and running SQL-based reporting queries. Use it for historical analytics, aggregations, and business intelligence questions..."

**Absent from our world.** No analytics capability exists.

### 4. `search_stripe_documentation`

**Required:** `question` (string, max 1000 chars). **Optional:** `language` (enum: dotnet, go, java, node, php, ruby, python, curl), `search_only_api_ref` (boolean).

Returns `{"results": [...]}` where each result has `title`, `url`, `type` (either `"docs"` or `"knowledge_gem"`), `content`, and optionally `subtitle`.

**Description (verbatim):** "Search the Stripe documentation for the given question and language.\n\nIt takes two arguments:\n- question (str): The user question to search an answer for in the Stripe documentation.\n- language (str, optional): The programming language to search for in the documentation."

**Absent from our world.** No documentation search exists.

### 5. `stripe_implementation_planner`

**Required:** `stripe_context`, `livemode`. **Optional:** `message`, `guide_id`, `accept`, `selected_leaf_nodes`.

An interactive decision-tree tool that helps plan Stripe integrations. Returns structured
decision trees with questions, options, principles, and documentation links. Stateful across
calls via `guide_id`.

**Description (verbatim, truncated):** "Stripe payment integration planner. Use this tool to help users accept payments, sell products online, set up billing, or build any Stripe integration. Call this BEFORE writing code when the user wants to charge customers, add a checkout flow, handle subscriptions, create invoices, or monetize their app..."

**Absent from our world.** No planning capability exists.

### 6. `send_stripe_mcp_feedback`

**Required:** `sentiment` (enum: positive, negative, neutral), `quote` (string, 1-1000 chars), `context` (string), `source` (enum: user, agent). **Optional:** `tool_name`.

Submits feedback about the MCP tools themselves (not the Stripe API).

**Description (verbatim):** "Submit feedback from user or agent about Stripe's MCP server tools.\n\nValid: \"the search tool returned irrelevant results\", \"I wish there was a tool for X\"\nInvalid: Stripe API complaints, AI model issues, IDE/environment problems..."

**Absent from our world.** No feedback mechanism exists.

---

## Tool present in our world, absent from real Stripe MCP (1)

### `get_stripe_account_info`

**No parameters** (only `ctx`). Returns a static account object.

The real Stripe MCP has no tool with this name or function. The closest equivalent is
`list_available_accounts_or_orgs`, which returns `{stripe_context, livemode, name}` — far
less data than our full account object. The real MCP does not expose `GET /v1/account` as a
searchable operation either — searching for "retrieve account information" returns an empty
result set. `GetAccountsAccount` at `/v1/accounts/{id}` exists but is for connected accounts.

An agent familiar with the real MCP will never call `get_stripe_account_info`. An agent
trained on our world will call it and get a tool-not-found error on the real MCP.
