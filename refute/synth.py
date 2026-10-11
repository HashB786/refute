"""The model writes the throwaway test tooling; running it decides whether it is kept.

Four scripts come out of here: two independent reference solutions, an input
validator and an input generator. Nothing is trusted because the model wrote
it. Each script must parse, pass the guard, run as its own process, and then
prove itself: the oracles against every sample, the validator against the
samples and against deliberately broken copies of them, the generator against
the validator over a smoke set.

A script that fails is sent back with the concrete failure, at most three
times. All of this depends only on the statement and its samples, never on
the target, so one cached set of tooling serves every target for a problem.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import guard, sandbox
from .llm import LLM
from .models import Limits, MODES, Sample, Script
from .stress import tokens_equal

MAX_REPAIRS = 3
SMOKE_SIZES = (1, 2, 5, 10, 30)
SMOKE_SEEDS = (1, 2, 3, 4, 5)
OUT_OF_RANGE = "1000000000000000000"

SYSTEM = (
    "You write small Python 3 programs that test competitive programming "
    "solutions. Reply with exactly one fenced Python code block and nothing "
    "else: no explanation before it and none after it."
)

IMPORT_RULE = (
    "The script may import only these modules: "
    + ", ".join(sorted(guard.ALLOWED_IMPORTS))
    + ". It must not call open, exec, eval, compile, __import__, globals or "
    "getattr, and must not touch dunder attributes. It reads only from "
    "standard input and writes only to standard output."
)

_FENCE = re.compile(r"```[A-Za-z0-9_+-]*\n(.*?)```", re.DOTALL)


class SynthError(Exception):
    """Tooling could not be produced. The message says which piece and why."""


@dataclass
class Calibration:
    """How the validator did against deliberately broken copies of the samples."""

    mutations: int = 0
    rejected: int = 0
    weak: bool = False

    @property
    def summary(self) -> str:
        if self.weak:
            return "weak: accepted every broken copy"
        return str(self.rejected) + " of " + str(self.mutations) + " rejected"


@dataclass
class Smoke:
    """How the generator did against the validator over the smoke set."""

    runs: int = 0
    accepted: int = 0
    distinct: int = 0


@dataclass
class Tooling:
    """Everything a stress run needs, with the evidence that it works."""

    samples: list[Sample]
    oracle_a: Script
    validator: Script
    generator: Script
    oracle_b: Script | None = None
    dropped_samples: int = 0
    trusted_oracle: bool = False
    calibration: Calibration = field(default_factory=Calibration)
    smoke: Smoke = field(default_factory=Smoke)
    notes: list[str] = field(default_factory=list)
    paths: dict[str, Path] = field(default_factory=dict)

    @property
    def repairs(self) -> int:
        scripts = [self.oracle_a, self.oracle_b, self.validator, self.generator]
        return sum(s.repairs for s in scripts if s is not None)


# ---------------------------------------------------------------------------
# text handling


def extract_code(text: str) -> str:
    """The one fenced block the model was asked for. The longest one, if several."""
    blocks = _FENCE.findall(text)
    if not blocks:
        raise SynthError("the reply had no fenced code block")
    return max(blocks, key=len).strip() + "\n"


def normalize(text: str) -> str:
    """Whitespace-normalized text, for the verbatim sample check."""
    return " ".join(text.split())


def _fence_block(sample: Sample) -> str:
    return (
        "Example "
        + sample.name
        + " input:\n"
        + sample.input_text.rstrip("\n")
        + "\nExample "
        + sample.name
        + " output:\n"
        + sample.output_text.rstrip("\n")
        + "\n"
    )


def samples_block(samples: list[Sample]) -> str:
    if not samples:
        return "The statement shows no examples.\n"
    return "\n".join(_fence_block(s) for s in samples)


# ---------------------------------------------------------------------------
# samples


def load_samples(directory: Path) -> list[Sample]:
    """Read 1.in / 1.out, 2.in / 2.out, ... from a directory given with --samples."""
    samples: list[Sample] = []
    for in_path in sorted(directory.glob("*.in"), key=lambda p: (len(p.stem), p.stem)):
        out_path = in_path.with_suffix(".out")
        if not out_path.is_file():
            continue
        samples.append(
            Sample(
                name=in_path.stem,
                input_text=in_path.read_text(encoding="utf-8"),
                output_text=out_path.read_text(encoding="utf-8"),
                source="samples_dir",
            )
        )
    if not samples:
        raise SynthError("no 1.in / 1.out pairs were found in " + str(directory))
    return samples


def parse_sample_reply(text: str) -> list[Sample]:
    """Pull the model's example list out of its reply. Shape errors are not fatal."""
    blocks = _FENCE.findall(text)
    raw = blocks[0] if blocks else text
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    samples: list[Sample] = []
    for position, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            continue
        got_in = item.get("input")
        got_out = item.get("output")
        if not isinstance(got_in, str) or not isinstance(got_out, str):
            continue
        if not got_in.strip() or not got_out.strip():
            continue
        samples.append(
            Sample(
                name=str(position),
                input_text=got_in.rstrip() + "\n",
                output_text=got_out.rstrip() + "\n",
                source="model_extracted",
            )
        )
    return samples


