"""Runs Refute against the planted bugs in bench/problems and writes up what happened.

Refute is run blind: it is handed the statement and the bug, and never
ref.py or truth_gen.py, which exist only so bench/verify.py can show that
each bug really is a bug. Tooling is written once per problem from the
statement alone and then reused for every bug of that problem, because that
is what the tooling depends on.

Every finished pair is appended to bench/results.json, and bench/RESULTS.md
is rewritten from that file after every problem. A run that is interrupted
leaves both readable, and --resume carries on from the pairs already there.
"""

from __future__ import annotations

import json
import re
import shutil
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import sandbox
from .llm import Client, LLMError
from .models import Budget, Limits
from .shrink import shrink
from .stress import Tools, run_stress
from .synth import SynthError, Tooling, synthesize

#: Smaller than a `refute run`, because the benchmark is eighteen of them.
#: RESULTS.md states these numbers, since they bound what it can find.
BUDGET = Budget(max_tests=300, max_seconds=45.0, shrink_seconds=15.0)
LIMITS = Limits()

_KIND = re.compile(r"^#\s*kind:\s*(.+)$", re.MULTILINE)


@dataclass
class Pair:
    """What happened when Refute was pointed at one bug."""

    problem: str
    bug: str
    kind: str
    found: bool
    verdict: str = ""
    tests: int = 0
    input_tokens: int = 0
    largest_tokens: int = 0
    set_aside: int = 0
    seconds: float = 0.0
    note: str = ""

    @property
    def reason(self) -> str:
        """Why this bug was not found, in the facts the run has."""
        if self.found:
            return ""
        if self.note and not self.note.startswith("no failing input"):
            return self.note
        text = (
            str(self.tests)
            + " tests on inputs of up to "
            + str(self.largest_tokens)
            + " tokens all matched both reference solutions, so the budget ran"
            " out before a difference turned up"
        )
        if self.set_aside:
            text += (
                ", and "
                + str(self.set_aside)
                + " of those tests were set aside rather than counted against it"
            )
        return text


@dataclass
class Figures:
    """What the tooling for one problem was worth, and what it cost."""

    problem: str
    samples: int = 0
    dropped_samples: int = 0
    repairs: int = 0
    weak_validator: bool = False
    calibration: str = ""
    smoke: str = ""
    oracle_checks: int = 0
    oracle_agreements: int = 0
    model_calls: int = 0
    model_tokens: int = 0
    seconds: float = 0.0
    note: str = ""

    @property
    def agreement(self) -> str:
        if self.oracle_checks == 0:
            return "none compared"
        return (
            str(self.oracle_agreements) + " of " + str(self.oracle_checks) + " compared"
        )


@dataclass
class Record:
    """Everything the benchmark has learned so far."""

    pairs: list[Pair] = field(default_factory=list)
    problems: list[Figures] = field(default_factory=list)
    budget: dict[str, float] = field(default_factory=dict)
    started_at: float = 0.0

    def done(self) -> set[tuple[str, str]]:
        return {(pair.problem, pair.bug) for pair in self.pairs}

    def to_json(self) -> str:
        return json.dumps(
            {
                "started_at": self.started_at,
                "budget": self.budget,
                "pairs": [asdict(pair) for pair in self.pairs],
                "problems": [asdict(figure) for figure in self.problems],
            },
            indent=2,
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, text: str) -> "Record":
        data = json.loads(text)
        return cls(
            pairs=[Pair(**row) for row in data.get("pairs", [])],
            problems=[Figures(**row) for row in data.get("problems", [])],
            budget=data.get("budget", {}),
            started_at=data.get("started_at", 0.0),
        )


# ---------------------------------------------------------------------------
# the data on disk


@dataclass
class Problem:
    slug: str
    directory: Path
    statement: str
    bugs: list[Path]


