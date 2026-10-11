"""Shrinking: the moves, the acceptance rule, and the recorded steps.

Most of these drive the shrinker with a stand-in whose judge is a plain
predicate, so a move can be studied on its own. The last two run the whole
thing against real scripts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from refute.models import Budget, Counterexample, Limits, RunResult, Verdict
from refute.sandbox import command_for, workspace, write_script
from refute.shrink import (
    candidates,
    compound_moves,
    line_moves,
    shrink,
    still_fails,
    token_moves,
    value_moves,
    weight,
)
from refute.stress import Tools, run_stress


@dataclass
class Predicate:
    """A stand-in for Tools whose target is one Python function.

    The real thing runs up to four subprocesses per candidate. For studying
    the moves themselves, a predicate says the same in one line: both
    references always answer "ok", and the target fails on the inputs the
    predicate picks out, in whichever way `verdict` says it fails.
    """

    fails: Callable[[str], bool]
    valid: Callable[[str], bool] = lambda text: True
    verdict: Verdict = Verdict.WA
    judged: list[str] = field(default_factory=list)
    oracle_a: Path = Path("oracle_a.py")
    oracle_b: Path | None = Path("oracle_b.py")
    float_tol: float | None = None

    def validate(self, text: str) -> bool:
        return bool(text.strip()) and self.valid(text)

    def generate(self, seed: int, size: int, mode: str) -> str:
        return ""

    def reference(self, text: str, path: Path) -> tuple[str, bool]:
        return "ok", True

    def run_target(self, text: str) -> RunResult:
        self.judged.append(text)
        try:
            failed = self.fails(text)
        except Exception:
            # The target now runs before the validator, so it sees candidates
            # it cannot parse. A real target is a subprocess: those come back
            # as a crash, not as an exception in the shrinker.
            return RunResult(exit_code=1, stdout="", stderr="unreadable", duration=0.0)
        if not failed:
            return RunResult(exit_code=0, stdout="ok", stderr="", duration=0.0)
        if self.verdict is Verdict.RE:
            return RunResult(exit_code=1, stdout="", stderr="boom", duration=0.0)
        if self.verdict is Verdict.TLE:
            return RunResult(
                exit_code=0, stdout="", stderr="", duration=0.0, timed_out=True
            )
        return RunResult(exit_code=0, stdout="wrong", stderr="", duration=0.0)


def start(text: str, verdict: Verdict = Verdict.WA) -> Counterexample:
    return Counterexample(
        input_text=text,
        expected="ok",
        actual="wrong",
        verdict=verdict.value,
        size=len(text.split()),
        mode="normal",
        seed=1,
    )


def counted(text: str) -> bool:
    """Valid when the first number says how many numbers are on the next line."""
    lines = text.splitlines()
    if len(lines) != 2:
        return False
    head = lines[0].split()
    if not head:
        return False
    try:
        declared = int(head[0])
        values = [int(token) for token in lines[1].split()]
    except ValueError:
        return False
    return declared == len(values) and declared >= 1


# ---------------------------------------------------------------------------
# weight, which is what makes the loop finish


def test_weight_falls_for_fewer_tokens_then_smaller_numbers_then_fewer_characters():
    assert weight("1 2") < weight("1 2 3")
    assert weight("1 1") < weight("1 9")
    assert weight("1") < weight("01")


def test_a_sign_flip_that_does_not_shrink_is_not_offered():
    # 1 to -1 keeps the magnitude and adds a character, so it cannot be taken.
    # Without this the loop would walk 1, -1, 1, -1 forever.
    assert all(candidate != "-1\n" for _, candidate in candidates("1\n"))


def test_every_candidate_is_strictly_smaller_than_what_it_came_from():
    text = "3 6\n1 3 4\n"
    for _, candidate in candidates(text):
        assert weight(candidate) < weight(text)


# ---------------------------------------------------------------------------
# the moves


def test_a_count_and_its_last_number_move_together():
    moves = dict(
        (action, text)
        for text, action in ((t, a) for a, t in compound_moves("3\n1 3 4\n"))
    )
    assert "lowered a count and dropped the last number it counted" in moves
    assert moves["lowered a count and dropped the last number it counted"] == "2\n1 3\n"


def test_a_count_and_its_last_line_move_together():
    produced = [text for action, text in compound_moves("2\n1 1\n2 2\n") if "line" in action]
    assert "1\n1 1\n" in produced


def test_a_count_in_the_middle_of_a_line_is_recognized():
    # "n a" then n numbers: the first token counts, the second does not.
    produced = [text for _, text in compound_moves("3 6\n1 3 4\n")]
    assert "2 6\n1 3\n" in produced


def test_a_number_that_counts_nothing_gets_no_compound_move():
    assert compound_moves("9\n1 2\n") == []


def test_line_token_and_value_moves_do_what_they_say():
    assert "1 2\n" in [text for _, text in line_moves("3 4\n1 2\n")]
    assert "3\n" in [text for _, text in token_moves("3 4\n")]
    # Three values per number and nothing in between: halves and decrements
    # are gone, because the binary search covers that ground in fewer probes.
    assert [text for _, text in value_moves("8\n")] == ["0\n", "1\n", "-1\n"]


def test_a_single_line_input_has_no_line_move():
    assert line_moves("1 2 3\n") == []


# ---------------------------------------------------------------------------
# the acceptance rule


def test_a_candidate_the_validator_rejects_is_not_accepted():
    tools = Predicate(fails=lambda text: True, valid=counted)
    assert still_fails(tools, "2\n1 2\n", Verdict.WA.value) is not None
    assert still_fails(tools, "9\n1 2\n", Verdict.WA.value) is None


def test_a_candidate_that_fails_differently_is_not_accepted():
    tools = Predicate(fails=lambda text: True, verdict=Verdict.RE)
    assert still_fails(tools, "1\n", Verdict.WA.value) is None
    assert still_fails(tools, "1\n", Verdict.RE.value) is not None


def test_only_the_target_runs_when_its_behaviour_changed():
    # The verdict being shrunk is a crash and this candidate does not crash,
    # so the validator and the two references have nothing to add.
    asked: list[str] = []

    def valid(text: str) -> bool:
        asked.append(text)
        return True

    tools = Predicate(fails=lambda text: False, valid=valid, verdict=Verdict.RE)
    assert still_fails(tools, "1\n", Verdict.RE.value) is None
    assert asked == []


def test_an_empty_candidate_is_never_accepted():
    tools = Predicate(fails=lambda text: True)
    assert still_fails(tools, "   \n", Verdict.WA.value) is None


# ---------------------------------------------------------------------------
# the shrinker on a synthetic failing predicate


def test_it_shrinks_to_the_smallest_input_the_predicate_still_fails_on():
    # Fails whenever any number is 5 or more. The smallest such input is "5".
    tools = Predicate(fails=lambda text: any(int(t) >= 5 for t in text.split()))
    outcome = shrink(tools, start("9 40 7 12\n"), Budget(shrink_seconds=20))
    assert outcome.counterexample.input_text.split() == ["5"]
    assert outcome.stable


def test_a_number_is_binary_searched_down_to_the_smallest_failing_value():
    # Fails whenever any number is 6 or more, starting far above it. Six is
    # the answer, and the cheap way there is to close the interval rather
    # than step down.
    tools = Predicate(fails=lambda text: any(int(t) >= 6 for t in text.split()))
    outcome = shrink(tools, start("900 123 77\n"), Budget(shrink_seconds=20))
    assert outcome.counterexample.input_text.split() == ["6"]
    assert outcome.stable
    assert any("binary search" in step.action for step in outcome.steps)
    # Halves and decrements need hundreds of evaluations to walk 900 down to
    # 6, and on a real problem every evaluation is up to four processes.
    assert len(tools.judged) < 150


def test_it_keeps_a_counted_input_valid_while_shrinking_it():
    # Only the compound move can shrink this: deleting a number alone leaves
    # the count wrong, and the validator refuses it.
    tools = Predicate(
        fails=lambda text: any(int(t) >= 4 for t in text.splitlines()[1].split()),
        valid=counted,
    )
    outcome = shrink(tools, start("4\n9 8 7 6\n"), Budget(shrink_seconds=20))
    assert outcome.counterexample.input_text == "1\n4\n"
    assert counted(outcome.counterexample.input_text)
    actions = [step.action for step in outcome.steps]
    assert "lowered a count and dropped the last number it counted" in actions


def test_every_accepted_step_is_recorded_in_order():
    tools = Predicate(fails=lambda text: any(int(t) >= 5 for t in text.split()))
    outcome = shrink(tools, start("9 40 7\n"), Budget(shrink_seconds=20))
    steps = outcome.steps
    assert steps[0].action == "found"
    assert steps[0].input_text == "9 40 7\n"
    assert [step.step for step in steps] == list(range(len(steps)))
    # Each row is strictly smaller than the one before it.
    for earlier, later in zip(steps, steps[1:]):
        assert weight(later.input_text) < weight(earlier.input_text)
    assert steps[-1].input_text == outcome.counterexample.input_text
    assert outcome.start_tokens == 3
    assert outcome.final_tokens == 1


def test_an_input_that_cannot_shrink_records_only_the_row_it_started_from():
    tools = Predicate(fails=lambda text: text.split() == ["1"])
    outcome = shrink(tools, start("1\n"), Budget(shrink_seconds=5))
    assert len(outcome.steps) == 1
    assert outcome.steps[0].action == "found"
    assert outcome.counterexample.input_text == "1\n"


def test_a_failure_that_does_not_repeat_is_reported_as_unstable():
    seen: list[int] = []

    def flaky(text: str) -> bool:
        seen.append(1)
        return len(seen) <= 1

    outcome = shrink(Predicate(fails=flaky), start("7\n"), Budget(shrink_seconds=5))
    assert not outcome.stable


def test_the_time_box_is_respected():
    tools = Predicate(fails=lambda text: any(int(t) >= 2 for t in text.split()))
    outcome = shrink(tools, start(" ".join(["900"] * 60) + "\n"), Budget(shrink_seconds=0))
    assert outcome.elapsed < 5.0
    assert outcome.steps[0].action == "found"


# ---------------------------------------------------------------------------
# end to end, with real scripts


COIN_VALIDATOR = """
import sys
data = sys.stdin.read().split()
if len(data) < 2:
    sys.exit(1)
