"""Stripe object search: the ``/v1/*/search`` endpoints.

Seven resources support search with Stripe's query language. The query parser,
per-resource field allowlists, and the search-result pagination model (``page``
/ ``next_page``, distinct from the cursor model used by list endpoints) all
live here.
"""