def keep_verbatim(statement: str, candidates: list[Sample]) -> list[Sample]:
    """Drop any extracted sample that is not in the statement, word for word.

    A model that paraphrases an example, or invents one, would quietly poison
    every oracle check that follows. Whitespace is normalized first, because
    the statement's layout is not part of the data.
    """
    flat = normalize(statement)
    kept: list[Sample] = []
    for sample in candidates:
        if not normalize(sample.input_text):
            continue
        if normalize(sample.input_text) not in flat:
            continue
        if normalize(sample.output_text) not in flat:
            continue
        kept.append(sample)
    return kept


def extract_samples(llm: LLM, statement: str) -> tuple[list[Sample], int]:
    """Ask the model for the statement's examples, then throw out the invented ones."""
    prompt = (
        "Here is a problem statement.\n---\n"
        + statement.strip()
        + "\n---\n\nList the example inputs and outputs the statement shows, "
        "as a JSON array of objects with the keys \"input\" and \"output\". "
        "Copy the text exactly as the statement shows it, with the same line "
        "breaks and the same numbers. Do not reformat anything, do not solve "
        "anything and do not invent an example the statement does not show. "
        "If it shows no examples, reply with [].\n"
        "Reply with exactly one fenced json code block and nothing else."
    )
    reply = llm.complete("samples", prompt, SYSTEM)
    candidates = parse_sample_reply(reply.text)
    kept = keep_verbatim(statement, candidates)
    return kept, len(candidates) - len(kept)


# ---------------------------------------------------------------------------
# validator calibration


def mutations(text: str) -> list[tuple[str, str]]:
    """Three deterministic ways to break an input, for calibrating the validator.

    A validator that accepts all three is not reading the constraints, and a
    stress run built on it would call every generated input valid.
    """
    out: list[tuple[str, str]] = []

    lines = text.splitlines()
    dropped = list(lines)
    for index in range(len(dropped) - 1, -1, -1):
        parts = dropped[index].split()
        if parts:
            dropped[index] = " ".join(parts[:-1])
            out.append(("the last token removed", "\n".join(dropped) + "\n"))
            break

    appended = list(lines)
    for index in range(len(appended) - 1, -1, -1):
        if appended[index].split():
            appended[index] = appended[index].rstrip() + " 1"
            out.append(("an extra token appended", "\n".join(appended) + "\n"))
            break
    else:
        out.append(("an extra token appended", text.rstrip("\n") + "\n1\n"))

    pushed = list(lines)
    for index, line in enumerate(pushed):
        parts = line.split()
        for position, token in enumerate(parts):
            try:
                int(token)
            except ValueError:
                continue
            parts[position] = OUT_OF_RANGE
            pushed[index] = " ".join(parts)
            out.append(("the first number out of range", "\n".join(pushed) + "\n"))
            break
        else:
            continue
        break

    flat = normalize(text)
    return [(name, body) for name, body in out if normalize(body) != flat]


# ---------------------------------------------------------------------------
# the repair loop


def _repair_prompt(base: str, source: str, problem: str) -> str:
    return (
        base
        + "\n\nThis is the script you sent last time:\n```python\n"
        + source.strip()
        + "\n```\n\nIt did not work: "
        + problem
        + "\nSend one corrected fenced Python code block. Change what is "
        "wrong; do not start again from nothing."
    )