try:
    values = [int(x) for x in data]
except ValueError:
    sys.exit(1)
n, amount = values[0], values[1]
coins = values[2:]
if not (1 <= n <= 12 and 1 <= amount <= 200):
    sys.exit(1)
if len(coins) != n or coins[0] != 1:
    sys.exit(1)
if any(coins[i] >= coins[i + 1] for i in range(n - 1)):
    sys.exit(1)
sys.exit(0 if coins[-1] <= 200 else 1)
"""

COIN_GENERATOR = """
import random
import sys
rng = random.Random(int(sys.argv[1]))
size = int(sys.argv[2])
mode = sys.argv[3]
top = 8 if mode == "tiny_values" else (200 if mode == "extreme" else 40)
choices = list(range(2, top + 1))
rng.shuffle(choices)
n = max(1, min(12, size, len(choices) + 1))
coins = sorted([1] + choices[: n - 1])
amount = rng.randint(1, min(200, max(1, coins[-1] * 2)))
print(len(coins), amount)
print(" ".join(str(c) for c in coins))
"""

COIN_ORACLE = """
import sys
data = sys.stdin.read().split()
n, amount = int(data[0]), int(data[1])
coins = [int(x) for x in data[2 : 2 + n]]
best = [0] + [10 ** 9] * amount
for total in range(1, amount + 1):
    for coin in coins:
        if coin <= total and best[total - coin] + 1 < best[total]:
            best[total] = best[total - coin] + 1
