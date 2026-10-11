"""Make the failing input as small as it can be while it still fails.

Three stages. First look for a smaller generated size that fails at all, since
dropping from thirty numbers to five is worth more than any local edit. Then
edit the input directly: delete lines and tokens, and move counts and their
items together. Then take each number on its own and search for the smallest
value it can hold while the target still fails.

That count move is what makes counted inputs shrink at all. An input shaped
"n, then n numbers" cannot lose a number by deletion, because the validator
rejects the result for not matching its own count. Lowering the count by one
while deleting the last item it counts keeps the input valid, so the pair
moves as one step.

The per-number search is a binary search, not a walk. Zero, one and minus one
are tried first; if none of them fails, the answer lies between the largest
value known to pass and the smallest known to fail, and the search closes
that interval. Stepping down instead, by halves and decrements, needs one
accepted step per decrement near the bottom, and every accepted step costs
processes.

Every candidate has to earn its place: the validator accepts it, both
references agree on the answer, and the target still fails the same way. The
target runs first because it is the one process that can rule a candidate out
by itself, and no input is ever evaluated twice. What is left runs in a
thread pool; which candidate is accepted does not depend on which finished
first, only on the order they were generated in, so a recorded run shrinks
the same way twice.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Sequence

from .models import Budget, Counterexample, RunResult, ShrinkStep, Verdict
from .stress import MODES, SIZES, Tools, tokens_equal, workers

SMALLER_SIZE_SEEDS = 60
FAILING_SIZE_SEEDS = 200
RECHECKS = 3

#: The values every number is tried at before the binary search starts.
PROBES: tuple[int, ...] = (0, 1, -1)

#: Probes per binary search round. Three quarters the interval each round and
#: fits any pool. It is a constant rather than the pool width so that the same
#: recorded run shrinks the same way on a machine with a different core count.
PROBES_PER_ROUND = 3


@dataclass
class ShrinkOutcome:
    """The smallest confirmed failing input, and the path that led to it."""

    counterexample: Counterexample
    steps: list[ShrinkStep] = field(default_factory=list)
    stable: bool = True
    rechecks: int = 0
    elapsed: float = 0.0

    @property
    def start_tokens(self) -> int:
        return self.steps[0].size_tokens if self.steps else 0

    @property
    def final_tokens(self) -> int:
        return self.steps[-1].size_tokens if self.steps else 0


def weight(text: str) -> tuple[int, int, int]:
    """How big an input is: fewer tokens, then smaller numbers, then fewer characters.

    A candidate is only accepted if this falls strictly. That is what makes
    the loop finish: the tuple cannot fall forever, so "1 to -1 to 1" and
    other cycles are refused rather than chased.
    """
    parts = text.split()
    magnitude = 0
    for part in parts:
        try:
            magnitude += abs(int(part))
        except ValueError:
            magnitude += len(part)
    return (len(parts), magnitude, len(text))


def _tidy(lines: list[str]) -> str:
    kept = [line.rstrip() for line in lines]
    while kept and not kept[-1].strip():
        kept.pop()
    if not kept:
        return ""
    return "\n".join(kept) + "\n"


def _as_int(token: str) -> int | None:
    try:
        return int(token)
    except ValueError:
        return None


def _batches(items: Sequence, width: int) -> list[Sequence]:
    return [items[start : start + width] for start in range(0, len(items), width)]


# ---------------------------------------------------------------------------
# the moves


def compound_moves(text: str) -> list[tuple[str, str]]:
    """Lower a count and drop the item it counted, as one move.

    A number is treated as a count when it equals the number of tokens on the
    next line, or the number of lines after its own. Both shapes are
    everywhere: "n" followed by a line of n numbers, and "n" followed by n
    lines.
    """
    lines = text.splitlines()
    out: list[tuple[str, str]] = []
    for row, line in enumerate(lines):
        parts = line.split()
        for column, token in enumerate(parts):
            value = _as_int(token)
            if value is None or value < 1:
                continue

            if value == len(lines) - row - 1:
                edited = list(lines)
                head = list(parts)
                head[column] = str(value - 1)
                edited[row] = " ".join(head)
                del edited[-1]
                out.append(
                    ("lowered a line count and dropped the last line", _tidy(edited))
                )

            if row + 1 < len(lines):
                next_parts = lines[row + 1].split()
                if next_parts and value == len(next_parts):
                    edited = list(lines)
                    head = list(parts)
                    head[column] = str(value - 1)
                    edited[row] = " ".join(head)
                    edited[row + 1] = " ".join(next_parts[:-1])
                    out.append(
                        (
                            "lowered a count and dropped the last number it counted",
                            _tidy(edited),
                        )
                    )
    return out


def line_moves(text: str) -> list[tuple[str, str]]:
    lines = text.splitlines()
    if len(lines) < 2:
        return []
    out: list[tuple[str, str]] = []
    for row in range(len(lines) - 1, -1, -1):
        edited = list(lines)
        del edited[row]
        out.append(("dropped a line", _tidy(edited)))
    return out


def token_moves(text: str) -> list[tuple[str, str]]:
    lines = text.splitlines()
    out: list[tuple[str, str]] = []
    for row, line in enumerate(lines):
        parts = line.split()
        for column in range(len(parts) - 1, -1, -1):
            edited = list(lines)
            head = list(parts)
            del head[column]
            edited[row] = " ".join(head)
            out.append(("dropped a number", _tidy(edited)))
    return out


def value_moves(text: str) -> list[tuple[str, str]]:
    """Try every number as 0, 1 and minus 1.

    These three are the answer often enough to be worth one cheap sweep over
    the whole input. Everything between them and the number itself is left to
    the binary search, which gets there in probes instead of steps.
    """
    lines = text.splitlines()
    out: list[tuple[str, str]] = []
    for row, line in enumerate(lines):
        parts = line.split()
        for column, token in enumerate(parts):
            value = _as_int(token)
            if value is None:
                continue
            for replacement in PROBES:
                if replacement == value:
                    continue
                edited = list(lines)
                head = list(parts)
                head[column] = str(replacement)
                edited[row] = " ".join(head)
                out.append(("lowered a number", _tidy(edited)))
    return out


def candidates(text: str) -> list[tuple[str, str]]:
    """Every move worth trying, with the ones that remove most tried first."""
    moves = (
        compound_moves(text) + line_moves(text) + token_moves(text) + value_moves(text)
    )
    limit = weight(text)
    out: list[tuple[str, str]] = []
    seen: set[str] = {text}
    for action, candidate in moves:
        if not candidate.strip() or candidate in seen:
            continue
        if weight(candidate) >= limit:
            continue
        seen.add(candidate)
        out.append((action, candidate))
    return out


# ---------------------------------------------------------------------------
# accepting a candidate

#: What the target itself did, which is all one run of it can say. Every
#: verdict Refute reports rests on exactly one of these three.
PRINTED = "printed output"
CRASHED = "exited nonzero"
TIMED_OUT = "timed out"

_WANTED: dict[str, str] = {
    Verdict.WA.value: PRINTED,
    Verdict.RE.value: CRASHED,
    Verdict.TLE.value: TIMED_OUT,
}


def behaviour(result: RunResult) -> str:
    """Which of the three things the target did on this input."""
    if result.timed_out:
        return TIMED_OUT
    if result.exit_code != 0:
        return CRASHED
    return PRINTED


def still_fails(tools: Tools, text: str, verdict: str) -> Counterexample | None:
    """A candidate is a counterexample only if nothing about the failure changed.

    The same three rules the stress judge applies, in the order that costs the
    least. The target goes first because it is the one process that can rule a
    candidate out on its own: an input edited until the target stops crashing,
    or starts crashing, or stops running out of time, cannot carry the verdict
    being shrunk whatever the references would have said about it. Deletions
    land there constantly, and each one that does costs one process instead of
    four. Only when the target did the same thing as on the input the shrink
    started from are the validator and the two references worth running.

    The size recorded here is the number of tokens, because an edited input no
    longer came from any generator size.
    """
    if not text.strip():
        return None

    wanted = _WANTED.get(verdict, PRINTED)
    target = tools.run_target(text)
    if behaviour(target) != wanted:
        return None
    if not tools.validate(text):
        return None

    expected, answered = tools.reference(text, tools.oracle_a)
    if not answered:
        return None
    if wanted == PRINTED and tokens_equal(target.stdout, expected, tools.float_tol):
        return None
    if tools.oracle_b is not None:
        other, answered_b = tools.reference(text, tools.oracle_b)
        if not answered_b or not tokens_equal(other, expected, tools.float_tol):
            return None

    return Counterexample(
        input_text=text,
        expected=expected,
        actual=target.stderr if wanted == CRASHED else target.stdout,
        verdict=verdict,
        size=len(text.split()),
        mode="shrunk",
        seed=0,
    )


@dataclass
class Verdicts:
    """Every candidate's verdict, kept so no input is evaluated twice.

    Shrinking meets the same text over and over: a probe reached from two
    directions, a value already tried before a deletion, a candidate two moves
    both produce. Each evaluation costs up to four processes, so they are
    worth remembering.

    Only the calling thread writes here. A batch is built from the texts that
    are not in the table yet, the pool evaluates those, and the results are
    read back in the order the batch was built, so which worker finished first
    changes nothing.
    """

    tools: Tools
    verdict: str
    table: dict[str, Counterexample | None] = field(default_factory=dict)

    def check(
        self, pool: ThreadPoolExecutor, texts: Sequence[str]
    ) -> list[Counterexample | None]:
        fresh = [text for text in dict.fromkeys(texts) if text not in self.table]
        if fresh:
            results = list(
                pool.map(
                    lambda text: still_fails(self.tools, text, self.verdict), fresh
                )
            )
            for text, found in zip(fresh, results):
                self.table[text] = found
        return [self.table[text] for text in texts]


# ---------------------------------------------------------------------------
# stage one: a smaller generated size


def _sweep(
    pool: ThreadPoolExecutor,
    verdicts: Verdicts,
    tools: Tools,
    size: int,
    attempts: int,
    deadline: float,
    offset: int,
    stop_at_first: bool,
) -> Counterexample | None:
    """Try `attempts` seeds at one size. Returns the shortest failing input.

    Seeds are tried in parallel but read back in order, so the result does not
    depend on which process finished first.
    """
    plan = [
        (MODES[index % len(MODES)].value, offset + size * 1_009 + index)
        for index in range(attempts)
    ]
    best: Counterexample | None = None
    for batch in _batches(plan, workers()):
        if time.monotonic() >= deadline:
            break
        texts = list(
            pool.map(lambda item: tools.generate(item[1], size, item[0]), batch)
        )
        for (mode, seed), found in zip(batch, verdicts.check(pool, texts)):
            if found is None:
                continue
            found = replace(found, mode=mode, seed=seed)
            if best is None or weight(found.input_text) < weight(best.input_text):
                best = found
        if best is not None and stop_at_first:
            break
    return best


def smallest_failing_size(
    pool: ThreadPoolExecutor,
    verdicts: Verdicts,
    tools: Tools,
    start: Counterexample,
    scan_deadline: float,
    sweep_deadline: float,
) -> Counterexample | None:
    """Find the smallest size that fails, then dig in at that size.

    Sixty seeds at each smaller size is enough to tell a failure that needs a
    big input from one that was simply found late. Two hundred at the size
    that does fail gives the smallest input that size tends to produce.

    The two stages get separate deadlines on purpose. Walking every smaller
    size costs four processes a seed and can fill the whole time box on its
    own, and the sweep is the stage that actually finds small inputs, so it
    cannot be left to whatever time happens to be left over.
    """
    best: Counterexample | None = None
    failing_size = start.size
    for size in [size for size in SIZES if size < start.size]:
        if time.monotonic() >= scan_deadline:
            break
        found = _sweep(
            pool,
            verdicts,
            tools,
            size,
            SMALLER_SIZE_SEEDS,
            scan_deadline,
            offset=7_000_000,
            stop_at_first=True,
        )
        if found is not None:
            best = found
            failing_size = size
            break

    # Two hundred seeds at the smallest size that fails, including when that
    # is the size the failure was already found at. Without this pass a
    # failure found at the smallest possible size never gets a second look,
    # and an input whose numbers happen to be large stays large, because no
    # single local edit can walk them down.
    if time.monotonic() >= sweep_deadline:
        return best
    deeper = _sweep(
        pool,
        verdicts,
        tools,
        failing_size,
        FAILING_SIZE_SEEDS,
        sweep_deadline,
        offset=8_000_000,
        stop_at_first=False,
    )
    if deeper is not None and (
        best is None or weight(deeper.input_text) < weight(best.input_text)
    ):
        best = deeper
    return best


# ---------------------------------------------------------------------------
# stage three: the smallest value each number can hold


def number_places(text: str) -> list[tuple[int, int]]:
    """Where every number sits, as (row, column), in reading order."""
    places: list[tuple[int, int]] = []
    for row, line in enumerate(text.splitlines()):
        for column, token in enumerate(line.split()):
            if _as_int(token) is not None:
                places.append((row, column))
    return places


def value_at(text: str, row: int, column: int) -> int | None:
    lines = text.splitlines()
    if not 0 <= row < len(lines):
        return None
    parts = lines[row].split()
    if not 0 <= column < len(parts):
        return None
    return _as_int(parts[column])


def with_value(text: str, row: int, column: int, value: int) -> str:
    lines = text.splitlines()
    parts = lines[row].split()
    parts[column] = str(value)
    lines[row] = " ".join(parts)
    return _tidy(lines)


def probe_points(low: int, high: int, count: int) -> list[int]:
    """Up to `count` evenly spaced values strictly between low and high.

    With count 1 this is plain bisection. With more it is the same search run
    several probes at a time, which is what a thread pool is for.
    """
    span = high - low
    if span <= 1:
        return []
    count = min(count, span - 1)
    points: list[int] = []
    for index in range(1, count + 1):
        point = low + (span * index) // (count + 1)
        if low < point < high and point not in points:
            points.append(point)
    return points


def bisect_place(
    pool: ThreadPoolExecutor,
    verdicts: Verdicts,
    text: str,
    row: int,
    column: int,
    deadline: float,
) -> list[tuple[str, Counterexample]]:
    """Walk one number down to the smallest value it can hold and still fail.

    Zero, one and minus one first; if one of those fails there is nothing
    smaller to look for. Otherwise the answer lies between the largest value
    known to pass and the smallest known to fail, so the search closes that
    interval, three probes to a round. Stepping down by halves and decrements
    instead takes one accepted step per decrement near the bottom: walking a
    million down to six that way is thousands of steps at four processes each,
    and closing the interval is about twenty probes.

    This assumes a smaller number cannot turn a passing input into a failing
    one. Where that does not hold the search stops early, holding a failing
    input that is still smaller than the one it started from, and the next
    round of moves tries again from there.

    Returns the steps it accepted, in order.
    """
    value = value_at(text, row, column)
    if value is None:
        return []
    sign = -1 if value < 0 else 1
    taken: list[tuple[str, Counterexample]] = []
    limit = weight(text)

    def offer(candidate_value: int) -> str | None:
        candidate = with_value(text, row, column, candidate_value)
        if not candidate.strip() or weight(candidate) >= limit:
            return None
        return candidate

    # The three small values, together, so one round of the pool covers them.
    small = [(probe, offer(probe)) for probe in PROBES]
    small = [(probe, candidate) for probe, candidate in small if candidate is not None]
    if small and time.monotonic() < deadline:
        results = verdicts.check(pool, [candidate for _, candidate in small])
        for found in results:
            if found is not None:
                return [("lowered a number to its smallest value", found)]

    # Minus one passed too, so magnitude one passes whichever sign the number
    # has, and the number itself fails: that is the interval to close.
    low, high = 1, abs(value)
    while high - low > 1 and time.monotonic() < deadline:
        offered = [
            (point, offer(sign * point))
            for point in probe_points(low, high, PROBES_PER_ROUND)
        ]
        offered = [(point, probe) for point, probe in offered if probe is not None]
        if not offered:
            break
        results = verdicts.check(pool, [probe for _, probe in offered])

        failing = [
            (point, found)
            for (point, _), found in zip(offered, results)
            if found is not None
        ]
        if failing:
            point, found = failing[0]  # the smallest probe that still fails
            taken.append(("lowered a number by binary search", found))
            text = found.input_text
            limit = weight(text)
            high = point
        passing = [
            point
            for (point, _), found in zip(offered, results)
            if found is None and point < high
        ]
        if passing:
            low = max(low, max(passing))
        # A round either lowered high or raised low: every probe is strictly
        # inside the interval, and one of the two lists always has something
        # in it. So the interval closes and this loop ends.
    return taken


# ---------------------------------------------------------------------------
# the whole thing


def shrink(
    tools: Tools,
    start: Counterexample,
    budget: Budget | None = None,
) -> ShrinkOutcome:
    """Shrink until nothing more is accepted, or until the time box closes."""
    budget = budget or Budget()
    began = time.monotonic()
    box = budget.shrink_seconds
    deadline = began + box
    # Each stage gets its own share, so that none of them can starve the
    # others: walking the smaller sizes, then sweeping the size that fails,
    # then editing the input directly.
    scan_deadline = began + box * 0.4
    sweep_deadline = began + box * 0.7

    current = start
    steps = [
        ShrinkStep(
            step=0,
            action="found",
            input_text=current.input_text,
            size_tokens=len(current.input_text.split()),
        )
    ]

    def record(action: str, found: Counterexample) -> None:
        steps.append(
            ShrinkStep(
                step=len(steps),
                action=action,
                input_text=found.input_text,
                size_tokens=len(found.input_text.split()),
            )
        )

    width = workers()
    verdicts = Verdicts(tools, start.verdict)
    with ThreadPoolExecutor(max_workers=width) as pool:
        smaller = smallest_failing_size(
            pool, verdicts, tools, start, scan_deadline, sweep_deadline
        )
        if smaller is not None and weight(smaller.input_text) < weight(
            current.input_text
        ):
            current = smaller
            record("generated a smaller input", current)

        while time.monotonic() < deadline:
            improved = False

            # Deletions and count moves first: they remove the most, and a
            # number that is gone needs no search.
            queue = candidates(current.input_text)
            for batch in _batches(queue, width):
                if time.monotonic() >= deadline:
                    break
                results = verdicts.check(pool, [text for _, text in batch])
                for (action, _), found in zip(batch, results):
                    if found is None:
                        continue
                    current = found
                    record(action, current)
                    improved = True
                    break
                if improved:
                    break
            if improved:
                continue

            # Then every number, one binary search each. A value change keeps
            # the shape of the input, so the places found before the first
            # search are still the right places after it.
            for row, column in number_places(current.input_text):
                if time.monotonic() >= deadline:
                    break
                for action, found in bisect_place(
                    pool, verdicts, current.input_text, row, column, deadline
                ):
                    current = found
                    record(action, current)
                    improved = True
            if not improved:
                break

        # The same input, three more times. A failure that only happens
        # sometimes is not a counterexample, and saying so is cheaper than
        # being wrong.
        stable = True
        rechecks = 0
        for _ in range(RECHECKS):
            rechecks += 1
            if still_fails(tools, current.input_text, start.verdict) is None:
                stable = False
                break

    return ShrinkOutcome(
        counterexample=current,
        steps=steps,
        stable=stable,
        rechecks=rechecks,
        elapsed=time.monotonic() - began,
    )
