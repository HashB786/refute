"""Asks the model what went wrong and for a corrected solution, then checks it.

Two calls: a diagnosis in prose, and a whole replacement solution. The
diagnosis is printed as the model's words and labelled as unchecked, because
nothing can check prose. The solution is checked the only way that means
anything: it has to pass the samples, pass the counterexample, and survive two
hundred stress tests on seeds the first run never used. If it does not, it is
labelled an unverified suggestion and the reason is given.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import guard, sandbox
from .llm import LLM
from .models import Budget, Counterexample, Limits, Sample
from .stress import Tools, run_stress, tokens_equal
from .synth import SYSTEM, IMPORT_RULE, SynthError, extract_code, normalize

MAX_SENTENCES = 4
FIX_SEED_SALT = 5_000_011

DIAGNOSE_SYSTEM = (
    "You explain why a program gives the wrong answer on one input. You are "
    "brief and concrete, and you do not apologise or pad."
)


@dataclass
class Outcome:
    """What came back from the model, and whether the fix survived being run."""

    diagnosis: str = ""
    source: str = ""
    verified: bool = False
    reason: str = ""
    notes: list[str] = field(default_factory=list)


def _trim(text: str, limit: int = MAX_SENTENCES) -> str:
    """Keep at most four sentences, since that is what the model was asked for."""
    flat = " ".join(text.split())
    pieces: list[str] = []
    start = 0
    for index, character in enumerate(flat):
        if character in ".!?" and (
            index + 1 == len(flat) or flat[index + 1] == " "
        ):
            pieces.append(flat[start : index + 1].strip())
            start = index + 1
            if len(pieces) == limit:
                break
    if len(pieces) < limit and flat[start:].strip():
        pieces.append(flat[start:].strip())
    return " ".join(piece for piece in pieces if piece)


def _facts(
    statement: str,
    target_source: str,
    found: Counterexample,
    target_name: str,
) -> str:
    """The shared part of both prompts: the problem, the code and the failure."""
    actual = found.actual.strip() or "nothing"
    return (
        "Problem statement:\n---\n"
        + statement.strip()
        + "\n---\n\nThis solution is wrong. It is called "
        + target_name
        + ":\n```python\n"
        + target_source.strip()
        + "\n```\n\nOn this input:\n```\n"
        + found.input_text.rstrip("\n")
        + "\n```\nthe right answer is:\n```\n"
        + found.expected.strip()
        + "\n```\nbut it "
        + (
            "crashed with: " + normalize(actual)[:300]
            if found.verdict == "RE"
            else (
                "ran out of time"
                if found.verdict == "TLE"
                else "printed:\n```\n" + actual + "\n```"
            )
        )
        + "\n"
    )


def diagnose(
    llm: LLM,
    statement: str,
    target_source: str,
    found: Counterexample,
    target_name: str,
) -> str:
    """Ask why it is wrong. At most four sentences, and never checked."""
    prompt = (
        _facts(statement, target_source, found, target_name)
        + "\nIn at most four sentences, say why the solution is wrong on this "
        "input and what the right approach is. Name the specific mistake, not "
        "a general category. Do not write any code and do not restate the "
        "input. Reply with the sentences and nothing else."
    )
    reply = llm.complete("diagnose", prompt, DIAGNOSE_SYSTEM)
    return _trim(reply.text.strip())


def propose(
    llm: LLM,
    statement: str,
    target_source: str,
    found: Counterexample,
    target_name: str,
) -> str:
    """Ask for a corrected whole solution. Guarded before it is ever run."""
    prompt = (
        _facts(statement, target_source, found, target_name)
        + "\nWrite the corrected solution: a complete Python 3 program, not a "
        "patch and not a fragment. It reads the input from standard input and "
        "prints the answer to standard output in exactly the format the "
        "statement asks for. Keep the shape of the original where you can, so "
        "that the change is easy to read.\n" + IMPORT_RULE
    )
    reply = llm.complete("fix", prompt, SYSTEM)
    source = extract_code(reply.text)
    refused = guard.describe(source)
    if refused:
        raise SynthError("the guard refused the suggested fix: " + refused)
    return source


def verify(
    source: str,
    tools: Tools,
    samples: list[Sample],
    found: Counterexample,
    limits: Limits | None = None,
    budget: Budget | None = None,
) -> tuple[bool, str]:
    """Run the fix against the samples, the counterexample and fresh stress tests.

    Returns (verified, reason). The reason is empty when it passed, and says
    what went wrong when it did not, in words that can go on the page.
    """
    limits = limits or tools.limits
    budget = budget or Budget()
    workdir = tools.workdir
    path = sandbox.write_script(workdir, "fix.py", source)

    for sample in samples:
        result = sandbox.run_python(
            path, sample.input_text, timeout=limits.oracle, limits=limits
        )
        if result.timed_out:
            return False, "it ran out of time on sample " + sample.name
        if result.exit_code != 0:
            return False, "it crashed on sample " + sample.name
        if not tokens_equal(result.stdout, sample.output_text, tools.float_tol):
            return False, "it does not match sample " + sample.name

    result = sandbox.run_python(
        path, found.input_text, timeout=limits.oracle, limits=limits
    )
    if result.timed_out:
        return False, "it ran out of time on the counterexample"
    if result.exit_code != 0:
        return False, "it crashed on the counterexample"
    if not tokens_equal(result.stdout, found.expected, tools.float_tol):
        return False, "it still gets the counterexample wrong"

    # The same tooling, the same references, a new target and seeds the first
    # run never used.
    checking = Tools(
        workdir=workdir,
        generator=tools.generator,
        validator=tools.validator,
        oracle_a=tools.oracle_a,
        oracle_b=tools.oracle_b,
        target=sandbox.command_for(path),
        limits=limits,
        float_tol=tools.float_tol,
    )
    outcome = run_stress(
        checking,
        Budget(
            max_tests=budget.fix_stress_tests,
            max_seconds=budget.max_seconds,
        ),
        seed_salt=FIX_SEED_SALT,
    )
    if outcome.failure is not None:
        return False, (
            "it failed "
            + str(len(outcome.tests))
            + " random tests in, on an input of "
            + str(len(outcome.failure.input_text.split()))
            + " numbers"
        )
    if len(outcome.tests) < budget.fix_stress_tests:
        return False, (
            "only "
            + str(len(outcome.tests))
            + " of "
            + str(budget.fix_stress_tests)
            + " random tests could be run"
        )
    return True, ""


def explain(
    llm: LLM,
    statement: str,
    target: Path,
    found: Counterexample,
    tools: Tools,
    samples: list[Sample],
    limits: Limits | None = None,
    budget: Budget | None = None,
) -> Outcome:
    """Diagnose, propose a fix, and report whether running it backed the fix up."""
    source = target.read_text(encoding="utf-8")
    outcome = Outcome()
    outcome.diagnosis = diagnose(llm, statement, source, found, target.name)
    try:
        outcome.source = propose(llm, statement, source, found, target.name)
    except SynthError as exc:
        outcome.reason = str(exc)
        return outcome
    outcome.verified, outcome.reason = verify(
        outcome.source, tools, samples, found, limits, budget
    )
    return outcome
