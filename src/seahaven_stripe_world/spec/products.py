"""The Stripe product table: path prefixes to activatable products.

Used by the refusal model (architecture section 4.4) to decide whether
a catalogued-but-unrouted operation answers B1 (product activation) or
B2 (permission).  B1 is the measured shape -- verbatim from the probe
(refusal-matrix.md section A) -- and should be preferred wherever it
applies.

Keyed on path prefix because ``spec3.json`` tags are empty for every
operation.  The path prefix is a reliable proxy: Issuing operations live
under ``/v1/issuing/``, Tax under ``/v1/tax/``, and so on.

The Sigma entry is not a path prefix but a product used by
``stripe_analytics`` (functional spec section 6).
"""

from typing import Final

__all__ = ["PRODUCTS", "product_for_path"]

#: ``(product_name, dashboard_url)`` keyed on path prefix.
#: Only products that an account explicitly activates belong here.
#: "Ordinary API surface" operations (Checkout, Payment Links, Connect
#: account reads) have no B1 form -- on real Stripe those just work --
#: and fall through to B2.
PRODUCTS: Final[dict[str, tuple[str, str]]] = {
    "/v1/issuing/": ("Issuing", "https://dashboard.stripe.com/issuing/overview"),
    "/v1/terminal/": ("Terminal", "https://dashboard.stripe.com/terminal"),
    "/v1/treasury/": ("Treasury", "https://dashboard.stripe.com/treasury"),
    "/v1/climate/": ("Climate", "https://dashboard.stripe.com/climate/overview"),
    "/v1/tax/": ("Tax", "https://dashboard.stripe.com/tax"),
    "/v1/financial_connections/": (
        "Financial Connections",
        "https://dashboard.stripe.com/financial-connections",
    ),
    "/v1/identity/": ("Identity", "https://dashboard.stripe.com/identity"),
    "/v1/radar/": ("Radar", "https://dashboard.stripe.com/radar"),
}

#: The Sigma product entry, used by ``stripe_analytics`` (functional spec
#: section 6).  Not a path prefix; exposed separately.
SIGMA_PRODUCT: Final[tuple[str, str]] = (
    "Sigma",
    "https://dashboard.stripe.com/sigma",
)


def product_for_path(path: str) -> tuple[str, str] | None:
    """The ``(product_name, dashboard_url)`` for a path, or ``None``.

    A ``None`` return means the operation is on ordinary API surface and
    the B2 (permission) refusal applies instead of B1 (product activation).
    """
    for prefix, product in PRODUCTS.items():
        if path.startswith(prefix):
            return product
    return None
