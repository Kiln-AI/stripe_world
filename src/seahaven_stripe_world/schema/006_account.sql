-- The instance's Stripe account: one row (company_fixtures functional spec §2).
--
-- `livemode` is NULL until the account is created: a blank instance's startup hook
-- stamps the mode it was opened in, and freezing keeps it. A fixture's data (its
-- cards, and the livemode inside stored JSON) was made in that mode, so an instance
-- of a fixture opens in the row's mode and refuses the other (`startup.open_account`).
--
-- `object` is the account object as `get_stripe_account_info` returns it, a wire
-- snapshot like events.data (Unix-second `created`). `settings` holds the `ledger`
-- and `dunning` overrides the world already reads from ctx.state.
--
-- No `x_seq` and no counter: the table is not listable, and no route reads or writes
-- it. It is tracked, but nothing in a session writes it, so it never appears in a
-- change log; `setup_sql` can still change it for one episode.
CREATE TABLE account (
    id       TEXT    PRIMARY KEY,
    livemode INTEGER CHECK (livemode IS NULL OR livemode IN (0, 1)),
    object   TEXT    NOT NULL CHECK (json_valid(object) AND json_type(object) = 'object'),
    settings TEXT    NOT NULL DEFAULT '{}'
                     CHECK (json_valid(settings) AND json_type(settings) = 'object')
) STRICT;

-- The seeded account is the fresh sandbox this world has always served: charges and
-- payouts disabled, empty capabilities, business_profile fields null, no metadata key
-- (stripe_world functional spec §10.2; the shape `GET /v1/account` returned on a fresh
-- sandbox, probed 2026-09-22). It has no mode yet, so a blank instance opens in either.
--
-- The literal is indented for review; `json()` minifies it to exactly the bytes
-- `_json.dumps` writes (its keys are already sorted). `tests/golden/sandbox_account.json`
-- holds those bytes, and `test_seed_is_todays_sandbox_account` compares them.
INSERT INTO account (id, livemode, object, settings)
VALUES ('acct_0NGvb3Cz7hWRKLEp', NULL, json('
    {
        "business_profile": {
            "annual_revenue": null,
            "estimated_worker_count": null,
            "mcc": null,
            "name": null,
            "support_address": null,
            "support_email": null,
            "support_phone": null,
            "support_url": null,
            "url": null
        },
        "business_type": null,
        "capabilities": {},
        "charges_enabled": false,
        "controller": {
            "is_controller": true,
            "type": "account"
        },
        "country": "US",
        "created": 1704067200,
        "default_currency": "usd",
        "details_submitted": false,
        "email": null,
        "external_accounts": {
            "data": [],
            "has_more": false,
            "object": "list",
            "url": "/v1/accounts/acct_0NGvb3Cz7hWRKLEp/external_accounts"
        },
        "future_requirements": {
            "alternatives": [],
            "current_deadline": null,
            "currently_due": [],
            "disabled_reason": null,
            "errors": [],
            "eventually_due": [],
            "past_due": [],
            "pending_verification": []
        },
        "id": "acct_0NGvb3Cz7hWRKLEp",
        "object": "account",
        "payouts_enabled": false,
        "requirements": {
            "alternatives": [],
            "current_deadline": null,
            "currently_due": [],
            "disabled_reason": "requirements.past_due",
            "errors": [],
            "eventually_due": [],
            "past_due": [],
            "pending_verification": []
        },
        "settings": {
            "bacs_debit_payments": {
                "display_name": null,
                "service_user_number": null
            },
            "branding": {
                "icon": null,
                "logo": null,
                "primary_color": null,
                "secondary_color": null
            },
            "card_issuing": {
                "tos_acceptance": {
                    "date": null,
                    "ip": null
                }
            },
            "card_payments": {
                "decline_on": {
                    "avs_failure": false,
                    "cvc_failure": false
                },
                "statement_descriptor_prefix": null,
                "statement_descriptor_prefix_kana": null,
                "statement_descriptor_prefix_kanji": null
            },
            "dashboard": {
                "display_name": null,
                "timezone": "Etc/UTC"
            },
            "invoices": {
                "default_account_tax_ids": null
            },
            "payments": {
                "statement_descriptor": null,
                "statement_descriptor_kana": null,
                "statement_descriptor_kanji": null
            },
            "payouts": {
                "debit_negative_balances": true,
                "schedule": {
                    "delay_days": 7,
                    "interval": "daily"
                },
                "statement_descriptor": null
            },
            "sepa_debit_payments": {},
            "treasury": {
                "tos_acceptance": {
                    "date": null,
                    "ip": null
                }
            }
        },
        "tos_acceptance": {
            "date": null,
            "ip": null,
            "user_agent": null
        },
        "type": "standard"
    }
'), '{}');
