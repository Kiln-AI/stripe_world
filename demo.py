"""Stripe World in one command: `uv run demo.py`.

Charges $20 to Stripe's test Visa card in a private test-mode Stripe account
that lives in this process, reads the charge back, then prints the rows the
charge wrote: the state an eval grades. No API key and no network.

The requests go to the handler `serve_http.py` serves to Stripe's SDKs, called
the way that server calls it: form-encoded, inside `inst.bulk()`. A card charge
is a confirmed PaymentIntent, as on the real API, which refuses card charges
made with `POST /v1/charges`.
"""

import json
from typing import Any
from urllib.parse import urlencode

import seahaven
from seahaven.http import HttpRequest

from seahaven_stripe_world import world
from seahaven_stripe_world.http_api import handle

HEADERS = (
    ("Authorization", "Bearer sk_test_demo"),  # any key is accepted
    ("Content-Type", "application/x-www-form-urlencoded"),
)


def stripe(
    inst: seahaven.Instance, method: str, path: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    """One request to Stripe's HTTP API, printed as a request line and its answer."""
    params = params or {}
    print(f"{method} {path}", *(f"{name}={value}" for name, value in params.items()))
    request = HttpRequest(method, path, headers=HEADERS, body=urlencode(params).encode())
    with inst.bulk() as ctx:
        response = handle(ctx, request)
    body = json.loads(response.body_bytes)
    print(f"  -> {response.status} {body.get('id', body)}\n")
    return body


def dollars(cents: int) -> str:
    return f"${cents // 100}.{cents % 100:02d}"


def main() -> None:
    # A blank test-mode account. The seed and the fixed clock pin every id and
    # timestamp, so every run prints the same thing.
    with world.instance(
        seed=7, now="2026-10-01T09:00:00.000Z", clock_mode="fixed", startup={"livemode": False}
    ) as inst:
        card = stripe(
            inst, "POST", "/v1/payment_methods", {"type": "card", "card[token]": "tok_visa"}
        )
        payment = stripe(
            inst,
            "POST",
            "/v1/payment_intents",
            {"amount": 2000, "currency": "usd", "payment_method": card["id"], "confirm": "true"},
        )
        charge = stripe(inst, "GET", f"/v1/charges/{payment['latest_charge']}")
        details = charge["payment_method_details"]["card"]
        print(
            f"Charged {dollars(charge['amount'])} {charge['currency'].upper()} to "
            f"{details['brand']} ending {details['last4']}: {charge['status']}\n"
        )

        log = inst.state()["state"]["db"]["log"]
        print(f"State: the {len(log)} rows those requests wrote")
        for record in log:
            row = record["after"]
            label = row.get("type") or row.get("status") or ""
            print(f"  {record['op']:<7} {record['table']:<21} {row['id']:<31} {label}")

        print("\nThe charge, as the state document records it:")
        print(json.dumps(next(r for r in log if r["table"] == "charges"), indent=2))


if __name__ == "__main__":
    main()
