"""The benchmark: the data on disk, the runner, resuming, and the write-up.

The runner is driven over a small benchmark of one problem built in a
temporary directory, with a FakeLLM for the tooling. The real six problems
are checked for shape here and by bench/verify.py, which runs them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from refute import bench as runner
from refute.llm import FakeLLM
from refute.models import Budget, Verdict

ROOT = Path(__file__).resolve().parent.parent
PROBLEMS = ROOT / "bench" / "problems"
KINDS = {
    "wrong greedy",
    "off-by-one",
    "bad initialization",
    "missed edge case",
    "non-termination",
    "wrong algorithm",
}
REQUIRED = KINDS - {"wrong algorithm"}


# ---------------------------------------------------------------------------
# the benchmark data


def test_there_are_six_problems_each_with_three_bugs():
    problems = runner.load_problems(ROOT / "bench")
    assert len(problems) == 6
    for problem in problems:
        assert len(problem.bugs) >= 3, problem.slug
        assert (problem.directory / "ref.py").is_file(), problem.slug
        assert (problem.directory / "truth_gen.py").is_file(), problem.slug
        assert problem.statement.strip()


def test_the_six_categories_are_all_covered():
    slugs = {problem.slug for problem in runner.load_problems(ROOT / "bench")}
    assert slugs == {
        "distinct-stretch",
        "fewest-hops",
        "max-subarray",
        "pick-talks",
        "ship-the-boxes",
        "split-the-load",
    }


def test_every_bug_says_what_kind_of_bug_it_is():
    for problem in runner.load_problems(ROOT / "bench"):
        for bug in problem.bugs:
            kind = runner.kind_of(bug)
            assert kind in KINDS, problem.slug + "/" + bug.stem + ": " + kind


def test_every_kind_the_brief_asks_for_appears():
    kinds = {
        runner.kind_of(bug)
        for problem in runner.load_problems(ROOT / "bench")
        for bug in problem.bugs
    }
    assert REQUIRED <= kinds


def test_at_least_two_problems_have_a_wrong_greedy_as_the_bug():
    greedy = {
        problem.slug
        for problem in runner.load_problems(ROOT / "bench")
        for bug in problem.bugs
        if runner.kind_of(bug) == "wrong greedy"
    }
    assert len(greedy) >= 2, greedy


def test_no_statement_gives_the_answer_away():
    # A statement that named its own algorithm would be testing the model's
    # reading, not its reasoning.
    for problem in runner.load_problems(ROOT / "bench"):
        lowered = problem.statement.lower()
        for word in ("dynamic programming", "binary search", "breadth-first", "greedy"):
            assert word not in lowered, problem.slug + " mentions " + word


def test_the_bugs_are_never_handed_the_reference():
    # Refute is run blind. Nothing under bugs/ may mention the files that
    # exist only to verify the data.
    for problem in runner.load_problems(ROOT / "bench"):
        for bug in problem.bugs:
            source = bug.read_text(encoding="utf-8")
            assert "ref.py" not in source
            assert "truth_gen" not in source


# ---------------------------------------------------------------------------
# a small benchmark of one problem, to drive the runner


STATEMENT = """
# Sum a list

The first line holds one integer n. The second line holds n integers.
Print their sum.

Constraints: 1 <= n <= 30 and 0 <= each value <= 100.

## Examples

Input:

```
3
1 2 3
```

Output:

```
6
```

Input:

```
1
7
```

Output:

```
7
```
"""

ORACLE = """
import sys
data = sys.stdin.read().split()
n = int(data[0])
print(sum(int(x) for x in data[1 : 1 + n]))
"""

VALIDATOR = """
import sys
data = sys.stdin.read().split()
try:
    values = [int(x) for x in data]
except ValueError:
    sys.exit(1)
if not values:
    sys.exit(1)
n = values[0]
if not 1 <= n <= 30 or len(values) != n + 1:
    sys.exit(1)
for value in values[1:]:
    if not 0 <= value <= 100:
        sys.exit(1)
