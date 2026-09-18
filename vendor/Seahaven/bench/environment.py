"""What the numbers were produced on, captured with them.

A benchmark figure without its machine is a rumour. Everything here is read at
run time and printed at the top of the report, so that a table can be compared
with another table only when the two of them agree about the interpreter, the
libraries, the commit and the hardware -- and so that a reader who recognises the
hardware as a shared virtual machine can discount the figures accordingly.

Nothing here fails a run. A machine that will not say how much memory it has, or
a tree that is not a git checkout, produces the string "unknown" and the
measurement goes ahead.
"""

import os
import platform
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from bench.harness import cold_cache_supported
from seahaven.instances import default_concurrency

__all__ = ["Environment", "capture"]

UNKNOWN = "unknown"


@dataclass(frozen=True)
class Environment:
    """One run's machine, interpreter and libraries, as labelled rows."""

    when: str
    commit: str
    python: str
    interpreter_path: str
    platform: str
    cpu_model: str
    cpus: str
    default_gate: int
    load_average: str
    memory: str
    work_filesystem: str
    packages: tuple[tuple[str, str], ...]
    page_cache_eviction: str

    def rows(self) -> list[tuple[str, str]]:
        """Label and value, in the order the report prints them."""
        return [
            ("Run at (UTC)", self.when),
            ("Commit", self.commit),
            ("Python", self.python),
            ("Interpreter", self.interpreter_path),
            ("Platform", self.platform),
            ("CPU", self.cpu_model),
            ("CPUs", self.cpus),
            ("Gate default here", str(self.default_gate)),
            ("Load average at start", self.load_average),
            ("Memory", self.memory),
            ("Working directory filesystem", self.work_filesystem),
            *((f"`{name}`", value) for name, value in self.packages),
            ("Page-cache eviction", self.page_cache_eviction),
        ]


def capture() -> Environment:
    """Read the machine. Called once, before the first measurement."""
    return Environment(
        when=datetime.now(UTC).isoformat(timespec="seconds"),
        commit=_commit(),
        python=_python(),
        interpreter_path=sys.executable,
        platform=platform.platform(),
        cpu_model=_cpu_model(),
        cpus=f"{os.process_cpu_count()} available to this process, {os.cpu_count()} on the machine",
        default_gate=default_concurrency(),
        load_average=_load_average(),
        memory=_memory(),
        work_filesystem=_filesystem(Path(tempfile.gettempdir())),
        packages=_packages(),
        page_cache_eviction=(
            "posix_fadvise(DONTNEED), guest page cache only"
            if cold_cache_supported()
            else "unavailable: cold-cache runs skipped"
        ),
    )


def _python() -> str:
    threading = "free-threaded" if not _gil_enabled() else "with the GIL"
    return f"{platform.python_version()} ({platform.python_implementation()}, {threading})"


def _gil_enabled() -> bool:
    """Whether this build runs one thread of Python at a time.

    The single most important fact about a concurrency measurement on CPython,
    and `sys._is_gil_enabled` is how a 3.13-or-later build answers it. A build
    that does not have the function has the GIL.
    """
    is_enabled = getattr(sys, "_is_gil_enabled", None)
    return True if is_enabled is None else bool(is_enabled())


def _commit() -> str:
    """The commit the tree is on, and whether it has been edited since."""
    head = _git("rev-parse", "--short", "HEAD")
    if head is None:
        return UNKNOWN
    dirty = _git("status", "--porcelain")
    return f"{head} (working tree modified)" if dirty else head


def _git(*arguments: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", *arguments],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _cpu_model() -> str:
    for line in _read("/proc/cpuinfo").splitlines():
        if line.startswith("model name"):
            return line.split(":", 1)[1].strip()
    return platform.processor() or UNKNOWN


def _load_average() -> str:
    try:
        return ", ".join(f"{value:.2f}" for value in os.getloadavg())
    except OSError, AttributeError:
        return UNKNOWN


def _memory() -> str:
    for line in _read("/proc/meminfo").splitlines():
        if line.startswith("MemTotal:"):
            kilobytes = int(line.split()[1])
            return f"{kilobytes / 1024 / 1024:.1f} GiB"
    return UNKNOWN


def _filesystem(directory: Path) -> str:
    """The mount `directory` sits on, by longest matching mount point.

    Where instances live decides what a write costs, and "tmpfs" and "a network
    volume" are different benchmarks. The answer names the directory as well as
    the mount, because the default working root is under the system temporary
    directory and a reader should be able to see which one that was.
    """
    best: tuple[int, str] = (-1, UNKNOWN)
    for line in _read("/proc/mounts").splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        device, point, kind = parts[0], parts[1], parts[2]
        if str(directory) == point or str(directory).startswith(point.rstrip("/") + "/"):
            best = max(best, (len(point), f"{kind} on {device} ({point})"))
    return f"{directory}: {best[1]}"


def _packages() -> tuple[tuple[str, str], ...]:
    """The versions that decide what a call costs, SQLite's own included."""
    found = [(name, _version(name)) for name in ("seahaven", "projecttracker", "apsw", "pydantic")]
    return (*found, ("sqlite", _sqlite_version()))


def _version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return UNKNOWN


def _sqlite_version() -> str:
    try:
        import apsw
    except ImportError:
        return UNKNOWN
    return f"{apsw.sqlite_lib_version()} (bundled with apsw)"


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""