def build_script(
    llm: LLM,
    purpose: str,
    prompt: str,
    filename: str,
    workdir: Path,
    check: Callable[[Path], str],
) -> Script:
    """Ask, guard, run, check, and on failure ask again with the exact reason."""
    attempt_prompt = prompt
    source = ""
    problem = ""
    for repairs in range(MAX_REPAIRS + 1):
        reply = llm.complete(purpose, attempt_prompt, SYSTEM)
        try:
            source = extract_code(reply.text)
        except SynthError as exc:
            problem = str(exc)
            attempt_prompt = _repair_prompt(prompt, reply.text.strip()[:600], problem)
            continue
        refused = guard.describe(source)
        if refused:
            problem = "the guard refused it: " + refused
            attempt_prompt = _repair_prompt(prompt, source, problem)
            continue
        path = sandbox.write_script(workdir, filename, source)
        problem = check(path)
        if not problem:
            return Script(purpose=purpose, source=source, repairs=repairs)
        attempt_prompt = _repair_prompt(prompt, source, problem)
    raise SynthError(
        purpose + " still did not work after " + str(MAX_REPAIRS) + " repairs: " + problem
    )


# ---------------------------------------------------------------------------
# the checks each script has to pass


def _tail(text: str, limit: int = 200) -> str:
    cleaned = " ".join(text.split())
    return cleaned[-limit:] if cleaned else "nothing"


def oracle_check(
    samples: list[Sample], limits: Limits, float_tol: float | None
) -> Callable[[Path], str]:
    """A reference solution has to reproduce every sample output."""

    def check(path: Path) -> str:
        for sample in samples:
            result = sandbox.run_python(
                path, sample.input_text, timeout=limits.oracle, limits=limits
            )
            where = "on example " + sample.name + " it "
            if result.timed_out:
                return where + "ran for longer than " + str(limits.oracle) + " s"
            if result.exit_code != 0:
                return (
                    where
                    + "exited with status "
                    + str(result.exit_code)
                    + ": "
                    + _tail(result.stderr)
                )
            if not tokens_equal(result.stdout, sample.output_text, float_tol):
                return (
                    where
                    + "printed "
                    + repr(normalize(result.stdout)[:120])
                    + " but the statement says "
                    + repr(normalize(sample.output_text)[:120])
                )
        return ""

    return check


def validator_check(
    samples: list[Sample], limits: Limits, calibration: Calibration
) -> Callable[[Path], str]:
    """The validator has to accept every sample and reject broken copies of them."""

    def check(path: Path) -> str:
        for sample in samples:
            result = sandbox.run_python(
                path, sample.input_text, timeout=limits.validator, limits=limits
            )
            if result.timed_out:
                return (
                    "on example "
                    + sample.name
                    + " it ran for longer than "
                    + str(limits.validator)
                    + " s"
                )
            if result.exit_code != 0:
                return (
                    "it rejected example "
                    + sample.name
                    + ", which the statement says is valid (status "
                    + str(result.exit_code)
                    + ", "
                    + _tail(result.stderr)
                    + ")"
                )
        tried = 0
        rejected = 0
        blind: list[str] = []
        for sample in samples:
            broken = mutations(sample.input_text)
            hits = 0
            for name, text in broken:
                tried += 1
                result = sandbox.run_python(
                    path, text, timeout=limits.validator, limits=limits
                )
                if result.exit_code != 0 or result.timed_out:
                    rejected += 1
                    hits += 1
            if broken and hits == 0:
                blind.append(sample.name)
        calibration.mutations = tried
        calibration.rejected = rejected
        if blind:
            return (
                "it accepted every broken copy of example "
                + ", ".join(blind)
                + ". Reject an input that has too few tokens, too many tokens, "
                "or a number outside the range the statement gives."
            )
        return ""

    return check


