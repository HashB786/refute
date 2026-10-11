"""Plain data records shared by every phase of the pipeline.

Nothing here runs code or calls a model; these are the values that the
synth, stress, shrink, fix and report modules pass between themselves and
that end up in report.json.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any


class Verdict(str, Enum):
    """Outcome of running the target against the oracles on one input."""

    PASS = "PASS"
    WA = "WA"
    RE = "RE"
    TLE = "TLE"
    ORACLE_CONFLICT = "ORACLE_CONFLICT"
    ORACLE_TIMEOUT = "ORACLE_TIMEOUT"
    INVALID = "INVALID"

    def is_failure(self) -> bool:
        """True for verdicts that accuse the target (not the tooling)."""
        return self in (Verdict.WA, Verdict.RE, Verdict.TLE)

    def is_skipped(self) -> bool:
        """True for verdicts that are counted but prove nothing about the target."""
        return self in (
            Verdict.ORACLE_CONFLICT,
            Verdict.ORACLE_TIMEOUT,
            Verdict.INVALID,
        )


class Mode(str, Enum):
    """Generator mode. The generator takes this as its third argument."""

    TINY_VALUES = "tiny_values"
    NORMAL = "normal"
    EXTREME = "extreme"


MODES: tuple[Mode, ...] = (Mode.TINY_VALUES, Mode.NORMAL, Mode.EXTREME)

#: Purposes a model call can have. FakeLLM in the tests answers by purpose.
PURPOSES: tuple[str, ...] = (
    "samples",
    "oracle_a",
    "oracle_b",
    "validator",
    "generator",
    "diagnose",
    "fix",
)


@dataclass(frozen=True)
class RunResult:
    """What came back from one sandboxed subprocess."""

    exit_code: int
    stdout: str
    stderr: str
    duration: float
    timed_out: bool = False
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass(frozen=True)
class Usage:
    """Token counts for one model call, from the response or estimated."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class LLMResponse:
    """One model reply, plus where it came from."""

    text: str
    purpose: str
    model: str
    provider: str
    usage: Usage
    cached: bool = False
    finish_reason: str = "stop"
    latency: float = 0.0
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Sample:
    """One statement example, kept only if it appears verbatim in the statement."""

    name: str
    input_text: str
    output_text: str
    source: str  # "samples_dir" or "model_extracted"


@dataclass
class Script:
    """A model-written Python script that passed the guard and ran."""

    purpose: str
    source: str
    repairs: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass
class TestRecord:
    """One stress test, recorded so the report can show the whole run."""

    index: int
    size: int
    mode: str
    seed: int
    verdict: str
    duration: float = 0.0
    oracle_b_run: bool = False


@dataclass
class ShrinkStep:
    """One accepted shrink step, kept so shrink.json shows the whole path."""

    step: int
    action: str
    input_text: str
    size_tokens: int


@dataclass
class Counterexample:
    """A confirmed failure: both oracles agreed and the target disagreed."""

    input_text: str
    expected: str
    actual: str
    verdict: str
    size: int
    mode: str
    seed: int


@dataclass
class Trust:
    """Everything the report needs to say how much the run is worth."""

    tests_run: int = 0
    largest_size: int = 0
    oracle_checks: int = 0
    oracle_agreements: int = 0
    validator_rejections: int = 0
    validator_mutations: int = 0
    weak_validator: bool = False
    skipped: dict[str, int] = field(default_factory=dict)
    model_calls: int = 0
    model_tokens: int = 0

    @property
    def oracle_agreement(self) -> float | None:
        if self.oracle_checks == 0:
            return None
        return self.oracle_agreements / self.oracle_checks


@dataclass
class Report:
    """The whole run. Serialized to report.json and rendered to report.html."""

    problem: str
    target: str
    refuted: bool
    counterexample: Counterexample | None = None
    tests: list[TestRecord] = field(default_factory=list)
    shrink_steps: list[ShrinkStep] = field(default_factory=list)
    trust: Trust = field(default_factory=Trust)
    diagnosis: str = ""
    fix_source: str = ""
    fix_verified: bool = False
    offline: bool = False
    started_at: float = field(default_factory=time.time)
    elapsed: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["oracle_agreement"] = self.trust.oracle_agreement
        return data


@dataclass(frozen=True)
class Limits:
    """Subprocess timeouts and caps. Seconds, except memory_mb and output_cap."""

    validator: float = 5.0
    generator: float = 5.0
    oracle: float = 10.0
    target: float = 2.0
    memory_mb: int = 512
    output_cap: int = 1024 * 1024


@dataclass(frozen=True)
class Budget:
    """Stop conditions for the stress and shrink phases."""

    max_tests: int = 600
    max_seconds: float = 90.0
    shrink_seconds: float = 90.0
    fix_stress_tests: int = 200


def out_paths(out_dir: Path) -> dict[str, Path]:
    """The fixed set of files a run writes into its output directory."""
    return {
        "report_html": out_dir / "report.html",
        "report_json": out_dir / "report.json",
        "counterexample": out_dir / "counterexample.in",
        "expected": out_dir / "expected.out",
        "actual": out_dir / "actual.out",
        "shrink": out_dir / "shrink.json",
    }
