"""Make the failing input as small as it can be while it still fails.

Two stages. First look for a smaller generated size that fails at all, since
dropping from thirty numbers to five is worth more than any local edit. Then
edit the input directly: delete lines and tokens, lower numbers, and move
counts and their items together.

That last move is what makes counted inputs shrink at all. An input shaped
"n, then n numbers" cannot lose a number by deletion, because the validator
rejects the result for not matching its own count. Lowering the count by one
while deleting the last item it counts keeps the input valid, so the pair
moves as one step.

Every candidate has to earn its place: the validator accepts it, both
references agree on the answer, and the target still fails the same way. That
costs three or four processes per candidate, so candidates are tried in
parallel; which one is accepted does not depend on which finished first, only
on the order they were generated in, so a recorded run shrinks the same way
twice.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Sequence

from .models import Budget, Counterexample, ShrinkStep
from .stress import MODES, SIZES, Tools, workers

SMALLER_SIZE_SEEDS = 60
FAILING_SIZE_SEEDS = 200
RECHECKS = 3


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
    """Try every number as 0, 1, -1, half of itself and one less."""
    lines = text.splitlines()
    out: list[tuple[str, str]] = []
    for row, line in enumerate(lines):
        parts = line.split()
        for column, token in enumerate(parts):
            value = _as_int(token)
            if value is None:
                continue
            for replacement in (0, 1, -1, value // 2, value - 1):
                if replacement == value:
                    continue
                edited = list(lines)
                head = list(parts)
                head[column] = str(replacement)
                edited[row] = " ".join(head)
                out.append(("lowered a number", _tidy(edited)))
    return out


def value_moves_at(text: str, row: int, column: int) -> list[tuple[str, str]]:
    """The value moves for one number, so a descent can carry on where it worked."""
    lines = text.splitlines()
    if not 0 <= row < len(lines):
        return []
    parts = lines[row].split()
    if not 0 <= column < len(parts):
        return []
    value = _as_int(parts[column])
    if value is None:
        return []
    out: list[tuple[str, str]] = []
    for replacement in (0, 1, -1, value // 2, value - 1):
        if replacement == value:
            continue
        edited = list(lines)
        head = list(parts)
        head[column] = str(replacement)
        edited[row] = " ".join(head)
        out.append(("lowered a number", _tidy(edited)))
    return out


def changed_place(before: str, after: str) -> tuple[int, int] | None:
    """Where one number changed, if that is the only difference between two inputs."""
    old_lines = before.splitlines()
    new_lines = after.splitlines()
    if len(old_lines) != len(new_lines):
        return None
    rows = [
        index
        for index, (old, new) in enumerate(zip(old_lines, new_lines))
        if old != new
    ]
    if len(rows) != 1:
        return None
    row = rows[0]
    old_parts = old_lines[row].split()
    new_parts = new_lines[row].split()
    if len(old_parts) != len(new_parts):
        return None
    columns = [
        index
        for index, (old, new) in enumerate(zip(old_parts, new_parts))
        if old != new
    ]
    if len(columns) != 1:
        return None
    return (row, columns[0])


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


def still_fails(tools: Tools, text: str, verdict: str) -> Counterexample | None:
    """A candidate is a counterexample only if nothing about the failure changed.

    The size recorded here is the number of tokens, because an edited input
    no longer came from any generator size.
    """
    if not text.strip():
        return None
    if not tools.validate(text):
        return None
    judgement = tools.judge(text)
    if judgement.verdict.value != verdict:
        return None
    return Counterexample(
        input_text=text,
        expected=judgement.expected,
        actual=judgement.actual,
        verdict=verdict,
        size=len(text.split()),
        mode="shrunk",
        seed=0,
    )


# ---------------------------------------------------------------------------
# stage one: a smaller generated size


def _seeded(
    tools: Tools, size: int, mode: str, seed: int, verdict: str
) -> Counterexample | None:
    text = tools.generate(seed, size, mode)
    if not text:
        return None
    found = still_fails(tools, text, verdict)
    if found is not None:
        found.mode = mode
        found.seed = seed
    return found


def _sweep(
    pool: ThreadPoolExecutor,
    tools: Tools,
    size: int,
    attempts: int,
    verdict: str,
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
        results = list(
            pool.map(lambda item: _seeded(tools, size, item[0], item[1], verdict), batch)
        )
        for found in results:
            if found is None:
                continue
            if best is None or weight(found.input_text) < weight(best.input_text):
                best = found
        if best is not None and stop_at_first:
            break
    return best


def smallest_failing_size(
    pool: ThreadPoolExecutor,
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
            tools,
            size,
            SMALLER_SIZE_SEEDS,
            start.verdict,
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
        tools,
        failing_size,
        FAILING_SIZE_SEEDS,
        start.verdict,
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
    with ThreadPoolExecutor(max_workers=width) as pool:
        smaller = smallest_failing_size(
            pool, tools, start, scan_deadline, sweep_deadline
        )
        if smaller is not None and weight(smaller.input_text) < weight(current.input_text):
            current = smaller
            record("generated a smaller input", current)

        tried: set[str] = {current.input_text}
        # When lowering one number works, the next thing to try is that same
        # number again. Numbers usually have to come down one at a time, since
        # halving tends to overshoot into inputs that pass, and rescanning
        # every move after each single step costs far more than the step.
        focus: tuple[int, int] | None = None
        while time.monotonic() < deadline:
            moves = (
                value_moves_at(current.input_text, *focus)
                if focus is not None
                else candidates(current.input_text)
            )
            limit = weight(current.input_text)
            queue = [
                (action, text)
                for action, text in moves
                if text not in tried and text.strip() and weight(text) < limit
            ]
            improved = False
            for batch in _batches(queue, width):
                if time.monotonic() >= deadline:
                    break
                for _, text in batch:
                    tried.add(text)
                results = list(
                    pool.map(
                        lambda item: still_fails(tools, item[1], start.verdict), batch
                    )
                )
                for (action, _), found in zip(batch, results):
                    if found is None:
                        continue
                    before = current.input_text
                    current = found
                    record(action, current)
                    focus = changed_place(before, current.input_text)
                    improved = True
                    break
                if improved:
                    break
            if improved:
                continue
            if focus is not None:
                # That number will not come down any further. Look everywhere
                # again before giving up.
                focus = None
                continue
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
