"""The recorder's key gate, isolated from `stripe` so its tests (and CI)
need nothing recording-related installed.

Test-mode only, no escape hatch: a key that is missing, or not
`sk_test_`/`rk_test_`-prefixed, refuses before any request is attempted.
"""

import os
from collections.abc import Mapping
from pathlib import Path

__all__ = ["RecordRefused", "resolve_api_key"]


class RecordRefused(Exception):
    """No usable test-mode key — nothing was sent, nothing was written."""


def resolve_api_key(
    environ: Mapping[str, str] | None = None,
    env_file: Path | None = None,
) -> str:
    """`STRIPE_CONFORMANCE_TEST_KEY` from the environment, falling back to a
    `.env` file's `STRIPE_SECRET_KEY` (the repo's expected spelling). Raises
    `RecordRefused` when nothing usable is found."""
    environ = os.environ if environ is None else environ
    key = environ.get("STRIPE_CONFORMANCE_TEST_KEY")
    if not key and env_file is not None and env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("STRIPE_SECRET_KEY="):
                key = line.split("=", 1)[1].strip().strip("'\"") or None
                break
    if not key:
        raise RecordRefused(
            "no API key: set STRIPE_CONFORMANCE_TEST_KEY (or put a test-mode "
            "STRIPE_SECRET_KEY in .env)"
        )
    if not key.startswith(("sk_test_", "rk_test_")):
        raise RecordRefused("refusing a non-test-mode key: recording uses sk_test_/rk_test_ only")
    return key
