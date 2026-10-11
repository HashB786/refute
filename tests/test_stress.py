"""The judge and the stress loop, with real scripts in a real workspace."""

from __future__ import annotations

from pathlib import Path

from refute.models import Budget, Limits, Verdict
from refute.sandbox import command_for, workspace, write_script
from refute.stress import SIZES, Tools, run_stress, tokens_equal

VALIDATOR = """
import sys
data = sys.stdin.read().split()
if len(data) != 2:
    sys.exit(1)
try:
    a, b = int(data[0]), int(data[1])
except ValueError:
    sys.exit(1)
sys.exit(0 if 0 <= a <= 100 and 0 <= b <= 100 else 1)
"""

GENERATOR = """
import random
import sys
rng = random.Random(int(sys.argv[1]))
size = int(sys.argv[2])
mode = sys.argv[3]
top = 2 if mode == "tiny_values" else (100 if mode == "extreme" else 40)
print(rng.randint(0, top), rng.randint(0, min(top, size * 3)))
"""

ORACLE = """
import sys
a, b = [int(x) for x in sys.stdin.read().split()]
print(a + b)
"""

# Wrong only once the second number passes 7. The generator caps that number
# at three times the size, so this is out of reach at size 1 or 2 and shows up
# a few sizes in: the loop has to walk up the sizes to find it.
TARGET_WA = """
import sys
a, b = [int(x) for x in sys.stdin.read().split()]
print(a + b if b < 8 else a)
"""

TARGET_CRASH = """
import sys
a, b = [int(x) for x in sys.stdin.read().split()]
if b > 20:
    raise ValueError("no")
print(a + b)
"""

TARGET_SLOW = """
import sys
import time
a, b = [int(x) for x in sys.stdin.read().split()]
if b > 20:
    time.sleep(30)
print(a + b)
"""

DISAGREEING_ORACLE = """
import sys
a, b = [int(x) for x in sys.stdin.read().split()]
print(a + b + (1 if b > 50 else 0))
"""


def build(directory: Path, target: str, oracle_b: str | None = ORACLE) -> Tools:
    target_path = write_script(directory, "target.py", target)
    return Tools(
        workdir=directory,
        generator=write_script(directory, "gen.py", GENERATOR),
        validator=write_script(directory, "validator.py", VALIDATOR),
        oracle_a=write_script(directory, "oracle_a.py", ORACLE),
        oracle_b=write_script(directory, "oracle_b.py", oracle_b) if oracle_b else None,
        target=command_for(target_path),
        limits=Limits(target=2.0),
    )


# ---------------------------------------------------------------------------
# comparison


def test_token_comparison_ignores_layout():
    assert tokens_equal("1 2\n3\n", " 1\n2 3 ")
    assert not tokens_equal("1 2", "1 2 3")
    assert not tokens_equal("1 3", "1 2")


def test_a_float_tolerance_applies_only_when_asked_for():
    assert not tokens_equal("1.0001", "1.0")
    assert tokens_equal("1.0001", "1.0", float_tol=1e-3)
    assert not tokens_equal("2.0", "1.0", float_tol=1e-3)
    assert not tokens_equal("apples", "1.0", float_tol=1e-3)


# ---------------------------------------------------------------------------
# the judge


def test_a_matching_target_passes():
    with workspace() as directory:
        tools = build(directory, ORACLE)
        judgement = tools.judge("5 7\n")
    assert judgement.verdict is Verdict.PASS
    assert judgement.expected.strip() == "12"
    assert not judgement.oracle_b_run


def test_a_wrong_answer_is_confirmed_by_the_second_reference():
    with workspace() as directory:
        tools = build(directory, TARGET_WA)
        judgement = tools.judge("5 70\n")
    assert judgement.verdict is Verdict.WA
    assert judgement.expected.strip() == "75"
    assert judgement.actual.strip() == "5"
    assert judgement.oracle_b_run
    assert judgement.oracles_agreed


def test_a_crash_is_a_run_error():
    with workspace() as directory:
        judgement = build(directory, TARGET_CRASH).judge("1 70\n")
    assert judgement.verdict is Verdict.RE
    assert "ValueError" in judgement.actual


def test_a_target_that_overruns_its_timeout_is_a_time_limit():
    with workspace() as directory:
        tools = build(directory, TARGET_SLOW)
        tools.limits = Limits(target=1.0)
        judgement = tools.judge("1 70\n")
    assert judgement.verdict is Verdict.TLE


def test_references_that_disagree_set_the_test_aside():
    with workspace() as directory:
        tools = build(directory, TARGET_WA, oracle_b=DISAGREEING_ORACLE)
        judgement = tools.judge("1 70\n")
    assert judgement.verdict is Verdict.ORACLE_CONFLICT
    assert judgement.oracles_agreed is False
    assert "prove" in judgement.note


