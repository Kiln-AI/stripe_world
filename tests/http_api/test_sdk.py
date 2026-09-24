"""The official Stripe SDK, pointed at a local server: the point of the HTTP API.

Offline: stripe-python talks only to the uvicorn server on 127.0.0.1.
"""

import pytest

stripe = pytest.importorskip("stripe")


def client(server_url: str, id: str = "sdk") -> stripe.StripeClient:
    return stripe.StripeClient(
        "sk_test_anything", base_addresses={"api": f"{server_url}/worlds/{id}"}
    )


def test_stripe_python_drives_a_billing_flow(server_url: str) -> None:
    sdk = client(server_url).v1
    customer = sdk.customers.create(
        params={"email": "sdk@example.test", "metadata": {"source": "sdk"}}
    )
    assert customer.id.startswith("cus_")
    assert customer.metadata["source"] == "sdk"

    product = sdk.products.create(params={"name": "Seat"})
    price = sdk.prices.create(
        params={
            "product": product.id,
            "unit_amount": 1500,
            "currency": "usd",
            "recurring": {"interval": "month"},
        }
    )
    card = sdk.payment_methods.create(params={"type": "card", "card": {"token": "tok_visa"}})
    sdk.payment_methods.attach(card.id, params={"customer": customer.id})
    subscription = sdk.subscriptions.create(
        params={
            "customer": customer.id,
            "items": [{"price": price.id, "quantity": 2}],
            "default_payment_method": card.id,
            "expand": ["latest_invoice"],
        }
    )
    assert subscription.status == "active"
    assert subscription.items.data[0].quantity == 2
    assert subscription.latest_invoice.amount_paid == 3000

    listed = sdk.customers.list(params={"limit": 1, "email": "sdk@example.test"})
    assert [each.id for each in listed.data] == [customer.id]
    cleared = sdk.customers.update(customer.id, params={"metadata": {"source": ""}})
    assert cleared.metadata.to_dict() == {}
    assert sdk.customers.delete(customer.id).deleted is True


def test_stripe_python_raises_stripes_errors(server_url: str) -> None:
    sdk = client(server_url).v1
    with pytest.raises(stripe.InvalidRequestError) as missing:
        sdk.customers.retrieve("cus_nope")
    assert missing.value.http_status == 404
    assert missing.value.code == "resource_missing"

    customer = sdk.customers.create(params={"email": "pay@example.test"})
    declined = sdk.payment_methods.create(
        params={
            "type": "card",
            "card": {"number": "4000000000000341", "exp_month": 9, "exp_year": 2027},
        }
    )
    with pytest.raises(stripe.CardError) as card_error:
        sdk.payment_intents.create(
            params={
                "amount": 1000,
                "currency": "usd",
                "customer": customer.id,
                "payment_method": declined.id,
                "confirm": True,
            }
        )
    assert card_error.value.http_status == 402
    assert card_error.value.code == "card_declined"
