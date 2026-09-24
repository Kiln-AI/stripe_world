"""`serve_http.py`'s command line, which is `seahaven.http.main`'s.

The in-process cases run the script with `seahaven.http.server.serve` replaced
by a recorder, so they check what reaches the server without binding a port.
One case runs the script as a process and makes a request to it.
"""

import json
import queue
import re
import runpy
import subprocess
import sys
import threading
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from seahaven_stripe_world import world
from seahaven_stripe_world.http_api import handle

server = pytest.importorskip("seahaven.http.server", exc_type=ImportError)

SCRIPT = Path(__file__).parents[2] / "serve_http.py"
VARIABLES = (
    "SEAHAVEN_FIXTURE",
    "SEAHAVEN_SEED",
    "SEAHAVEN_NOW",
    "SEAHAVEN_CLOCK_MODE",
    "SEAHAVEN_RESET_OPTIONS",
)
TEST_MODE = '{"startup": {"livemode": false}}'
# A bound on a hang rather than a measurement.
PROCESS_WAIT = 60.0
RUNNING = re.compile(r"Uvicorn running on http://127\.0\.0\.1:(\d+)")


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in VARIABLES:
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """What each run of the script handed the server, keyword arguments by name."""
    calls: list[dict[str, Any]] = []

    def record(given_world: Any, given_handler: Any, **kwargs: Any) -> None:
        assert (given_world, given_handler) == (world, handle)
        calls.append(kwargs)

    monkeypatch.setattr(server, "serve", record)
    return calls


def run_script(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), *argv])
    runpy.run_path(str(SCRIPT), run_name="__main__")


def test_no_options_serve_a_blank_live_mode_instance_on_the_default_address(
    monkeypatch: pytest.MonkeyPatch, served: list[dict[str, Any]]
) -> None:
    run_script(monkeypatch)
    assert served == [
        {"host": "127.0.0.1", "port": 8000, "reset_options": {}, "max_instances": 100}
    ]


def test_the_address_and_the_instance_limit_are_options(
    monkeypatch: pytest.MonkeyPatch, served: list[dict[str, Any]]
) -> None:
    run_script(monkeypatch, "--host", "0.0.0.0", "--port", "9123", "--max-instances", "0")
    assert served == [{"host": "0.0.0.0", "port": 9123, "reset_options": {}, "max_instances": 0}]


def test_the_convenience_flags_are_reset_options(
    monkeypatch: pytest.MonkeyPatch, served: list[dict[str, Any]]
) -> None:
    run_script(
        monkeypatch,
        "--fixture",
        "empty",
        "--seed",
        "7",
        "--now",
        "2026-09-02T00:00:00.000Z",
        "--clock-mode",
        "tick",
    )
    assert served[0]["reset_options"] == {
        "fixture": "empty",
        "seed": 7,
        "now": "2026-09-02T00:00:00.000Z",
        "clock_mode": "tick",
    }


def test_test_mode_is_a_reset_option(
    monkeypatch: pytest.MonkeyPatch, served: list[dict[str, Any]]
) -> None:
    run_script(monkeypatch, "--reset-options", TEST_MODE)
    assert served[0]["reset_options"] == {"startup": {"livemode": False}}


def test_the_environment_variables_are_reset_options(
    monkeypatch: pytest.MonkeyPatch, served: list[dict[str, Any]]
) -> None:
    monkeypatch.setenv("SEAHAVEN_FIXTURE", "empty")
    monkeypatch.setenv("SEAHAVEN_SEED", "11")
    run_script(monkeypatch)
    monkeypatch.delenv("SEAHAVEN_FIXTURE")
    monkeypatch.delenv("SEAHAVEN_SEED")
    monkeypatch.setenv("SEAHAVEN_RESET_OPTIONS", TEST_MODE)
    run_script(monkeypatch)
    assert [call["reset_options"] for call in served] == [
        {"fixture": "empty", "seed": 11},
        {"startup": {"livemode": False}},
    ]


@pytest.mark.parametrize(
    ("argv", "refusal"),
    [
        (["--fixture", "empty", "--reset-options", TEST_MODE], "cannot be combined with"),
        (["--seed", "seven"], "seven"),
        (["--reset-options", '{"control_tools": true}'], "control_tools"),
        (["--max-instances", "-1"], "--max-instances"),
    ],
)
def test_a_refused_option_is_one_line_and_exit_1(
    monkeypatch: pytest.MonkeyPatch,
    served: list[dict[str, Any]],
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    refusal: str,
) -> None:
    with pytest.raises(SystemExit) as exited:
        run_script(monkeypatch, *argv)
    assert exited.value.code == 1
    error = capsys.readouterr().err
    assert refusal in error
    assert error.count("\n") == 1
    assert served == []


# --- the script, as a process ------------------------------------------------


@pytest.fixture
def script() -> Iterator[tuple[subprocess.Popen[str], queue.Queue[str]]]:
    """`python serve_http.py --port 0 --reset-options <test mode>`, and its stderr lines."""
    process = subprocess.Popen(
        [sys.executable, str(SCRIPT), "--port", "0", "--reset-options", TEST_MODE],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stderr is not None
    errors: queue.Queue[str] = queue.Queue()
    stream = process.stderr

    def drain() -> None:
        for line in stream:
            errors.put(line)

    drainer = threading.Thread(target=drain, daemon=True)
    drainer.start()
    try:
        yield process, errors
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(PROCESS_WAIT)
        drainer.join(PROCESS_WAIT)
        stream.close()
        if process.stdout is not None:
            process.stdout.close()


def test_the_script_serves_stripes_api_over_http(
    script: tuple[subprocess.Popen[str], queue.Queue[str]],
) -> None:
    process, errors = script
    assert process.stdout is not None
    assert process.stdout.readline().startswith("Serving seahaven_stripe_world at http://")
    port = _port(errors)
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/worlds/proc/v1/customers",
        data=b"email=proc%40example.test",
        headers={"Authorization": "Bearer sk_test_anything"},
        method="POST",
    )
    # No proxy: a proxy in the environment would not reach this machine's loopback.
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with direct.open(request, timeout=PROCESS_WAIT) as response:
        customer = json.load(response)
    assert customer["email"] == "proc@example.test"
    assert customer["livemode"] is False


def _port(errors: queue.Queue[str]) -> int:
    """The port uvicorn bound, from its startup line on stderr."""
    seen: list[str] = []
    while True:
        try:
            line = errors.get(timeout=PROCESS_WAIT)
        except queue.Empty:
            raise AssertionError(f"the server did not start: {''.join(seen)}") from None
        seen.append(line)
        if match := RUNNING.search(line):
            return int(match.group(1))
