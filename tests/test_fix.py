"""The diagnosis and the suggested fix.

The model is a FakeLLM. The verification is real: every fix here is written to
disk and run against the samples, the counterexample and a fresh stress pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from refute import fix as fixer
from refute.llm import FakeLLM
from refute.models import Budget, Counterexample, Limits, Sample, Verdict
from refute.sandbox import command_for, workspace, write_script
from refute.stress import Tools
from refute.synth import SynthError

from .test_shrink import COIN_GENERATOR, COIN_GREEDY, COIN_ORACLE, COIN_VALIDATOR

STATEMENT = "Pay an amount with as few coins as possible. First line: n and a."

SAMPLES = [
    Sample("1", "3 11\n1 5 10\n", "2\n", "samples_dir"),
    Sample("2", "4 7\n1 2 3 5\n", "2\n", "samples_dir"),
]

FOUND = Counterexample(
    input_text="3 6\n1 3 4\n",
    expected="2\n",
    actual="3\n",
    verdict=Verdict.WA.value,
    size=5,
    mode="normal",
    seed=1,
)

# A fix that is wrong only on larger inputs, so the samples and the
# counterexample pass and only the stress pass catches it.
SUBTLY_WRONG = """
import sys
data = sys.stdin.read().split()
n, amount = int(data[0]), int(data[1])
coins = [int(x) for x in data[2 : 2 + n]]
if amount <= 20:
    best = [0] + [10 ** 9] * amount
    for total in range(1, amount + 1):
        for coin in coins:
            if coin <= total:
                best[total] = min(best[total], best[total - coin] + 1)
    print(best[amount])
else:
    used = 0
    for coin in sorted(coins, reverse=True):
        used += amount // coin
        amount %= coin
    print(used)