def load_problems(root: Path) -> list[Problem]:
    problems: list[Problem] = []
    for directory in sorted((root / "problems").iterdir()):
        if not directory.is_dir() or not (directory / "statement.md").is_file():
            continue
        problems.append(
            Problem(
                slug=directory.name,
                directory=directory,
                statement=(directory / "statement.md").read_text(encoding="utf-8"),
                bugs=sorted((directory / "bugs").glob("*.py")),
            )
        )
    return problems


def kind_of(bug: Path) -> str:
    found = _KIND.search(bug.read_text(encoding="utf-8"))
    return found.group(1).strip() if found else "unlabelled"


# ---------------------------------------------------------------------------
# running one problem


def run_bug(
    tooling: Tooling, workdir: Path, bug: Path, budget: Budget
) -> tuple[Pair, int, int]:
    """Stress and shrink one bug against the problem's shared tooling.

    Returns the result plus how many times the two reference solutions were
    compared and agreed, which the caller adds up over the whole problem.
    """
    started = time.monotonic()
    target = workdir / "target.py"
    shutil.copyfile(bug, target)
    tools = Tools(
        workdir=workdir,
        generator=tooling.paths["generator"],
        validator=tooling.paths["validator"],
        oracle_a=tooling.paths["oracle_a"],
        oracle_b=tooling.paths.get("oracle_b"),
        target=sandbox.command_for(target),
        limits=LIMITS,
    )
    outcome = run_stress(tools, budget)
    pair = Pair(
        problem=bug.parent.parent.name,
        bug=bug.stem,
        kind=kind_of(bug),
        found=outcome.failure is not None,
        tests=len(outcome.tests),
        largest_tokens=outcome.largest_tokens,
        set_aside=sum(outcome.skipped.values()),
    )
    if outcome.failure is None:
        pair.seconds = round(time.monotonic() - started, 1)
        pair.note = "no failing input inside the budget"
        return pair, outcome.oracle_checks, outcome.oracle_agreements

    pair.verdict = outcome.failure.verdict
    shrunk = shrink(tools, outcome.failure, budget)
    pair.input_tokens = len(shrunk.counterexample.input_text.split())
    pair.seconds = round(time.monotonic() - started, 1)
    if not shrunk.stable:
        pair.note = "the input did not fail every time it was re-run"
    return pair, outcome.oracle_checks, outcome.oracle_agreements


def run_problem(
    problem: Problem,
    client: Client,
    record: Record,
    budget: Budget,
    on_step: Callable[[str, str], None],
    save: Callable[[], None],
) -> bool:
    """Write the tooling once, then point it at every bug of this problem.

    `save` is called after every bug, so an interrupted run loses at most the
    bug it was on. Returns whether anything was run.
    """
    already = record.done()
    pending = [bug for bug in problem.bugs if (problem.slug, bug.stem) not in already]
    if not pending:
        on_step(problem.slug, "already done")
        return False

    started = time.monotonic()
    calls_before = client.calls + client.cached
    tokens_before = client.tokens
    figures = Figures(problem=problem.slug)

    with sandbox.workspace("refute-bench-") as workdir:
        try:
            on_step(problem.slug, "writing the tooling")
            tooling = synthesize(
                client,
                problem.statement,
                workdir,
                limits=LIMITS,
                on_step=lambda message: on_step(problem.slug, message),
            )
        except (SynthError, LLMError) as exc:
            figures.note = "no tooling: " + str(exc)
            figures.model_calls = client.calls + client.cached - calls_before
            figures.model_tokens = client.tokens - tokens_before
            figures.seconds = round(time.monotonic() - started, 1)
            record.problems = [f for f in record.problems if f.problem != problem.slug]
            record.problems.append(figures)
            for bug in pending:
                record.pairs.append(
                    Pair(
                        problem=problem.slug,
                        bug=bug.stem,
                        kind=kind_of(bug),
                        found=False,
                        note="the tooling could not be written",
                    )
                )
            save()
            return True

        figures.samples = len(tooling.samples)
        figures.dropped_samples = tooling.dropped_samples
        figures.repairs = tooling.repairs
        figures.weak_validator = tooling.calibration.weak
        figures.calibration = tooling.calibration.summary
        figures.smoke = (
            str(tooling.smoke.accepted) + " of " + str(tooling.smoke.runs)
        )

        for bug in pending:
            on_step(problem.slug, bug.stem)
            pair, checks, agreements = run_bug(tooling, workdir, bug, budget)
            # The two references are compared on every tenth test, so the
            # figure for a problem is what its bugs saw between them.
            figures.oracle_checks += checks
            figures.oracle_agreements += agreements
            record.pairs.append(pair)
            save()

    figures.model_calls = client.calls + client.cached - calls_before
    figures.model_tokens = client.tokens - tokens_before
    figures.seconds = round(time.monotonic() - started, 1)
    record.problems = [f for f in record.problems if f.problem != problem.slug]
    record.problems.append(figures)
    save()
    return True


