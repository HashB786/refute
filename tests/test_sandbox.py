"""The sandbox is never mocked. These tests run real subprocesses."""

from __future__ import annotations

import sys
from pathlib import Path

from refute.models import Limits
from refute.sandbox import (
    command_for,
    run,
    run_python,
    scrub,
    workspace,
    write_script,
)


def test_stdin_reaches_the_script_and_stdout_comes_back():
    with workspace() as directory:
        script = write_script(
            directory,
            "echo.py",
            "import sys\nprint(sys.stdin.read().strip().upper())\n",
        )
        result = run_python(script, stdin_text="refute\n")
        assert result.exit_code == 0
        assert result.stdout.strip() == "REFUTE"
        assert not result.timed_out
        assert result.ok


def test_arguments_are_passed_through():
    with workspace() as directory:
        script = write_script(
            directory,
            "args.py",
            "import sys\nprint(' '.join(sys.argv[1:]))\n",
        )
        result = run_python(script, args=["7", "30", "extreme"])
        assert result.stdout.split() == ["7", "30", "extreme"]


def test_a_nonzero_exit_is_reported_not_raised():
    with workspace() as directory:
        script = write_script(directory, "bad.py", "import sys\nsys.exit(3)\n")
        result = run_python(script)
        assert result.exit_code == 3
        assert not result.ok
        assert not result.timed_out


def test_a_crash_keeps_the_traceback_on_stderr():
    with workspace() as directory:
        script = write_script(directory, "boom.py", "raise ValueError('nope')\n")
        result = run_python(script)
        assert result.exit_code != 0
        assert "ValueError" in result.stderr


def test_a_timeout_is_a_verdict_and_the_process_is_killed():
    with workspace() as directory:
        script = write_script(
            directory, "slow.py", "import time\nwhile True:\n    time.sleep(0.05)\n"
        )
        result = run_python(script, timeout=1.0)
        assert result.timed_out
        assert not result.ok
        assert result.duration < 20.0


def test_output_is_capped_and_the_cap_is_recorded():
    limits = Limits(output_cap=500)
    with workspace() as directory:
        script = write_script(directory, "loud.py", "print('x' * 20000)\n")
        result = run_python(script, timeout=20.0, limits=limits)
        assert len(result.stdout) == 500
        assert result.truncated


def test_output_under_the_cap_is_not_marked_truncated():
    with workspace() as directory:
        script = write_script(directory, "quiet.py", "print('small')\n")
        result = run_python(script)
        assert not result.truncated


def test_the_workspace_is_removed_afterwards():
    with workspace() as directory:
        assert directory.is_dir()
        kept = directory
        write_script(directory, "x.py", "print(1)\n")
    assert not kept.exists()


def test_a_script_cannot_pick_up_a_module_from_its_own_directory():
    # Isolated mode matters: a planted json.py next to the script must not
    # win over the standard library.
    with workspace() as directory:
        write_script(directory, "json.py", "raise SystemExit('planted module ran')\n")
        script = write_script(
            directory, "main.py", "import json\nprint(json.dumps([1, 2]))\n"
        )
        result = run_python(script)
        assert result.exit_code == 0
        assert result.stdout.strip() == "[1, 2]"


def test_run_accepts_a_plain_command():
    result = run([sys.executable, "-c", "print(41 + 1)"], timeout=20.0)
    assert result.stdout.strip() == "42"


def test_command_for_uses_python_for_py_and_the_binary_otherwise():
    assert command_for(Path("sol.py"))[0] == sys.executable
    assert command_for(Path("sol.exe")) == [str(Path("sol.exe"))]


def test_scrub_replaces_temp_paths_so_feedback_stays_deterministic():
    with workspace() as directory:
        text = "failed while running " + str(directory / "gen.py")
        cleaned = scrub(text, directory)
        assert str(directory) not in cleaned
        assert "<tmp>" in cleaned
