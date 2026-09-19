"""Committed-cassette hygiene: no key, account id or real email reaches
git, and no stored step ever carried the volatile headers.

No network, no fixtures — this is the test that runs on every PR, including
one that only touches source, in case a rebase or a manual edit reintroduced
something the recorder's own gate would have blocked."""

from pathlib import Path

from conformance.redact import header_names_never_stored, scan_text

CASSETTES = Path(__file__).parent / "cassettes"


def test_no_secret_reaches_committed_cassettes() -> None:
    files = sorted(CASSETTES.glob("*.json"))
    assert files, "the conformance cassettes directory must exist and hold cassettes"
    for path in files:
        hits = scan_text(path.read_text())
        assert not hits, f"{path.name}: {hits}"


def test_request_id_and_idempotency_key_headers_are_never_stored() -> None:
    """Headers are dropped at capture, never stored-then-ignored. The
    snake_case `idempotency_key` request field is legitimate cassette content
    and does not match the header spelling."""
    for path in sorted(CASSETTES.glob("*.json")):
        hits = header_names_never_stored(path.read_text())
        assert not hits, f"{path.name}: {hits}"