def generator_check(
    validator: Path, limits: Limits, smoke: Smoke
) -> Callable[[Path], str]:
    """The generator has to be seeded, valid every time, and varied."""

    def one(path: Path, seed: int, size: int, mode: str) -> tuple[str, str]:
        result = sandbox.run_python(
            path,
            args=[str(seed), str(size), mode],
            timeout=limits.generator,
            limits=limits,
        )
        label = (
            "with SEED " + str(seed) + " SIZE " + str(size) + " MODE " + mode + " it "
        )
        if result.timed_out:
            return "", label + "ran for longer than " + str(limits.generator) + " s"
        if result.exit_code != 0:
            return "", label + "exited with status " + str(result.exit_code) + ": " + _tail(
                result.stderr
            )
        if not result.stdout.strip():
            return "", label + "printed nothing"
        check = sandbox.run_python(
            validator, result.stdout, timeout=limits.validator, limits=limits
        )
        if check.exit_code != 0 or check.timed_out:
            return "", (
                label
                + "printed an input the validator rejects: "
                + repr(normalize(result.stdout)[:160])
            )
        return result.stdout, ""

    def check(path: Path) -> str:
        plan = [
            (seed, size, mode.value)
            for size in SMOKE_SIZES
            for mode in MODES
            for seed in SMOKE_SEEDS
        ]
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda item: one(path, *item), plan))
        printed = {key: text for key, (text, _) in zip(plan, results)}
        smoke.runs = len(plan)
        smoke.accepted = sum(1 for text, problem in results if text and not problem)
        smoke.distinct = len({text for text in printed.values() if text})
        for text, problem in results:
            if problem:
                return problem

        biggest = SMOKE_SIZES[-1]
        repeat, problem = one(path, SMOKE_SEEDS[0], biggest, MODES[1].value)
        if problem:
            return problem
        if repeat != printed[(SMOKE_SEEDS[0], biggest, MODES[1].value)]:
            return (
                "it printed two different inputs for the same SEED. Use "
                "random.Random(SEED) as the only source of randomness."
            )

        floor = max(2, smoke.runs // 3)
        if smoke.distinct < floor:
            return (
                "it printed only "
                + str(smoke.distinct)
                + " different inputs over "
                + str(smoke.runs)
                + " runs. Different seeds and different sizes must give "
                "different inputs."
            )

        # The seed has to change the input at a fixed size and mode. A
        # generator that reads SIZE and ignores SEED makes the stress run test
        # one input per size over and over, and leaves the shrinker nothing
        # smaller to find.
        for mode in MODES:
            spread = {printed[(seed, biggest, mode.value)] for seed in SMOKE_SEEDS}
            if len(spread) < 2:
                return (
                    "with SIZE "
                    + str(biggest)
                    + " and MODE "
                    + mode.value
                    + " every seed printed exactly the same input. SIZE sets "
                    "how big the input is; SEED has to decide what is in it. "
                    "Draw every value with the rng, inside the range the "
                    "statement allows."
                )
        return ""

    return check


# ---------------------------------------------------------------------------
# prompts


def _problem_block(statement: str, samples: list[Sample]) -> str:
    return (
        "Problem statement:\n---\n"
        + statement.strip()
        + "\n---\n\n"
        + samples_block(samples)
    )


def oracle_prompt(statement: str, samples: list[Sample], second: bool) -> str:
    body = (
        _problem_block(statement, samples)
        + "\nWrite a correct solution to this problem in Python 3. It reads the "
        "input from standard input and prints the answer to standard output in "
        "exactly the format the statement asks for, and nothing else.\n"
        "Correctness is the only thing that matters. Speed does not: every "
        "input it will be given is small.\n"
    )
    if second:
        body += (
            "Use a different algorithm from the most obvious one. If the obvious "
            "solution is exhaustive search or plain recursion, write a dynamic "
            "program or a direct formula instead. If the obvious solution is a "
            "dynamic program, write exhaustive search instead. This solution "
            "will be cross-checked against the obvious one, so it has to be "
            "independent of it and still correct.\n"
        )
    else:
        body += (
            "Write the simplest thing that is certainly correct, even if it is "
            "slow: exhaustive search is welcome.\n"
        )
    return body + IMPORT_RULE


def validator_prompt(statement: str, samples: list[Sample]) -> str:
    return (
        _problem_block(statement, samples)
        + "\nWrite an input validator for this problem in Python 3. It reads the "
        "whole input from standard input and exits with status 0 if that input "
        "obeys every format rule and every constraint the statement gives, and "
        "exits with status 1 otherwise. It prints nothing either way.\n"
        "Check the number of tokens, that each one is the type the statement "
        "says, that every number is inside its stated range, and any ordering, "
        "uniqueness, distinctness or sum condition the statement states. An "
        "input with a token missing, a token too many, or a number out of "
        "range must be rejected. The examples above must be accepted.\n"
        + IMPORT_RULE
    )


def generator_prompt(statement: str, samples: list[Sample]) -> str:
    return (
        _problem_block(statement, samples)
        + "\nWrite a random input generator for this problem in Python 3. It is "
        "run as:\n\n    gen.py SEED SIZE MODE\n\n"
        "SEED is an integer. Use random.Random(SEED) as the only source of "
        "randomness, so that the same three arguments always print exactly the "
        "same input. Do not call random.seed and do not use the module-level "
        "random functions.\n"
        "SIZE is an integer from 1 to 30 that scales the main dimension of the "
        "input. SIZE 1 is the smallest input the statement allows. SIZE 30 is a "
        "large one, scaled so that a simple exhaustive-search solution still "
        "finishes in about a second.\n"
        "SIZE decides how big the input is. SEED decides what is in it. Two "
        "runs with the same SIZE and MODE but different SEED values must print "
        "different inputs, so draw every value with the rng, inside the range "
        "the statement allows. Do not work a value out from SIZE alone.\n"
        "MODE is one of these three words:\n"
        "  tiny_values  small ranges, to force ties, duplicates and zeros\n"
        "  normal       ordinary inputs\n"
        "  extreme      values at the stated bounds, and negatives where the "
        "statement allows them\n"
        "It prints one complete input to standard output and nothing else. "
        "Every input it prints must obey every constraint in the statement, in "
        "every mode and at every size.\n" + IMPORT_RULE
    )


# ---------------------------------------------------------------------------
# putting it together


def synthesize(
    llm: LLM,
    statement: str,
    workdir: Path,
    samples: list[Sample] | None = None,
    oracle: Path | None = None,
    limits: Limits | None = None,
    float_tol: float | None = None,
    on_step: Callable[[str], None] | None = None,
) -> Tooling:
    """Produce the four scripts, each one checked by running it."""
    limits = limits or Limits()
    notes: list[str] = []
    dropped = 0

    def step(message: str) -> None:
        if on_step is not None:
            on_step(message)

    if samples is None:
        step("reading the examples")
        samples, dropped = extract_samples(llm, statement)
        if dropped:
            notes.append(
                str(dropped)
                + " extracted example(s) did not appear in the statement word "
                "for word and were discarded"
            )
    if not samples:
        raise SynthError(
            "no usable examples. Point --samples at a directory of 1.in / 1.out "
            "files, or use a statement that shows its examples."
        )

    check_oracle = oracle_check(samples, limits, float_tol)
    calibration = Calibration()
    smoke = Smoke()
    paths: dict[str, Path] = {}

    if oracle is not None:
        step("checking your reference solution")
        source = oracle.read_text(encoding="utf-8")
        path = sandbox.write_script(workdir, "oracle_a.py", source)
        problem = check_oracle(path)
        if problem:
            raise SynthError(
                "the reference solution given with --oracle does not match the "
                "examples: " + problem
            )
        oracle_a = Script(purpose="oracle_a", source=source, notes=["given with --oracle"])
        oracle_b = None
        paths["oracle_a"] = path
    else:
        step("writing reference A")
        oracle_a = build_script(
            llm,
            "oracle_a",
            oracle_prompt(statement, samples, second=False),
            "oracle_a.py",
            workdir,
            check_oracle,
        )
        paths["oracle_a"] = workdir / "oracle_a.py"
        step("writing reference B")
        oracle_b = build_script(
            llm,
            "oracle_b",
            oracle_prompt(statement, samples, second=True),
            "oracle_b.py",
            workdir,
            check_oracle,
        )
        paths["oracle_b"] = workdir / "oracle_b.py"

    step("writing the validator")
    try:
        validator = build_script(
            llm,
            "validator",
            validator_prompt(statement, samples),
            "validator.py",
            workdir,
            validator_check(samples, limits, calibration),
        )
    except SynthError as exc:
        # A validator that accepts everything still lets the run go ahead; it
        # just means the trust section has to say so. A validator that will
        # not run at all does not.
        if "accepted every broken copy" not in str(exc):
            raise
        calibration.weak = True
        notes.append(
            "weak validator: it accepted every broken copy of at least one "
            "example, so an invalid generated input could be tested as if it "
            "were valid"
        )
        validator = Script(
            purpose="validator",
            source=(workdir / "validator.py").read_text(encoding="utf-8"),
            repairs=MAX_REPAIRS,
            notes=["weak"],
        )
    paths["validator"] = workdir / "validator.py"

    step("writing the generator")
    generator = build_script(
        llm,
        "generator",
        generator_prompt(statement, samples),
        "gen.py",
        workdir,
        generator_check(paths["validator"], limits, smoke),
    )
    paths["generator"] = workdir / "gen.py"

    return Tooling(
        samples=samples,
        oracle_a=oracle_a,
        oracle_b=oracle_b,
        validator=validator,
        generator=generator,
        dropped_samples=dropped,
        trusted_oracle=oracle is not None,
        calibration=calibration,
        smoke=smoke,
        notes=notes,
        paths=paths,
    )
