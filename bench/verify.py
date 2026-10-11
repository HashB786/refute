"""Checks that the benchmark data is what the benchmark claims it is.

Three things have to hold for every problem, and this script runs code to
decide each one:

  - ref.py reproduces the examples in the statement.
  - every bug in bugs/ reproduces them too, so no bug can be caught by
    reading the statement alone.
  - every bug disagrees with ref.py on some input truth_gen.py produces, so
    no bug is secretly correct.

Run it from the repository root:

    python bench/verify.py

It prints a line per bug and exits nonzero if anything failed.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from refute.models import Limits
from refute.sandbox import run_python

PROBLEMS = Path(__file__).resolve().parent / "problems"
SEEDS = 200
LIMITS = Limits(oracle=10.0, target=5.0)

_EXAMPLE = re.compile(
    r"Input:\s*\n+```\n(.*?)```\s*\n+Output:\s*\n+```\n(.*?)```",
    re.DOTALL,
)
_KIND = re.compile(r"^#\s*kind:\s*(.+)$", re.MULTILINE)


@dataclass
class Example:
    index: int
    input_text: str
    output_text: str


@dataclass
class Problem:
    slug: str
    directory: Path
    statement: str
    examples: list[Example]
    bugs: list[Path] = field(default_factory=list)

    @property
    def reference(self) -> Path:
        return self.directory / "ref.py"

    @property
    def generator(self) -> Path:
        return self.directory / "truth_gen.py"


def examples_in(statement: str) -> list[Example]:
    """The examples a statement shows, read straight out of the markdown.

    The benchmark cannot ask a model for these: the whole point is to check
    the data before any model sees it.
    """
    found = []
    for index, (got_in, got_out) in enumerate(_EXAMPLE.findall(statement), start=1):
        found.append(Example(index, got_in, got_out))
    return found


def bug_kind(source: str) -> str:
    found = _KIND.search(source)
    return found.group(1).strip() if found else ""


def load() -> list[Problem]:
    problems = []
    for directory in sorted(PROBLEMS.iterdir()):
        if not directory.is_dir():
            continue
        statement = (directory / "statement.md").read_text(encoding="utf-8")
        problems.append(
            Problem(
                slug=directory.name,
                directory=directory,
                statement=statement,
                examples=examples_in(statement),
                bugs=sorted((directory / "bugs").glob("*.py")),
            )
        )
    return problems


def same(left: str, right: str) -> bool:
    return left.split() == right.split()


def answer(script: Path, text: str) -> tuple[str, str]:
    """Run a solution. Returns (output, problem) with one of them empty."""
    result = run_python(script, text, timeout=LIMITS.oracle, limits=LIMITS)
    if result.timed_out:
        return "", "ran longer than " + str(LIMITS.oracle) + " s"
    if result.exit_code != 0:
        return "", "exited with status " + str(result.exit_code)
    return result.stdout, ""


def check_examples(script: Path, problem: Problem) -> str:
    for example in problem.examples:
        got, trouble = answer(script, example.input_text)
        where = "example " + str(example.index) + ": "
        if trouble:
            return where + trouble
        if not same(got, example.output_text):
            return (
                where
                + "printed "
                + repr(" ".join(got.split())[:60])
                + " but the statement says "
                + repr(" ".join(example.output_text.split())[:60])
            )
    return ""


def disagreement(problem: Problem, bug: Path) -> tuple[str, str]:
    """Find an input where the bug and ref.py part company.

    Returns (input, how it differed), or ("", "") when none of the seeds
    separated them, which means the bug is not a bug.
    """
    for seed in range(1, SEEDS + 1):
        made = run_python(
            problem.generator, args=[str(seed)], timeout=LIMITS.target, limits=LIMITS
        )
        if made.timed_out or made.exit_code != 0 or not made.stdout.strip():
            continue
        text = made.stdout
        wanted, trouble = answer(problem.reference, text)
        if trouble:
            return text, "ref.py itself " + trouble
        got, trouble = answer(bug, text)
        if trouble:
            return text, trouble
        if not same(got, wanted):
            return text, (
                "printed " + repr(" ".join(got.split())[:40])
                + " where ref.py printed " + repr(" ".join(wanted.split())[:40])
            )
    return "", ""


def main() -> int:
    problems = load()
    if not problems:
        print("no problems found in " + str(PROBLEMS))
        return 1

    failures: list[str] = []
    bugs = 0
    for problem in problems:
        print(problem.slug)
        if len(problem.examples) < 2:
            failures.append(problem.slug + ": the statement shows fewer than 2 examples")
            print("  statement            FAILED: fewer than 2 examples")
            continue
        print("  statement            " + str(len(problem.examples)) + " examples")

        trouble = check_examples(problem.reference, problem)
        if trouble:
            failures.append(problem.slug + "/ref.py: " + trouble)
            print("  ref.py               FAILED: " + trouble)
            continue
        print("  ref.py               reproduces every example")

        if len(problem.bugs) < 3:
            failures.append(problem.slug + ": fewer than 3 bugs")
            print("  bugs                 FAILED: fewer than 3")
            continue

        for bug in problem.bugs:
            bugs += 1
            name = bug.stem
            kind = bug_kind(bug.read_text(encoding="utf-8"))
            if not kind:
                failures.append(problem.slug + "/" + name + ": no kind comment")
                print("  " + name.ljust(20) + " FAILED: no '# kind:' line")
                continue

            trouble = check_examples(bug, problem)
            if trouble:
                failures.append(
                    problem.slug + "/" + name + ": fails the examples, " + trouble
                )
                print("  " + name.ljust(20) + " FAILED the examples: " + trouble)
                continue

            text, how = disagreement(problem, bug)
            if not text:
                failures.append(
                    problem.slug
                    + "/"
                    + name
                    + ": agreed with ref.py on all "
                    + str(SEEDS)
                    + " generated inputs"
                )
                print(
                    "  "
                    + name.ljust(20)
                    + " FAILED: never disagreed with ref.py in "
                    + str(SEEDS)
                    + " tries"
                )
                continue
            if how.startswith("ref.py itself"):
                failures.append(problem.slug + ": " + how)
                print("  " + name.ljust(20) + " FAILED: " + how)
                continue

            print(
                "  "
                + name.ljust(20)
                + " passes the examples, "
                + how
                + "  ["
                + kind
                + "]"
            )
        print("")

    print(
        "checked "
        + str(len(problems))
        + " problems and "
        + str(bugs)
        + " bugs"
    )
    if failures:
        print("")
        print(str(len(failures)) + " problems to fix:")
        for line in failures:
            print("  " + line)
        return 1
    print("every bug passes its statement's examples and fails against ref.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
