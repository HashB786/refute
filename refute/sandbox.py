"""Runs one untrusted script as a separate process, with a timeout and caps.

Every piece of code Refute executes goes through here: model-written oracles,
validators and generators, and the target solution itself. Input arrives only
on stdin, output is capped, and a process that overruns its timeout is killed
along with any children it started.

This is containment, not isolation. The README says plainly that Refute runs
untrusted code and recommends the provided Dockerfile with --network none.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

from .models import Limits, RunResult

_IS_WINDOWS = sys.platform == "win32"

try:  # POSIX only; absent on Windows.
    import resource
except ImportError:  # pragma: no cover - exercised on Windows
    resource = None  # type: ignore[assignment]


@contextmanager
def workspace(prefix: str = "refute-") -> Iterator[Path]:
    """A fresh temp directory for one run, removed when the block exits."""
    path = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def write_script(directory: Path, name: str, source: str) -> Path:
    """Put a script on disk inside the workspace and return its path."""
    path = directory / name
    path.write_text(source, encoding="utf-8")
    return path


def _limit_memory(memory_mb: int):
    """A preexec_fn that caps address space and starts a new process group."""
    if resource is None:
        return None

    def apply() -> None:  # pragma: no cover - runs in the child process
        os.setsid()
        limit = memory_mb * 1024 * 1024
        for which in (resource.RLIMIT_AS, resource.RLIMIT_DATA):
            try:
                resource.setrlimit(which, (limit, limit))
            except (ValueError, OSError):
                pass

    return apply


def _popen_kwargs(memory_mb: int) -> dict[str, object]:
    """Platform-specific arguments that make the whole group killable."""
    if _IS_WINDOWS:
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": flags}
    return {"preexec_fn": _limit_memory(memory_mb)}


def _kill_group(process: subprocess.Popen) -> None:
    """Kill the process and anything it spawned, on either platform."""
    if _IS_WINDOWS:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(process.pid), 9)
        except (ProcessLookupError, PermissionError):
            pass
    process.kill()


def _cap(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def run(
    command: Sequence[str],
    stdin_text: str = "",
    timeout: float = 10.0,
    cwd: Path | None = None,
    limits: Limits | None = None,
) -> RunResult:
    """Run a command with stdin piped in, and return what it did.

    Never raises on the child's behalf: a crash, a timeout and a clean exit
    all come back as a RunResult, because every one of them is a verdict.
    """
    limits = limits or Limits()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "TEMP": str(cwd) if cwd else os.environ.get("TEMP", ""),
    }
    started = time.monotonic()
    process = subprocess.Popen(
        list(command),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_popen_kwargs(limits.memory_mb),  # type: ignore[arg-type]
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(input=stdin_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(process)
        stdout, stderr = process.communicate()
    duration = time.monotonic() - started

    stdout, cut_out = _cap(stdout or "", limits.output_cap)
    stderr, cut_err = _cap(stderr or "", limits.output_cap)
    return RunResult(
        exit_code=process.returncode if not timed_out else -1,
        stdout=stdout,
        stderr=stderr,
        duration=duration,
        timed_out=timed_out,
        truncated=cut_out or cut_err,
    )


def run_python(
    script: Path,
    stdin_text: str = "",
    args: Sequence[str] = (),
    timeout: float = 10.0,
    limits: Limits | None = None,
) -> RunResult:
    """Run a Python script in isolated mode, so nothing is picked up from cwd."""
    command = [sys.executable, "-I", "-S", str(script), *[str(a) for a in args]]
    return run(
        command,
        stdin_text=stdin_text,
        timeout=timeout,
        cwd=script.parent,
        limits=limits,
    )


def have_gpp() -> bool:
    """Whether a C++ target can be compiled on this machine."""
    return shutil.which("g++") is not None


def compile_cpp(source: Path, out_dir: Path) -> tuple[Path | None, str]:
    """Compile a .cpp target. Returns (binary, "") or (None, compiler output)."""
    if not have_gpp():
        return None, "g++ was not found on PATH, so .cpp targets cannot be run"
    binary = out_dir / ("target.exe" if _IS_WINDOWS else "target")
    result = run(
        ["g++", "-O2", "-std=c++17", "-o", str(binary), str(source)],
        timeout=60.0,
    )
    if result.exit_code != 0 or not binary.exists():
        return None, (result.stderr or result.stdout).strip()
    return binary, ""


def command_for(path: Path) -> list[str]:
    """The command that runs a target, by file extension."""
    if path.suffix == ".py":
        return [sys.executable, "-I", "-S", str(path)]
    return [str(path)]


def scrub(text: str, *paths: Path) -> str:
    """Replace temp paths with <tmp>, so feedback to the model stays stable.

    Determinism matters here: the cache key is a hash of the prompt, so a
    temp directory name leaking into a repair prompt would miss every time.
    """
    out = text
    for path in paths:
        for form in {str(path), str(path.resolve()), path.as_posix()}:
            out = out.replace(form, "<tmp>")
    return out