def test_a_reference_that_does_not_answer_sets_the_test_aside():
    with workspace() as directory:
        tools = build(directory, ORACLE)
        tools.oracle_a = write_script(directory, "broken.py", "raise SystemExit(3)\n")
        judgement = tools.judge("1 2\n")
    assert judgement.verdict is Verdict.ORACLE_TIMEOUT
    assert "reference A" in judgement.note


def test_every_tenth_test_measures_agreement_even_on_a_pass():
    with workspace() as directory:
        tools = build(directory, ORACLE)
        judgement = tools.judge("5 7\n", always_check_b=True)
    assert judgement.verdict is Verdict.PASS
    assert judgement.oracle_b_run
    assert judgement.oracles_agreed


def test_a_trusted_oracle_needs_no_confirmation():
    with workspace() as directory:
        tools = build(directory, TARGET_WA, oracle_b=None)
        judgement = tools.judge("5 70\n")
    assert judgement.verdict is Verdict.WA
    assert not judgement.oracle_b_run
    assert judgement.oracles_agreed is None


def test_the_validator_decides_what_is_worth_testing():
    with workspace() as directory:
        tools = build(directory, ORACLE)
        assert tools.validate("1 2\n")
        assert not tools.validate("1 2 3\n")
        assert not tools.validate("999 2\n")
        assert not tools.validate("")


def test_the_generator_is_driven_by_seed_size_and_mode():
    with workspace() as directory:
        tools = build(directory, ORACLE)
        first = tools.generate(7, 5, "normal")
        assert first
        assert tools.generate(7, 5, "normal") == first
        assert tools.generate(8, 5, "normal") != first


# ---------------------------------------------------------------------------
# the stress loop


def test_stress_finds_a_failure_and_stops_there():
    with workspace() as directory:
        outcome = run_stress(build(directory, TARGET_WA), Budget(max_tests=200))
    assert outcome.failure is not None
    assert outcome.failure.verdict == Verdict.WA.value
    assert outcome.failure.expected.strip() != outcome.failure.actual.strip()
    # The run stops once a failure is confirmed, well short of the budget.
    assert len(outcome.tests) < 200
    # The one reported is the earliest failure, not whichever finished first.
    # Tests already in flight alongside it still get recorded.
    failing = [r for r in outcome.tests if Verdict(r.verdict).is_failure()]
    assert failing
    assert outcome.failure.seed == min(failing, key=lambda r: r.index).seed
    # Every test that ran was recorded, in run order, with no gaps.
    assert [record.index for record in outcome.tests] == list(
        range(1, len(outcome.tests) + 1)
    )


def test_stress_reports_no_failure_for_a_correct_target():
    with workspace() as directory:
        outcome = run_stress(build(directory, ORACLE), Budget(max_tests=60))
    assert outcome.failure is None
    assert outcome.failures == 0
    assert len(outcome.tests) == 60
    assert outcome.largest_tokens == 2
    assert all(record.verdict == Verdict.PASS.value for record in outcome.tests)


def test_stress_measures_oracle_agreement_on_every_tenth_test():
    with workspace() as directory:
        outcome = run_stress(build(directory, ORACLE), Budget(max_tests=40))
    assert outcome.oracle_checks == 4
    assert outcome.oracle_agreements == 4
    assert sum(1 for record in outcome.tests if record.oracle_b_run) == 4


def test_the_test_budget_is_honored():
    with workspace() as directory:
        outcome = run_stress(build(directory, ORACLE), Budget(max_tests=17))
    assert len(outcome.tests) == 17


def test_a_small_size_is_tried_before_a_large_one():
    with workspace() as directory:
        outcome = run_stress(build(directory, ORACLE), Budget(max_tests=24))
    assert outcome.tests[0].size == SIZES[0]
    assert outcome.tests[-1].size <= SIZES[1]


def test_set_aside_tests_are_counted_and_the_target_is_not_blamed():
    with workspace() as directory:
        tools = build(directory, ORACLE)
        tools.validator = write_script(directory, "no.py", "import sys\nsys.exit(1)\n")
        outcome = run_stress(tools, Budget(max_tests=12))
    assert outcome.failure is None
    assert outcome.skipped == {Verdict.INVALID.value: 12}
    assert all(record.verdict == Verdict.INVALID.value for record in outcome.tests)


def test_progress_is_reported_while_the_run_goes_on():
    seen: list[int] = []
    with workspace() as directory:
        run_stress(
            build(directory, ORACLE),
            Budget(max_tests=40),
            on_progress=lambda state: seen.append(len(state.tests)),
        )
    assert seen
    assert seen == sorted(seen)
    assert seen[-1] == 40