print(best[amount])
"""

COIN_GREEDY = """
import sys
data = sys.stdin.read().split()
n, amount = int(data[0]), int(data[1])
coins = [int(x) for x in data[2 : 2 + n]]
used = 0
for coin in sorted(coins, reverse=True):
    used += amount // coin
    amount %= coin
print(used)
"""


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


def test_stress_then_shrink_on_a_wrong_greedy_solution():
    with workspace() as directory:
        tools = coin_tools(directory)
        outcome = run_stress(tools, Budget(max_tests=300, max_seconds=60))
        assert outcome.failure is not None, "the greedy solution should be refuted"
        shrunk = shrink(tools, outcome.failure, Budget(shrink_seconds=25))

        found = shrunk.counterexample
        # The result is a real, valid, still-failing input, not a leftover.
        assert tools.validate(found.input_text)
        assert tools.judge(found.input_text).verdict is Verdict.WA
        assert shrunk.stable
        assert found.expected.strip() != found.actual.strip()

    assert shrunk.final_tokens <= shrunk.start_tokens
    assert shrunk.steps[0].action == "found"
    assert shrunk.steps[-1].input_text == found.input_text
    # Three coins and an amount is the smallest shape a greedy failure needs.
    numbers = found.input_text.split()
    assert len(numbers) >= 5
    assert int(numbers[0]) == len(numbers) - 2


def test_the_shrunk_input_is_smaller_than_what_stress_found():
    with workspace() as directory:
        tools = coin_tools(directory)
        big = None
        for seed in range(400):
            text = tools.generate(seed, 12, "normal")
            if not text or not tools.validate(text):
                continue
            if tools.judge(text).verdict is Verdict.WA:
                big = text
                break
        assert big is not None, "no failing input was generated at size 12"
        shrunk = shrink(tools, start(big), Budget(shrink_seconds=25))
    assert weight(shrunk.counterexample.input_text) < weight(big)
    assert len(shrunk.steps) >= 2
