"""`_json`: the single dump convention for every JSON TEXT column."""

from stripeapi._json import dumps, loads


def test_dumps_sorts_keys_and_compacts() -> None:
    assert dumps({"b": 2, "a": {"d": 4, "c": 3}}) == '{"a":{"c":3,"d":4},"b":2}'


def test_dumps_passes_non_ascii_through_rather_than_escaping() -> None:
    assert dumps({"name": "Ünal"}) == '{"name":"Ünal"}'


def test_dumps_is_byte_identical_across_runs() -> None:
    value = {"metadata": {"order": "6735"}, "items": [{"price": "price_123"}]}
    assert dumps(value) == dumps(dict(reversed(list(value.items()))))


def test_loads_preserves_none() -> None:
    assert loads(None) is None
    assert loads('{"a":1}') == {"a": 1}
    assert loads("[]") == []