"""

CRASHES = """
import sys
sys.stdin.read()
raise ValueError("no")
"""


def fenced(code: str) -> str:
    return "```python\n" + code.strip() + "\n```\n"


def coin_tools(directory: Path) -> Tools:
    target = write_script(directory, "target.py", COIN_GREEDY)
    return Tools(
        workdir=directory,
        generator=write_script(directory, "gen.py", COIN_GENERATOR),
        validator=write_script(directory, "validator.py", COIN_VALIDATOR),
        oracle_a=write_script(directory, "oracle_a.py", COIN_ORACLE),
        oracle_b=write_script(directory, "oracle_b.py", COIN_ORACLE),
        target=command_for(target),
        limits=Limits(target=2.0),
    )


# ---------------------------------------------------------------------------
# the diagnosis


def test_a_long_diagnosis_is_cut_to_four_sentences():
    text = "One. Two. Three. Four. Five. Six."
    assert fixer._trim(text) == "One. Two. Three. Four."


def test_a_short_diagnosis_is_left_alone():
    assert fixer._trim("Just the one thing.") == "Just the one thing."
    assert fixer._trim("A. B.") == "A. B."


def test_a_diagnosis_with_no_full_stop_still_comes_through():
    assert fixer._trim("it takes the biggest coin first") == (
        "it takes the biggest coin first"
    )


def test_a_decimal_point_does_not_end_a_sentence():
    assert fixer._trim("It fails for 1.5 and 2.5 only.") == "It fails for 1.5 and 2.5 only."


def test_the_diagnosis_prompt_carries_the_failure_and_not_the_answer_key():
    llm = FakeLLM({"diagnose": "It takes the largest coin first."})
    text = fixer.diagnose(llm, STATEMENT, COIN_GREEDY, FOUND, "greedy.py")
    assert text == "It takes the largest coin first."
    prompt = llm.prompts[0][1]
    assert "3 6" in prompt
    assert "1 3 4" in prompt
    assert "the right answer is" in prompt
    assert "greedy.py" in prompt
    assert "at most four sentences" in prompt


def test_a_crash_is_described_as_a_crash_in_the_prompt():
    crashed = Counterexample("1\n", "", "ValueError: no", Verdict.RE.value, 1, "n", 1)
    llm = FakeLLM({"diagnose": "It divides by zero."})
    fixer.diagnose(llm, STATEMENT, COIN_GREEDY, crashed, "s.py")
    assert "crashed with" in llm.prompts[0][1]


def test_a_timeout_is_described_as_running_out_of_time():
    slow = Counterexample("1\n", "", "", Verdict.TLE.value, 1, "n", 1)
    llm = FakeLLM({"diagnose": "It loops."})
    fixer.diagnose(llm, STATEMENT, COIN_GREEDY, slow, "s.py")
    assert "ran out of time" in llm.prompts[0][1]


# ---------------------------------------------------------------------------
# the proposal


def test_a_proposed_fix_is_pulled_out_of_the_fenced_block():
    llm = FakeLLM({"fix": fenced(COIN_ORACLE)})
    source = fixer.propose(llm, STATEMENT, COIN_GREEDY, FOUND, "greedy.py")
    assert "best" in source
    assert "```" not in source


def test_a_proposed_fix_that_breaks_the_guard_is_refused():
    llm = FakeLLM({"fix": fenced("import os\nprint(os.getcwd())\n")})
    with pytest.raises(SynthError) as caught:
        fixer.propose(llm, STATEMENT, COIN_GREEDY, FOUND, "greedy.py")
    assert "guard refused the suggested fix" in str(caught.value)


def test_a_reply_with_no_code_is_refused():
    llm = FakeLLM({"fix": "I would rewrite it as a dynamic program."})
    with pytest.raises(SynthError):
        fixer.propose(llm, STATEMENT, COIN_GREEDY, FOUND, "greedy.py")


def test_the_fix_prompt_asks_for_a_whole_program():
    llm = FakeLLM({"fix": fenced(COIN_ORACLE)})
    fixer.propose(llm, STATEMENT, COIN_GREEDY, FOUND, "greedy.py")
    prompt = llm.prompts[0][1]
    assert "complete Python 3 program" in prompt
    assert "not a patch" in prompt


# ---------------------------------------------------------------------------
# verification, which is the only thing that decides


def test_a_correct_fix_is_verified():
    with workspace() as directory:
        tools = coin_tools(directory)
        verified, reason = fixer.verify(
            COIN_ORACLE, tools, SAMPLES, FOUND, budget=Budget(fix_stress_tests=60)
        )
    assert verified, reason
    assert reason == ""


def test_a_fix_that_misses_a_sample_is_not_verified():
    with workspace() as directory:
        tools = coin_tools(directory)
        verified, reason = fixer.verify("print(0)\n", tools, SAMPLES, FOUND)
    assert not verified
    assert reason == "it does not match sample 1"


def test_a_fix_that_crashes_is_not_verified():
    with workspace() as directory:
        tools = coin_tools(directory)
        verified, reason = fixer.verify(CRASHES, tools, SAMPLES, FOUND)
    assert not verified
    assert "crashed on sample 1" in reason


def test_a_fix_that_still_gets_the_counterexample_wrong_is_not_verified():
    with workspace() as directory:
        tools = coin_tools(directory)
        # The greedy solution passes both samples by construction.
        verified, reason = fixer.verify(COIN_GREEDY, tools, SAMPLES, FOUND)
    assert not verified
    assert reason == "it still gets the counterexample wrong"


def test_a_fix_that_only_fails_on_larger_inputs_is_caught_by_the_stress_pass():
    # This is the case the two hundred extra tests exist for: the samples and
    # the counterexample all pass, and the fix is still wrong.
    with workspace() as directory:
        tools = coin_tools(directory)
        path = write_script(directory, "probe.py", SUBTLY_WRONG)
        from refute.sandbox import run_python

        assert run_python(path, FOUND.input_text).stdout.strip() == "2"
        verified, reason = fixer.verify(
            SUBTLY_WRONG, tools, SAMPLES, FOUND, budget=Budget(fix_stress_tests=200)
        )
    assert not verified
    assert "random tests in" in reason


def test_the_fix_is_tested_on_seeds_the_first_run_never_used():
    from refute.stress import _plan

    first = {seed for _, _, _, seed in _plan(1, 0, 0)}
    salted = {seed for _, _, _, seed in _plan(1, 0, fixer.FIX_SEED_SALT)}
    assert not (first & salted)


# ---------------------------------------------------------------------------
# the whole step


def test_explain_diagnoses_and_verifies_in_one_go(tmp_path):
    target = tmp_path / "greedy.py"
    target.write_text(COIN_GREEDY, encoding="utf-8")
    llm = FakeLLM(
        {
            "diagnose": "It always takes the largest coin that fits.",
            "fix": fenced(COIN_ORACLE),
        }
    )
    with workspace() as directory:
        tools = coin_tools(directory)
        outcome = fixer.explain(
            llm,
            STATEMENT,
            target,
            FOUND,
            tools,
            SAMPLES,
            budget=Budget(fix_stress_tests=60),
        )
    assert outcome.diagnosis == "It always takes the largest coin that fits."
    assert "best" in outcome.source
    assert outcome.verified
    assert outcome.reason == ""
    assert [purpose for purpose, _ in llm.prompts] == ["diagnose", "fix"]


def test_explain_keeps_the_diagnosis_when_the_fix_is_refused(tmp_path):
    target = tmp_path / "greedy.py"
    target.write_text(COIN_GREEDY, encoding="utf-8")
    llm = FakeLLM(
        {
            "diagnose": "It takes the largest coin first.",
            "fix": fenced("import subprocess\n"),
        }
    )
    with workspace() as directory:
        outcome = fixer.explain(
            llm, STATEMENT, target, FOUND, coin_tools(directory), SAMPLES
        )
    assert outcome.diagnosis == "It takes the largest coin first."
    assert outcome.source == ""
    assert not outcome.verified
    assert "guard refused" in outcome.reason


def test_explain_reports_an_unverified_fix_with_its_reason(tmp_path):
    target = tmp_path / "greedy.py"
    target.write_text(COIN_GREEDY, encoding="utf-8")
    llm = FakeLLM(
        {"diagnose": "Wrong approach.", "fix": fenced("print(0)\n")}
    )
    with workspace() as directory:
        outcome = fixer.explain(
            llm, STATEMENT, target, FOUND, coin_tools(directory), SAMPLES
        )
    assert outcome.source == "print(0)\n"
    assert not outcome.verified
    assert outcome.reason == "it does not match sample 1"