# ---------------------------------------------------------------------------
# the write-up


PREAMBLE = (
    "Refute was handed each statement and each planted bug and nothing else.",
    "It never saw ref.py or truth_gen.py; those exist so bench/verify.py can"
    " show that every bug really is one.",
    "Every bug reproduces the examples in its statement by construction, so"
    " none of them can be caught by reading the statement.",
    "The bugs were written by the coding agent that built Refute. They are"
    " not collected from real submissions, and a benchmark marking its own"
    " homework is weaker evidence than one that does not.",
    "The tooling for a problem is written once from the statement and reused"
    " for every bug of that problem, so the model calls below are per problem,"
    " not per bug.",
    "Model replies are cached by a hash of the prompt. A second run of this"
    " benchmark reads the tooling back from that cache, so the calls and"
    " tokens below are what writing the tooling cost, not what the run that"
    " produced this file spent.",
)


def _median(values: list[float]) -> str:
    if not values:
        return "none"
    middle = statistics.median(values)
    return format(middle, ".0f") if middle >= 10 else format(middle, ".1f")


def write_results(record: Record, path: Path) -> None:
    """Rewrite RESULTS.md from the record. Safe to call after every problem."""
    pairs = sorted(record.pairs, key=lambda pair: (pair.problem, pair.bug))
    found = [pair for pair in pairs if pair.found]
    missed = [pair for pair in pairs if not pair.found]

    lines: list[str] = ["# Benchmark results", ""]
    lines.append(
        "Found "
        + str(len(found))
        + " of "
        + str(len(pairs))
        + " planted bugs."
        + ("" if len(pairs) else " Nothing has run yet.")
    )
    lines.append("")
    if len(record.problems) and len(pairs):
        lines.append(
            "This run used a budget of "
            + str(int(record.budget.get("max_tests", 0)))
            + " tests or "
            + str(int(record.budget.get("max_seconds", 0)))
            + " seconds per bug, with "
            + str(int(record.budget.get("shrink_seconds", 0)))
            + " seconds to shrink. A larger budget would find more."
        )
        lines.append("")
    for sentence in PREAMBLE:
        lines.append("- " + sentence)
    lines.append("")

    lines.append("## Per bug")
    lines.append("")
    lines.append(
        "| problem | bug | kind | found | verdict | tests | input | seconds |"
    )
    lines.append("|---|---|---|---|--:|--:|--:|--:|")
    for pair in pairs:
        lines.append(
            "| "
            + " | ".join(
                [
                    pair.problem,
                    pair.bug.replace("_", " "),
                    pair.kind,
                    "yes" if pair.found else "no",
                    pair.verdict or "-",
                    str(pair.tests),
                    str(pair.input_tokens) if pair.found else "-",
                    format(pair.seconds, ".1f"),
                ]
            )
            + " |"
        )
    lines.append("")
    lines.append(
        "Tests is how many ran before the first confirmed failure. Input is"
        " the size of the smallest failing input Refute kept, counted in"
        " whitespace-separated tokens, so a word counts as one."
    )
    lines.append("")

    lines.append("## Per problem")
    lines.append("")
    lines.append(
        "| problem | samples | repairs | oracles agree | validator | generated"
        " valid | model calls | tokens | seconds |"
    )
    lines.append("|---|--:|--:|---|---|---|--:|--:|--:|")
    for figure in sorted(record.problems, key=lambda row: row.problem):
        lines.append(
            "| "
            + " | ".join(
                [
                    figure.problem,
                    str(figure.samples)
                    + (
                        " (" + str(figure.dropped_samples) + " dropped)"
                        if figure.dropped_samples
                        else ""
                    ),
                    str(figure.repairs),
                    figure.agreement,
                    figure.calibration or "-",
                    figure.smoke or "-",
                    str(figure.model_calls),
                    str(figure.model_tokens),
                    format(figure.seconds, ".0f"),
                ]
            )
            + " |"
        )
    lines.append("")
    weak = [figure.problem for figure in record.problems if figure.weak_validator]
    if weak:
        lines.append(
            "The validator stayed weak for "
            + ", ".join(sorted(weak))
            + ", so an invalid generated input could have been tested there as"
            " if it were valid."
        )
        lines.append("")

    lines.append("## Totals")
    lines.append("")
    lines.append("- Found " + str(len(found)) + " of " + str(len(pairs)) + ".")
    if found:
        lines.append(
            "- Median tests to the first confirmed failure: "
            + _median([float(pair.tests) for pair in found])
            + "."
        )
        lines.append(
            "- Median smallest input: "
            + _median([float(pair.input_tokens) for pair in found])
            + " tokens."
        )
        lines.append(
            "- Median seconds per bug found: "
            + _median([pair.seconds for pair in found])
            + "."
        )
    verdicts: dict[str, int] = {}
    for pair in found:
        verdicts[pair.verdict] = verdicts.get(pair.verdict, 0) + 1
    if verdicts:
        lines.append(
            "- Verdicts: "
            + ", ".join(
                name + " " + str(count) for name, count in sorted(verdicts.items())
            )
            + "."
        )
    kinds: dict[str, list[Pair]] = {}
    for pair in pairs:
        kinds.setdefault(pair.kind, []).append(pair)
    lines.append(
        "- By kind: "
        + ", ".join(
            name
            + " "
            + str(sum(1 for pair in rows if pair.found))
            + " of "
            + str(len(rows))
            for name, rows in sorted(kinds.items())
        )
        + "."
    )
    lines.append(
        "- Model calls in total: "
        + str(sum(figure.model_calls for figure in record.problems))
        + "."
    )
    lines.append("")

    lines.append("## Misses")
    lines.append("")
    if not missed:
        lines.append("Every planted bug was found.")
    else:
        lines.append(
            "Each of these is a bug that is really there, which bench/verify.py"
            " shows, and that this run did not reach."
        )
        lines.append("")
        for pair in missed:
            lines.append(
                "- **"
                + pair.problem
                + " / "
                + pair.bug.replace("_", " ")
                + "** ("
                + pair.kind
                + "): "
                + pair.reason
                + "."
            )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# the whole benchmark


def run_bench(
    root: Path,
    client: Client,
    resume: bool = False,
    budget: Budget | None = None,
    on_step: Callable[[str, str], None] = lambda slug, message: None,
) -> Record:
    """Run every problem, writing the results out as each one finishes."""
    budget = budget or BUDGET
    json_path = root / "results.json"
    md_path = root / "RESULTS.md"

    record = Record()
    if resume and json_path.is_file():
        record = Record.from_json(json_path.read_text(encoding="utf-8"))
    if not record.started_at:
        record.started_at = time.time()
    record.budget = {
        "max_tests": budget.max_tests,
        "max_seconds": budget.max_seconds,
        "shrink_seconds": budget.shrink_seconds,
    }

    def save() -> None:
        root.mkdir(parents=True, exist_ok=True)
        json_path.write_text(record.to_json(), encoding="utf-8")

    # Written up front so an interrupted first problem still leaves a file
    # that says what the run was.
    save()
    write_results(record, md_path)

    for problem in load_problems(root):
        run_problem(problem, client, record, budget, on_step, save)
        # After every problem, so a partial run is still usable.
        write_results(record, md_path)
    return record
