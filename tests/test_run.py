"""`refute run` end to end: a FakeLLM for the tooling, real execution for the rest.

The model is canned. Nothing else is: the four scripts really run, the greedy
example really fails, and the console output here is the one DESIGN.md
specifies, down to the column the labels line up in.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from refute.cli import differing, finding, main, program_text
from refute.llm import FakeLLM
from refute.models import Counterexample, Limits, Verdict

from .test_shrink import COIN_GENERATOR, COIN_ORACLE, COIN_VALIDATOR

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "coin-change"


def fenced(code: str) -> str:
    return "```python\n" + code.strip() + "\n```\n"


def canned() -> FakeLLM:
    return FakeLLM(
        {
            "oracle_a": fenced(COIN_ORACLE),
            "oracle_b": fenced(COIN_ORACLE),
            "validator": fenced(COIN_VALIDATOR),
            "generator": fenced(COIN_GENERATOR),
        }
    )


@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    """Run in a scratch directory, so refute-out/ lands there."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def fake_model(monkeypatch):
    model = canned()
    monkeypatch.setattr("refute.cli.build_client", lambda **kwargs: model)
    return model


# ---------------------------------------------------------------------------
# the sentences and the marking, on their own


def test_the_finding_names_both_answers_when_they_are_short():
    found = Counterexample("3 6\n1 3 4\n", "2\n", "3\n", Verdict.WA.value, 5, "normal", 1)
    assert (
        finding("greedy.py", found, Limits())
        == "Refuted. greedy.py prints 3. The right answer is 2."
    )


def test_a_long_answer_is_described_instead_of_quoted():
    found = Counterexample("x\n", "y " * 40, "z " * 40, Verdict.WA.value, 1, "normal", 1)
    assert finding("s.py", found, Limits()) == "Refuted. s.py gives a wrong answer on this input."


def test_a_multi_line_answer_is_described_instead_of_quoted():
    found = Counterexample("x\n", "1\n2\n", "1\n3\n", Verdict.WA.value, 1, "normal", 1)
    assert "gives a wrong answer" in finding("s.py", found, Limits())


def test_a_crash_and_a_timeout_get_their_own_sentence():
    crash = Counterexample("x\n", "", "boom", Verdict.RE.value, 1, "normal", 1)
    assert finding("s.py", crash, Limits()) == "Refuted. s.py crashes on this input."
    slow = Counterexample("x\n", "", "", Verdict.TLE.value, 1, "normal", 1)
    assert (
        finding("s.py", slow, Limits(target=2.0))
        == "Refuted. s.py takes longer than 2 s on this input."
    )


def test_only_the_differing_tokens_are_marked():
    assert differing("1 2 3", "1 9 3", None) == {1}
    assert differing("1 2", "1 2 3", None) == set()
    assert differing("1 2 3 4", "1 2", None) == {2, 3}
    assert differing("1.001 2", "1.0 2", 1e-2) == set()


def test_a_differing_token_is_wrapped_in_parentheses():
    # With NO_COLOR, the parentheses are all that is left to say which is wrong.
    lines = program_text("3 7", {0})
    assert lines[0].plain == "(3) 7"


# ---------------------------------------------------------------------------
# the whole command


