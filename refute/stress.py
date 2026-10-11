"""Generate, validate, run both references, run the target, record the verdict.

The rule that makes a verdict worth printing: a failure counts only when both
reference solutions agree on the answer. If they disagree, the test is set
aside and counted, and nothing is claimed about the target. The second
reference is otherwise run only every tenth test, to measure how often the
two agree at all.

This module also holds the judge, because the shrinker needs exactly the same
one: a smaller input is only a counterexample if it fails in the same way.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from . import sandbox
from .models import (
    Budget,
    Counterexample,
    Limits,
    MODES,
    Mode,
    TestRecord,
    Verdict,
)

#: Sizes a stress pass walks through, smallest first, so a small failing input
#: is found before a large one.
SIZES: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 30)
SEEDS_PER_PASS = 8
ORACLE_B_EVERY = 10


def tokens(text: str) -> list[str]:
    return text.split()


def tokens_equal(got: str, want: str, float_tol: float | None = None) -> bool:
    """Token comparison, with an optional tolerance for floating point output."""
    left = tokens(got)
    right = tokens(want)
    if len(left) != len(right):
        return False
    for a, b in zip(left, right):
        if a == b:
            continue
        if float_tol is None:
            return False
        try:
            if abs(float(a) - float(b)) <= float_tol:
                continue
        except (ValueError, OverflowError):
            return False
        return False
    return True


@dataclass
class Judgement:
    """What one input proved, and what the two sides printed."""

    verdict: Verdict
    expected: str = ""
    actual: str = ""
    oracle_b_run: bool = False
    oracles_agreed: bool | None = None
    note: str = ""

    @property
    def confirmed(self) -> bool:
        """A failure that both references back. The only kind Refute reports."""
        return self.verdict.is_failure()


@dataclass
class Tools:
    """The checked tooling plus the target, as the things that can be run."""

    workdir: Path
    generator: Path
    validator: Path
    oracle_a: Path
    target: Sequence[str]
    oracle_b: Path | None = None
    limits: Limits = field(default_factory=Limits)
    float_tol: float | None = None

    # -- the pieces --------------------------------------------------------

    def generate(self, seed: int, size: int, mode: str) -> str:
        """One random input, or an empty string if the generator failed."""
        result = sandbox.run_python(
            self.generator,
            args=[str(seed), str(size), mode],
            timeout=self.limits.generator,
            limits=self.limits,
        )
        if result.timed_out or result.exit_code != 0:
            return ""
        return result.stdout

    def validate(self, text: str) -> bool:
        if not text.strip():
            return False
        result = sandbox.run_python(
            self.validator, text, timeout=self.limits.validator, limits=self.limits
        )
        return result.exit_code == 0 and not result.timed_out

    def reference(self, text: str, path: Path) -> tuple[str, bool]:
        """Run a reference. Returns (output, answered)."""
        result = sandbox.run_python(
            path, text, timeout=self.limits.oracle, limits=self.limits
        )
        if result.timed_out or result.exit_code != 0:
            return "", False
        return result.stdout, True

    def run_target(self, text: str):
        return sandbox.run(
            self.target,
            stdin_text=text,
            timeout=self.limits.target,
            cwd=self.workdir,
            limits=self.limits,
        )

    # -- the judge ---------------------------------------------------------

    def judge(self, text: str, always_check_b: bool = False) -> Judgement:
        """Decide what this input proves about the target.

        A reference that times out or crashes has not answered, so the test is
        set aside as ORACLE_TIMEOUT rather than blamed on the target.
        """
        expected, answered = self.reference(text, self.oracle_a)
        if not answered:
            return Judgement(Verdict.ORACLE_TIMEOUT, note="reference A did not answer")

        target = self.run_target(text)
        if target.timed_out:
            candidate = Verdict.TLE
            actual = ""
        elif target.exit_code != 0:
            candidate = Verdict.RE
            actual = target.stderr
        elif tokens_equal(target.stdout, expected, self.float_tol):
            candidate = Verdict.PASS
            actual = target.stdout
        else:
            candidate = Verdict.WA
            actual = target.stdout

        # The second reference settles whether a failure is real, and on every
        # tenth test it also measures how often the two agree.
        needed = candidate.is_failure() or always_check_b
        if self.oracle_b is None or not needed:
            return Judgement(candidate, expected, actual)

        other, answered_b = self.reference(text, self.oracle_b)
        if not answered_b:
            return Judgement(
                Verdict.ORACLE_TIMEOUT,
                expected,
                actual,
                oracle_b_run=True,
                note="reference B did not answer",
            )
        agreed = tokens_equal(other, expected, self.float_tol)
        if not agreed:
            return Judgement(
                Verdict.ORACLE_CONFLICT,
                expected,
                actual,
                oracle_b_run=True,
                oracles_agreed=False,
                note="the two references disagree, so this input proves nothing",
            )
        return Judgement(
            candidate, expected, actual, oracle_b_run=True, oracles_agreed=True
        )


@dataclass
class StressOutcome:
    """Every test that ran, and the first confirmed failure if there was one."""

    tests: list[TestRecord] = field(default_factory=list)
    failure: Counterexample | None = None
    oracle_checks: int = 0
    oracle_agreements: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    largest_tokens: int = 0
    elapsed: float = 0.0

    @property
    def failures(self) -> int:
        return sum(1 for record in self.tests if Verdict(record.verdict).is_failure())

    @property
    def largest_size(self) -> int:
        return max((record.size for record in self.tests), default=0)


def _plan(
    pass_number: int, first_index: int, salt: int = 0
) -> list[tuple[int, int, Mode, int]]:
    """One pass: every size and mode, with eight seeds nobody has used yet.

    The salt shifts the whole sequence. Checking a suggested fix passes one,
    so that it is tested on inputs the first run never saw.
    """
    items: list[tuple[int, int, Mode, int]] = []
    index = first_index
    for size in SIZES:
        for mode in MODES:
            for offset in range(SEEDS_PER_PASS):
                index += 1
                seed = salt + pass_number * 1_000_003 + index * 97 + offset
                items.append((index, size, mode, seed))
    return items


def _chunks(items: Sequence, width: int) -> Iterable[Sequence]:
    for start in range(0, len(items), width):
        yield items[start : start + width]


def _one_test(
    tools: Tools, item: tuple[int, int, Mode, int]
) -> tuple[TestRecord, Judgement, str]:
    index, size, mode, seed = item
    started = time.monotonic()
    text = tools.generate(seed, size, mode.value)
    if not text:
        record = TestRecord(index, size, mode.value, seed, Verdict.INVALID.value)
        return record, Judgement(Verdict.INVALID, note="the generator failed"), text
    if not tools.validate(text):
        record = TestRecord(index, size, mode.value, seed, Verdict.INVALID.value)
        return (
            record,
            Judgement(Verdict.INVALID, note="the validator rejected the input"),
            text,
        )
    judgement = tools.judge(text, always_check_b=index % ORACLE_B_EVERY == 0)
    record = TestRecord(
        index=index,
        size=size,
        mode=mode.value,
        seed=seed,
        verdict=judgement.verdict.value,
        duration=time.monotonic() - started,
        oracle_b_run=judgement.oracle_b_run,
    )
    return record, judgement, text


def workers() -> int:
    return max(2, min(8, os.cpu_count() or 4))


def run_stress(
    tools: Tools,
    budget: Budget | None = None,
    on_progress: Callable[[StressOutcome], None] | None = None,
    seed_salt: int = 0,
) -> StressOutcome:
    """Test until the first confirmed failure, or until the budget runs out."""
    budget = budget or Budget()
    outcome = StressOutcome()
    started = time.monotonic()
    width = workers()

    with ThreadPoolExecutor(max_workers=width) as pool:
        pass_number = 0
        while True:
            if outcome.failure is not None:
                break
            if len(outcome.tests) >= budget.max_tests:
                break
            if time.monotonic() - started >= budget.max_seconds:
                break
            pass_number += 1
            items = _plan(pass_number, len(outcome.tests), seed_salt)
            room = budget.max_tests - len(outcome.tests)
            items = items[:room]

            for chunk in _chunks(items, width * 2):
                results = list(pool.map(lambda item: _one_test(tools, item), chunk))
                for record, judgement, text in results:
                    outcome.tests.append(record)
                    outcome.largest_tokens = max(
                        outcome.largest_tokens, len(text.split())
                    )
                    if judgement.oracles_agreed is not None:
                        outcome.oracle_checks += 1
                        outcome.oracle_agreements += int(judgement.oracles_agreed)
                    verdict = Verdict(record.verdict)
                    if verdict.is_skipped():
                        outcome.skipped[verdict.value] = (
                            outcome.skipped.get(verdict.value, 0) + 1
                        )
                    if verdict.is_failure() and outcome.failure is None:
                        outcome.failure = Counterexample(
                            input_text=text,
                            expected=judgement.expected,
                            actual=judgement.actual,
                            verdict=verdict.value,
                            size=record.size,
                            mode=record.mode,
                            seed=record.seed,
                        )
                if on_progress is not None:
                    on_progress(outcome)
                if outcome.failure is not None:
                    break
                if time.monotonic() - started >= budget.max_seconds:
                    break

    outcome.elapsed = time.monotonic() - started
    return outcome