sys.exit(0)
"""

GENERATOR = """
import random
import sys
rng = random.Random(int(sys.argv[1]))
size = int(sys.argv[2])
mode = sys.argv[3]
n = max(1, min(30, size))
top = 2 if mode == "tiny_values" else (100 if mode == "extreme" else 40)
print(n)
print(" ".join(str(rng.randint(0, top)) for _ in range(n)))
"""

DROPS_LAST = """
# kind: off-by-one
import sys
data = sys.stdin.read().split()
n = int(data[0])
print(sum(int(x) for x in data[1:n]))
"""

CORRECT = """
# kind: off-by-one
import sys
data = sys.stdin.read().split()
n = int(data[0])
print(sum(int(x) for x in data[1 : 1 + n]))
"""


def fenced(code: str) -> str:
    return "```python\n" + code.strip() + "\n```\n"


def tiny_bench(root: Path, bugs: dict[str, str]) -> Path:
    directory = root / "problems" / "sum-a-list"
    (directory / "bugs").mkdir(parents=True)
    (directory / "statement.md").write_text(STATEMENT, encoding="utf-8")
    (directory / "ref.py").write_text(ORACLE, encoding="utf-8")
    (directory / "truth_gen.py").write_text(GENERATOR, encoding="utf-8")
    for name, source in bugs.items():
        (directory / "bugs" / (name + ".py")).write_text(source, encoding="utf-8")
    return root


def model() -> FakeLLM:
    return FakeLLM(
        {
            "samples": '```json\n[{"input": "3\\n1 2 3", "output": "6"},'
            ' {"input": "1\\n7", "output": "7"}]\n```',
            "oracle_a": fenced(ORACLE),
            "oracle_b": fenced(ORACLE),
            "validator": fenced(VALIDATOR),
            "generator": fenced(GENERATOR),
        }
    )


SMALL = Budget(max_tests=60, max_seconds=30.0, shrink_seconds=6.0)


# ---------------------------------------------------------------------------
# the runner


def test_a_planted_bug_is_found_and_written_up(tmp_path):
    root = tiny_bench(tmp_path / "bench", {"drops_last": DROPS_LAST})
    record = runner.run_bench(root, model(), budget=SMALL)

    assert len(record.pairs) == 1
    pair = record.pairs[0]
    assert pair.problem == "sum-a-list"
    assert pair.bug == "drops_last"
    assert pair.kind == "off-by-one"
    assert pair.found
    assert pair.verdict == Verdict.WA.value
    assert pair.tests >= 1
    assert pair.input_tokens >= 2

    assert len(record.problems) == 1
    figures = record.problems[0]
    assert figures.samples == 2
    assert figures.model_calls == 5
    assert "of" in figures.smoke

    text = (root / "RESULTS.md").read_text(encoding="utf-8")
    assert "Found 1 of 1 planted bugs." in text
    assert "| sum-a-list | drops last | off-by-one | yes |" in text
    assert "Every planted bug was found." in text


def test_a_bug_that_is_not_really_a_bug_is_reported_as_a_miss(tmp_path):
    root = tiny_bench(tmp_path / "bench", {"not_a_bug": CORRECT})
    record = runner.run_bench(root, model(), budget=SMALL)

    pair = record.pairs[0]
    assert not pair.found
    assert pair.note == "no failing input inside the budget"
    assert pair.tests == SMALL.max_tests
    assert pair.largest_tokens > 0
    assert "the budget ran out" in pair.reason

    text = (root / "RESULTS.md").read_text(encoding="utf-8")
    assert "Found 0 of 1 planted bugs." in text
    assert "## Misses" in text
    assert "not a bug" in text
    assert "Every planted bug was found." not in text


def test_the_tooling_is_written_once_and_reused_for_every_bug(tmp_path):
    root = tiny_bench(
        tmp_path / "bench", {"drops_last": DROPS_LAST, "also_drops": DROPS_LAST}
    )
    llm = model()
    record = runner.run_bench(root, llm, budget=SMALL)

    assert len(record.pairs) == 2
    assert all(pair.found for pair in record.pairs)
    # One samples call and four tooling calls, for two bugs.
    assert [purpose for purpose, _ in llm.prompts] == [
        "samples",
        "oracle_a",
        "oracle_b",
        "validator",
        "generator",
    ]
    assert record.problems[0].model_calls == 5


def test_the_write_up_is_rewritten_after_every_problem(tmp_path):
    root = tmp_path / "bench"
    for slug in ("a-problem", "b-problem"):
        directory = root / "problems" / slug
        (directory / "bugs").mkdir(parents=True)
        (directory / "statement.md").write_text(STATEMENT, encoding="utf-8")
        (directory / "ref.py").write_text(ORACLE, encoding="utf-8")
        (directory / "truth_gen.py").write_text(GENERATOR, encoding="utf-8")
        (directory / "bugs" / "drops_last.py").write_text(DROPS_LAST, encoding="utf-8")

    seen: list[str] = []
    real_write = runner.write_results

    def watch(record, path):
        real_write(record, path)
        seen.append(path.read_text(encoding="utf-8"))

    import refute.bench

    original = refute.bench.write_results
    refute.bench.write_results = watch
    try:
        runner.run_bench(root, model(), budget=SMALL)
    finally:
        refute.bench.write_results = original

    # Once before anything ran, then once per problem.
    assert len(seen) == 3
    assert "Found 0 of 0 planted bugs." in seen[0]
    assert "Found 1 of 1 planted bugs." in seen[1]
    assert "Found 2 of 2 planted bugs." in seen[2]


def test_an_empty_run_still_writes_a_readable_file(tmp_path):
    root = tmp_path / "bench"
    (root / "problems").mkdir(parents=True)
    runner.run_bench(root, model(), budget=SMALL)
    text = (root / "RESULTS.md").read_text(encoding="utf-8")
    assert "Found 0 of 0 planted bugs." in text
    assert "Nothing has run yet." in text
    assert json.loads((root / "results.json").read_text(encoding="utf-8"))["pairs"] == []


# ---------------------------------------------------------------------------
# resuming


def test_resume_skips_the_pairs_already_recorded(tmp_path):
    root = tiny_bench(
        tmp_path / "bench", {"drops_last": DROPS_LAST, "also_drops": DROPS_LAST}
    )
    first = runner.run_bench(root, model(), budget=SMALL)
    assert len(first.pairs) == 2

    second_model = model()
    second = runner.run_bench(root, second_model, resume=True, budget=SMALL)
    assert len(second.pairs) == 2
    assert second_model.calls == 0, "resuming must not write the tooling again"
    assert {pair.bug for pair in second.pairs} == {"drops_last", "also_drops"}


def test_without_resume_the_record_starts_again(tmp_path):
    root = tiny_bench(tmp_path / "bench", {"drops_last": DROPS_LAST})
    runner.run_bench(root, model(), budget=SMALL)
    again = model()
    runner.run_bench(root, again, budget=SMALL)
    assert again.calls == 5


def test_resume_finishes_a_run_that_stopped_half_way(tmp_path):
    root = tiny_bench(
        tmp_path / "bench", {"drops_last": DROPS_LAST, "also_drops": DROPS_LAST}
    )
    # A record with one pair done, as an interrupted run would leave.
    half = runner.Record(
        pairs=[
            runner.Pair(
                problem="sum-a-list",
                bug="also_drops",
                kind="off-by-one",
                found=True,
                verdict=Verdict.WA.value,
                tests=4,
                input_tokens=3,
                seconds=1.0,
            )
        ],
        started_at=1.0,
    )
    (root / "results.json").write_text(half.to_json(), encoding="utf-8")

    record = runner.run_bench(root, model(), resume=True, budget=SMALL)
    assert {pair.bug for pair in record.pairs} == {"also_drops", "drops_last"}
    assert len(record.pairs) == 2
    # The pair that was already there was not run again.
    kept = next(pair for pair in record.pairs if pair.bug == "also_drops")
    assert kept.tests == 4


def test_the_record_survives_a_round_trip_through_json():
    record = runner.Record(
        pairs=[runner.Pair("p", "b", "off-by-one", True, "WA", 7, 3, seconds=1.5)],
        problems=[runner.Figures("p", samples=2, model_calls=5)],
        budget={"max_tests": 300.0},
        started_at=12.0,
    )
    again = runner.Record.from_json(record.to_json())
    assert again == record
    assert again.done() == {("p", "b")}


# ---------------------------------------------------------------------------
# the write-up


def sample_record() -> runner.Record:
    return runner.Record(
        pairs=[
            runner.Pair("alpha", "one", "off-by-one", True, "WA", 12, 4, seconds=3.5),
            runner.Pair("alpha", "two", "wrong greedy", True, "WA", 30, 6, seconds=9.0),
            runner.Pair(
                "beta", "three", "non-termination", True, "TLE", 8, 3, seconds=2.0
            ),
            runner.Pair(
                "beta",
                "four",
                "bad initialization",
                False,
                "",
                300,
                0,
                largest_tokens=31,
                set_aside=4,
                seconds=44.0,
                note="no failing input inside the budget",
            ),
        ],
        problems=[
            runner.Figures(
                "alpha",
                samples=2,
                repairs=1,
                calibration="6 of 6 rejected",
                smoke="75 of 75",
                oracle_checks=9,
                oracle_agreements=9,
                model_calls=5,
                model_tokens=4000,
                seconds=40.0,
            ),
            runner.Figures(
                "beta",
                samples=3,
                dropped_samples=1,
                weak_validator=True,
                calibration="weak: accepted every broken copy",
                smoke="70 of 75",
                oracle_checks=10,
                oracle_agreements=8,
                model_calls=8,
                model_tokens=9000,
                seconds=55.0,
            ),
        ],
        budget={"max_tests": 300, "max_seconds": 45, "shrink_seconds": 15},
    )


def test_the_write_up_states_what_the_numbers_mean(tmp_path):
    path = tmp_path / "RESULTS.md"
    runner.write_results(sample_record(), path)
    text = path.read_text(encoding="utf-8")

    assert "Found 3 of 4 planted bugs." in text
    assert "300 tests or 45 seconds per bug" in text
    # The honesty the brief asks for, in the document itself.
    assert "reproduces the examples in its statement by construction" in text
    assert "written by the coding agent" in text
    assert "not collected from real submissions" in text
    assert "reused for every bug of that problem" in text
    assert "never saw ref.py" in text


def test_the_write_up_has_a_row_per_bug_and_per_problem(tmp_path):
    path = tmp_path / "RESULTS.md"
    runner.write_results(sample_record(), path)
    text = path.read_text(encoding="utf-8")
    assert "| alpha | one | off-by-one | yes | WA | 12 | 4 | 3.5 |" in text
    assert "| beta | four | bad initialization | no | - | 300 | - | 44.0 |" in text
    assert "| alpha | 2 | 1 | 9 of 9 compared | 6 of 6 rejected | 75 of 75 | 5 | 4000 | 40 |" in text
    assert "3 (1 dropped)" in text
    assert "8 of 10 compared" in text


def test_the_write_up_reports_the_medians_and_the_kinds(tmp_path):
    path = tmp_path / "RESULTS.md"
    runner.write_results(sample_record(), path)
    text = path.read_text(encoding="utf-8")
    assert "- Found 3 of 4." in text
    assert "Median tests to the first confirmed failure: 12." in text
    assert "Median smallest input: 4.0 tokens." in text
    assert "Verdicts: TLE 1, WA 2." in text
    assert "bad initialization 0 of 1" in text
    assert "non-termination 1 of 1" in text
    assert "Model calls in total: 13." in text


def test_the_write_up_lists_the_misses_with_a_reason(tmp_path):
    path = tmp_path / "RESULTS.md"
    runner.write_results(sample_record(), path)
    text = path.read_text(encoding="utf-8")
    misses = text.split("## Misses")[1]
    assert "beta / four" in misses
    assert "300 tests on inputs of up to" in misses
    assert "the budget ran out" in misses
    assert "alpha / one" not in misses


def test_a_weak_validator_is_named_in_the_write_up(tmp_path):
    path = tmp_path / "RESULTS.md"
    runner.write_results(sample_record(), path)
    text = path.read_text(encoding="utf-8")
    assert "The validator stayed weak for beta" in text


def test_the_write_up_never_claims_a_proof(tmp_path):
    path = tmp_path / "RESULTS.md"
    runner.write_results(sample_record(), path)
    lowered = path.read_text(encoding="utf-8").lower()
    assert "proves" not in lowered
    assert "proof" not in lowered
    assert "correct" not in lowered or "incorrect" in lowered


def test_a_problem_records_how_often_its_two_references_agreed(tmp_path):
    # CLAUDE.md asks for the oracle agreement per problem. It is summed over
    # the bugs of that problem, because the second reference is only consulted
    # on every tenth test.
    root = tiny_bench(tmp_path / "bench", {"drops_last": DROPS_LAST})
    record = runner.run_bench(root, model(), budget=SMALL)
    figures = record.problems[0]
    assert figures.oracle_checks > 0, "the second reference was never compared"
    assert figures.oracle_agreements == figures.oracle_checks
    assert figures.agreement.endswith(" compared")
    assert "none compared" not in (root / "RESULTS.md").read_text(encoding="utf-8")


def test_a_miss_explains_itself_from_what_the_run_saw():
    stuck = runner.Pair(
        "p", "b", "wrong greedy", False, tests=300, largest_tokens=24, set_aside=7
    )
    assert "300 tests on inputs of up to 24 tokens" in stuck.reason
    assert "the budget ran out" in stuck.reason
    assert "7 of those tests were set aside" in stuck.reason

    no_tooling = runner.Pair(
        "p", "b", "off-by-one", False, note="the tooling could not be written"
    )
    assert no_tooling.reason == "the tooling could not be written"

    assert runner.Pair("p", "b", "off-by-one", True).reason == ""