def test_run_refutes_the_greedy_example(in_tmp, fake_model, capsys):
    code = main(
        [
            "run",
            str(EXAMPLE / "statement.md"),
            str(EXAMPLE / "greedy.py"),
            "--samples",
            str(EXAMPLE / "samples"),
            "--no-ai-explain",
            "--budget",
            "250",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0, out

    # The header, then one status line per stage, in DESIGN.md's order.
    lines = out.splitlines()
    assert lines[0].startswith("refute ")
    assert "coin-change" in lines[0]
    assert "greedy.py" in lines[0]
    labels = [line.split()[0] for line in lines if line and not line.startswith(" ")]
    for stage in ("samples", "tooling", "stress", "shrink"):
        assert stage in labels

    # Labels line up at column 11 for status rows and 12 for the hero block.
    for line in lines:
        if line.startswith("stress"):
            assert line.startswith("stress     ")
        if line.startswith("  input"):
            assert line.startswith("  input     ")
        if line.startswith("  expected"):
            assert line.startswith("  expected  ")

    assert "reference A ok" in out
    assert "reference B ok" in out
    assert "validator ok" in out
    assert "generator ok" in out
    assert "Refuted." in out
    assert "  run it    " in out
    assert " -> " in out  # the shrink line

    assert "  report    " in out
    assert "report.html" in out
    # Plain ASCII only, and inside eighty columns.
    assert out.isascii()
    assert max(len(line) for line in lines) <= 80


def test_run_writes_the_output_directory(in_tmp, fake_model):
    code = main(
        [
            "run",
            str(EXAMPLE / "statement.md"),
            str(EXAMPLE / "greedy.py"),
            "--samples",
            str(EXAMPLE / "samples"),
            "--no-ai-explain",
            "--budget",
            "250",
        ]
    )
    assert code == 0
    out_dir = in_tmp / "refute-out"
    counterexample = (out_dir / "counterexample.in").read_text(encoding="utf-8")
    expected = (out_dir / "expected.out").read_text(encoding="utf-8")
    actual = (out_dir / "actual.out").read_text(encoding="utf-8")

    assert expected.strip() != actual.strip()
    # The file holds a real input for this problem: a count, an amount, coins.
    numbers = counterexample.split()
    assert int(numbers[0]) == len(numbers) - 2
    assert numbers[2] == "1"

    # The greedy solution really does print what actual.out says it does.
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(EXAMPLE / "greedy.py")],
        input=counterexample,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout.strip() == actual.strip()

    steps = json.loads((out_dir / "shrink.json").read_text(encoding="utf-8"))
    assert steps["steps"][0]["action"] == "found"
    assert steps["steps"][-1]["input_text"] == counterexample
    assert steps["stable"] is True
    assert steps["rechecks"] == 3
    assert [step["step"] for step in steps["steps"]] == list(
        range(len(steps["steps"]))
    )


def test_run_reports_a_target_it_cannot_run(in_tmp, fake_model, capsys):
    target = in_tmp / "solution.rb"
    target.write_text("puts 1\n", encoding="utf-8")
    code = main(
        [
            "run",
            str(EXAMPLE / "statement.md"),
            str(target),
            "--samples",
            str(EXAMPLE / "samples"),
            "--no-ai-explain",
        ]
    )
    assert code == 1
    assert "only .py and .cpp targets" in capsys.readouterr().out


def test_run_says_which_file_is_missing(in_tmp, capsys):
    assert main(["run", "nope.md", "nope.py", "--no-ai-explain"]) == 1
    assert "there is no file at nope.md" in capsys.readouterr().out


def test_run_writes_the_report_page_and_the_json(in_tmp, fake_model):
    import json

    code = main(
        [
            "run",
            str(EXAMPLE / "statement.md"),
            str(EXAMPLE / "greedy.py"),
            "--samples",
            str(EXAMPLE / "samples"),
            "--no-ai-explain",
            "--budget",
            "250",
        ]
    )
    assert code == 0
    out_dir = in_tmp / "refute-out"
    html = (out_dir / "report.html").read_text(encoding="utf-8")
    counterexample = (out_dir / "counterexample.in").read_text(encoding="utf-8")

    assert html.startswith("<!doctype html>")
    assert html.count("<h1>") == 1
    assert "<title>Refute: greedy.py</title>" in html
    # The page shows the input that was really found, and the real test count.
    for line in counterexample.splitlines():
        assert '<div class="line">' + line + "</div>" in html
    assert 'class="strip"' in html
    assert 'class="facts"' in html
    # No model sections, because --no-ai-explain was given.
    assert "What the model said" not in html
    assert "A suggested fix" not in html

    data = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert data["refuted"] is True
    assert data["counterexample"]["input_text"] == counterexample
    assert data["trust"]["samples"] == 2
    assert data["trust"]["smoke_runs"] == 75
    assert data["trust"]["tests_run"] == len(data["tests"])
    assert data["version"] == "0.1.0"
    assert str(data["trust"]["tests_run"]) + " tests" in html


def test_run_explains_and_verifies_a_fix(in_tmp, monkeypatch, capsys):
    model = canned()
    model.set("diagnose", "It always takes the largest coin that fits.")
    model.set("fix", fenced(COIN_ORACLE))
    monkeypatch.setattr("refute.cli.build_client", lambda **kwargs: model)

    code = main(
        [
            "run",
            str(EXAMPLE / "statement.md"),
            str(EXAMPLE / "greedy.py"),
            "--samples",
            str(EXAMPLE / "samples"),
            "--budget",
            "250",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert "explain    diagnosis written, fix verified" in out

    html = (in_tmp / "refute-out" / "report.html").read_text(encoding="utf-8")
    assert "<h2>What the model said</h2>" in html
    assert "Written by the model. Refute has not checked it." in html
    assert "It always takes the largest coin that fits." in html
    assert "<h2>A suggested fix</h2>" in html
    assert "Verified: it passes the 2 samples" in html
    assert 'class="diff"' in html


def test_run_carries_on_when_the_model_has_nothing_to_say(in_tmp, fake_model, capsys):
    # The canned model answers the tooling purposes but not diagnose, so the
    # explanation is skipped and the rest of the report still gets written.
    code = main(
        [
            "run",
            str(EXAMPLE / "statement.md"),
            str(EXAMPLE / "greedy.py"),
            "--samples",
            str(EXAMPLE / "samples"),
            "--budget",
            "250",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert "explain" in out
    html = (in_tmp / "refute-out" / "report.html").read_text(encoding="utf-8")
    assert "What the model said" not in html
    assert 'class="sheet hero"' in html


def test_a_correct_target_is_not_refuted(in_tmp, fake_model, capsys):
    correct = in_tmp / "correct.py"
    correct.write_text(COIN_ORACLE, encoding="utf-8")
    code = main(
        [
            "run",
            str(EXAMPLE / "statement.md"),
            str(correct),
            "--samples",
            str(EXAMPLE / "samples"),
            "--no-ai-explain",
            "--budget",
            "40",
        ]
    )
    out = capsys.readouterr().out
    flat = " ".join(out.split())
    assert code == 2, out
    assert "Not refuted. 40 tests" in flat
    assert "That is evidence, not proof." in flat
    assert "numbers, no failure" in flat
    assert "shrink" not in out
    assert not (in_tmp / "refute-out" / "counterexample.in").exists()
