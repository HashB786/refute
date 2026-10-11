"""The `refute` command.

`refute ping` checks the model configuration. `refute run` is the pipeline:
examples, tooling, stress, shrink. The console output follows DESIGN.md:
ASCII only, at most 80 columns, plain text for Refute's own words, blue for
what the target printed, red only on the mismatch, and parentheses around
every differing token so the meaning survives without color.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from dataclasses import asdict
from pathlib import Path

from rich.console import Console
from rich.text import Text

from . import __version__, sandbox
from .bench import load_problems, run_bench
from .fix import explain as explain_failure
from .llm import CacheMiss, Client, LLMError, Settings, build_client
from .models import Budget, Counterexample, Limits, Report, Trust, Verdict, out_paths
from .report import differing
from .report import finding as page_finding
from .report import write as write_report
from .shrink import ShrinkOutcome, shrink
from .stress import StressOutcome, Tools, run_stress
from .synth import SynthError, Tooling, load_samples, synthesize

#: The recorded example `refute demo` replays. Resolved against the package,
#: not the working directory, so the demo works from anywhere in the repo.
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
DEMO_PROBLEM = EXAMPLES / "coin-change"
DEMO_CACHE = EXAMPLES / "cache"

#: Where the benchmark's problems and its write-up live.
BENCH = Path(__file__).resolve().parent.parent / "bench"

PING_PROMPT = "Reply with exactly: refute ping ok"
LABEL = 10
HERO_LABEL = 8
WIDTH = 80
MAX_HERO_LINES = 8

BLUE = "blue"
RED = "red"
DIM = "dim"


def make_console() -> Console:
    """One console shape for the whole program: 80 columns, no auto-coloring.

    highlight and markup are both off because target output and model text go
    through here; neither should be able to introduce a color or be read as
    rich markup.
    """
    return Console(width=WIDTH, highlight=False, markup=False)


# ---------------------------------------------------------------------------
# the console shapes


def row(console: Console, label: str, body: str | Text, style: str | None = None) -> None:
    """A top-level status line: `label` then the text, aligned at column 11."""
    text = Text(label.ljust(LABEL) + " ", style=DIM)
    text.append(body if isinstance(body, Text) else Text(body, style=style or ""))
    console.print(text)


def hero_row(console: Console, label: str, body: Text) -> None:
    """An indented row of the input or output block, aligned at column 12."""
    text = Text("  " + label.ljust(HERO_LABEL) + "  ", style=DIM)
    text.append(body)
    console.print(text)


class Status:
    """One line rewritten in place while stress runs, and only on a terminal."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self.showing = False

    def update(self, label: str, body: str) -> None:
        if not self.console.is_terminal:
            return
        line = (label.ljust(LABEL) + " " + body)[: WIDTH - 1]
        self.console.file.write("\r" + line.ljust(WIDTH - 1))
        self.console.file.flush()
        self.showing = True

    def clear(self) -> None:
        if self.showing:
            self.console.file.write("\r" + " " * (WIDTH - 1) + "\r")
            self.console.file.flush()
            self.showing = False


def plural(count: int, word: str) -> str:
    return str(count) + " " + word + ("" if count == 1 else "s")


def program_text(body: str, marked: set[int] | None = None) -> list[Text]:
    """Program output as lines, blue, with differing tokens in red parentheses.

    The parentheses are not decoration. With NO_COLOR, or in a log, they are
    the only thing left that says which number is wrong.
    """
    marked = marked or set()
    lines: list[Text] = []
    index = 0
    for raw in body.splitlines() or [""]:
        line = Text()
        for position, token in enumerate(raw.split()):
            if position:
                line.append(" ")
            if index in marked:
                line.append("(" + token + ")", style=RED)
            else:
                line.append(token, style=BLUE)
            index += 1
        lines.append(line)
    return lines


def capped(lines: list[Text]) -> list[Text]:
    """At most eight lines, with a plain last line saying there was more."""
    if len(lines) <= MAX_HERO_LINES:
        return lines
    return lines[: MAX_HERO_LINES - 1] + [Text("...", style=DIM)]


def finding(name: str, found: Counterexample, limits: Limits) -> str:
    """The console's finding: the page's sentence with "Refuted." in front.

    The page opens with the finding itself, as DESIGN.md asks. The console
    says plainly that something was refuted before saying what.
    """
    return "Refuted. " + page_finding(name, found, limits.target)


# ---------------------------------------------------------------------------
# ping


def ping(client: Client, console: Console) -> int:
    """Make one model call and print what the endpoint said about it."""
    row(console, "calling", client.settings.provider, style=DIM)
    try:
        # Not cached unless --offline: a ping answered from disk could not
        # tell a working key from an expired one.
        response = client.complete("samples", PING_PROMPT, use_cache=False)
    except CacheMiss as exc:
        row(console, "offline", str(exc))
        return 1
    except LLMError as exc:
        row(console, "failed", str(exc))
        return 1

    usage = response.usage
    row(console, "provider", response.provider or client.settings.provider)
    row(console, "endpoint", client.endpoint())
    row(console, "model", response.model or client.settings.model)
    if response.cached:
        row(console, "source", "cache, no call was made")
    else:
        row(console, "latency", format(response.latency, ".2f") + " s")
    row(
        console,
        "tokens",
        str(usage.prompt_tokens)
        + " prompt, "
        + str(usage.completion_tokens)
        + " completion, "
        + ("estimated" if usage.estimated else "reported"),
    )
    first = response.text.strip().splitlines()
    row(console, "reply", first[0][:60] if first else "nothing")
    if response.headers:
        console.print("")
        for key in sorted(response.headers):
            row(console, key, response.headers[key], style=DIM)
    elif not response.cached:
        row(console, "limits", "no x-ratelimit-* headers were sent", style=DIM)
    return 0


# ---------------------------------------------------------------------------
# run


def tooling_summary(tooling: Tooling) -> str:
    """"reference A ok, reference B ok, validator ok, generator ok"."""
    parts: list[str] = []
    pieces = [
        ("your reference" if tooling.trusted_oracle else "reference A", tooling.oracle_a),
        ("reference B", tooling.oracle_b),
        ("validator", tooling.validator),
        ("generator", tooling.generator),
    ]
    for name, script in pieces:
        if script is None:
            continue
        state = "weak" if "weak" in script.notes else "ok"
        if script.repairs and state == "ok":
            state += " (" + plural(script.repairs, "repair") + ")"
        parts.append(name + " " + state)
    return ", ".join(parts)


def target_command(solution: Path, workdir: Path, console: Console) -> list[str] | None:
    """Copy the target into the workspace and return the command that runs it."""
    if solution.suffix == ".py":
        copied = workdir / "target.py"
        shutil.copyfile(solution, copied)
        return sandbox.command_for(copied)
    if solution.suffix in (".cpp", ".cc", ".cxx"):
        binary, problem = sandbox.compile_cpp(solution, workdir)
        if binary is None:
            row(console, "target", "it did not compile: " + problem)
            return None
        return [str(binary)]
    row(console, "target", "only .py and .cpp targets can be run, not " + solution.suffix)
    return None


def write_outputs(
    out_dir: Path,
    found: Counterexample | None,
    shrunk: ShrinkOutcome | None,
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = out_paths(out_dir)
    if found is not None:
        paths["counterexample"].write_text(found.input_text, encoding="utf-8")
        paths["expected"].write_text(found.expected, encoding="utf-8")
        paths["actual"].write_text(found.actual, encoding="utf-8")
    if shrunk is not None:
        paths["shrink"].write_text(
            json.dumps(
                {
                    "steps": [asdict(step) for step in shrunk.steps],
                    "stable": shrunk.stable,
                    "rechecks": shrunk.rechecks,
                    "seconds": round(shrunk.elapsed, 3),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return paths


def short_path(path: Path) -> str:
    """A path relative to where the command was run, when it is under it.

    `refute demo` resolves the example against the package, so without this
    the command to repeat the failure is an absolute path that wraps over
    three lines of an eighty column terminal.
    """
    try:
        return path.resolve().relative_to(Path.cwd()).as_posix()
    except ValueError:
        return path.as_posix()


def run_command(solution: Path, paths: dict[str, Path]) -> str:
    """The command that repeats the failure, in the form the report shows it."""
    head = "python " if solution.suffix == ".py" else ""
    return head + short_path(solution) + " < " + short_path(paths["counterexample"])


def build_report(
    problem: str,
    solution: Path,
    outcome: StressOutcome,
    shrunk: ShrinkOutcome | None,
    tooling: Tooling,
    told,
    client,
    limits: Limits,
    command: str,
    offline: bool,
    wall_started: float,
    elapsed: float,
) -> Report:
    """Gather the whole run into the record that report.py renders."""
    found = shrunk.counterexample if shrunk is not None else outcome.failure
    calibration = tooling.calibration
    trust = Trust(
        tests_run=len(outcome.tests),
        largest_size=outcome.largest_size,
        largest_tokens=outcome.largest_tokens,
        oracle_checks=outcome.oracle_checks,
        oracle_agreements=outcome.oracle_agreements,
        validator_rejections=calibration.rejected,
        validator_mutations=calibration.mutations,
        weak_validator=calibration.weak,
        samples=len(tooling.samples),
        samples_calibrated=calibration.samples_rejecting,
        smoke_runs=tooling.smoke.runs,
        smoke_accepted=tooling.smoke.accepted,
        trusted_oracle=tooling.trusted_oracle,
        skipped=dict(outcome.skipped),
        model=getattr(client, "model", ""),
        model_calls=client.calls + client.cached,
        model_cached=client.cached,
        model_tokens=client.tokens,
    )
    return Report(
        problem=problem,
        target=solution.name,
        refuted=found is not None,
        counterexample=found,
        tests=outcome.tests,
        shrink_steps=shrunk.steps if shrunk is not None else [],
        trust=trust,
        diagnosis=told.diagnosis if told is not None else "",
        fix_source=told.source if told is not None else "",
        fix_verified=told.verified if told is not None else False,
        fix_reason=told.reason if told is not None else "",
        target_source=solution.read_text(encoding="utf-8"),
        command=command,
        stable=shrunk.stable if shrunk is not None else True,
        target_timeout=limits.target,
        version=__version__,
        offline=offline,
        started_at=wall_started,
        elapsed=elapsed,
        notes=list(tooling.notes),
    )


def report_refuted(
    console: Console,
    solution: Path,
    found: Counterexample,
    limits: Limits,
    float_tol: float | None,
    paths: dict[str, Path],
) -> None:
    """The finding, then the input and the two answers, then how to repeat it."""
    console.print("")
    console.print(finding(solution.name, found, limits))
    console.print("")

    raw = found.input_text.splitlines() or [""]
    for index, line in enumerate(capped([Text(one) for one in raw])):
        hero_row(console, "input" if index == 0 else "", line)

    if found.verdict == Verdict.RE.value:
        tail = [one for one in found.actual.splitlines() if one.strip()]
        hero_row(console, "error", Text(tail[-1][:60] if tail else "no message", style=BLUE))
    elif found.verdict == Verdict.TLE.value:
        hero_row(
            console,
            "time",
            Text("longer than " + format(limits.target, "g") + " s", style=BLUE),
        )
    else:
        expected = capped(program_text(found.expected))
        for index, line in enumerate(expected):
            hero_row(console, "expected" if index == 0 else "", Text(line.plain))
        marked = differing(found.actual, found.expected, float_tol)
        for index, line in enumerate(capped(program_text(found.actual, marked))):
            hero_row(console, "got" if index == 0 else "", line)

    console.print("")
    hero_row(console, "run it", Text(run_command(solution, paths)))
    if paths["report_html"].is_file():
        hero_row(console, "report", Text(paths["report_html"].as_posix()))


def report_not_refuted(console: Console, outcome: StressOutcome) -> None:
    console.print("")
    console.print(
        "Not refuted. "
        + plural(len(outcome.tests), "test")
        + ", inputs up to "
        + plural(outcome.largest_tokens, "number")
        + ", no failure. That is evidence, not proof."
    )


def run(args: argparse.Namespace, console: Console) -> int:
    """The whole pipeline, with one status line per stage."""
    statement_path: Path = args.statement
    solution: Path = args.solution
    if not statement_path.is_file():
        row(console, "statement", "there is no file at " + str(statement_path))
        return 1
    if not solution.is_file():
        row(console, "solution", "there is no file at " + str(solution))
        return 1

    statement = statement_path.read_text(encoding="utf-8")
    problem = statement_path.parent.name or statement_path.stem
    limits = Limits()
    budget = Budget(max_tests=args.budget) if args.budget else Budget()
    out_dir = Path("refute-out")

    console.print("refute " + __version__ + "  " + problem + "  " + solution.name)
    console.print("")

    try:
        client = build_client(cache_dir=args.cache_dir, offline=args.offline)
    except LLMError as exc:
        row(console, "config", str(exc))
        return 1

    samples = None
    if args.samples is not None:
        try:
            samples = load_samples(args.samples)
        except SynthError as exc:
            row(console, "samples", str(exc))
            return 1
        row(console, "samples", plural(len(samples), "example") + " given with --samples")

    status = Status(console)
    started = time.monotonic()
    wall_started = time.time()
    with sandbox.workspace("refute-run-") as workdir:
        try:
            tooling = synthesize(
                client,
                statement,
                workdir,
                samples=samples,
                oracle=args.oracle,
                limits=limits,
                float_tol=args.float_tol,
                on_step=lambda message: status.update("tooling", message),
            )
        except (SynthError, LLMError) as exc:
            status.clear()
            row(console, "tooling", str(exc))
            return 1
        status.clear()

        if samples is None:
            row(
                console,
                "samples",
                str(len(tooling.samples) + tooling.dropped_samples)
                + " found, "
                + str(len(tooling.samples))
                + " checked against the statement",
            )
        row(console, "tooling", tooling_summary(tooling))

        command = target_command(solution, workdir, console)
        if command is None:
            return 1

        tools = Tools(
            workdir=workdir,
            generator=tooling.paths["generator"],
            validator=tooling.paths["validator"],
            oracle_a=tooling.paths["oracle_a"],
            oracle_b=tooling.paths.get("oracle_b"),
            target=command,
            limits=limits,
            float_tol=args.float_tol,
        )

        outcome = run_stress(
            tools,
            budget,
            on_progress=lambda state: status.update(
                "stress", plural(len(state.tests), "test") + " so far"
            ),
        )
        status.clear()
        row(
            console,
            "stress",
            plural(len(outcome.tests), "test")
            + ", "
            + (plural(outcome.failures, "failure") if outcome.failures else "no failure"),
        )

        shrunk = None
        if outcome.failure is not None:
            status.update("shrink", "making the input smaller")
            shrunk = shrink(tools, outcome.failure, budget)
            status.clear()
            row(
                console,
                "shrink",
                plural(shrunk.start_tokens, "number")
                + " -> "
                + plural(shrunk.final_tokens, "number")
                + " in "
                + plural(max(0, len(shrunk.steps) - 1), "step"),
            )
            if not shrunk.stable:
                row(
                    console,
                    "warning",
                    "the input did not fail every time it was re-run",
                    style=RED,
                )

        found = shrunk.counterexample if shrunk is not None else outcome.failure

        told = None
        if found is not None and not args.no_ai_explain:
            status.update("explain", "asking the model what went wrong")
            trouble = ""
            try:
                told = explain_failure(
                    client,
                    statement,
                    solution,
                    found,
                    tools,
                    tooling.samples,
                    limits,
                    budget,
                )
            except CacheMiss:
                # The prompts carry the counterexample, so a recorded run only
                # has an answer for the input it recorded. Shrinking is time
                # boxed and can land somewhere else on a slower machine.
                trouble = "no recorded answer for this counterexample"
            except (SynthError, LLMError) as exc:
                trouble = str(exc)
            status.clear()
            if trouble:
                row(console, "explain", trouble, style=DIM)
            elif told.verified:
                row(console, "explain", "diagnosis written, fix verified")
            elif told.source:
                row(console, "explain", "diagnosis written, fix unverified: " + told.reason)
            else:
                row(console, "explain", "diagnosis written, no usable fix")

        paths = write_outputs(out_dir, found, shrunk)
        paper = build_report(
            problem=problem,
            solution=solution,
            outcome=outcome,
            shrunk=shrunk,
            tooling=tooling,
            told=told,
            client=client,
            limits=limits,
            command=run_command(solution, paths),
            offline=args.offline,
            wall_started=wall_started,
            elapsed=time.monotonic() - started,
        )
        write_report(out_dir, paper, args.float_tol)

        if found is not None:
            report_refuted(console, solution, found, limits, args.float_tol, paths)
        else:
            report_not_refuted(console, outcome)

    console.print("")
    row(
        console,
        "time",
        format(time.monotonic() - started, ".1f")
        + " s, "
        + plural(client.calls, "model call")
        + ", "
        + str(client.tokens)
        + " tokens",
        style=DIM,
    )
    return 0 if found is not None else 2


# ---------------------------------------------------------------------------
# parser


def demo(console: Console) -> int:
    """Replay the recorded example with no network and no key.

    The model output is read back from examples/cache. Everything else is
    done again for real: the references, the validator, the generator and
    the target are all run as subprocesses here and now.
    """
    if not (DEMO_PROBLEM / "statement.md").is_file():
        row(console, "demo", "the example is missing from " + DEMO_PROBLEM.as_posix())
        return 1
    if not DEMO_CACHE.is_dir():
        row(console, "demo", "no recorded model output in " + DEMO_CACHE.as_posix())
        return 1
    row(
        console,
        "demo",
        "replaying recorded model output; every program here is really run",
        style=DIM,
    )
    console.print("")
    return run(
        argparse.Namespace(
            statement=DEMO_PROBLEM / "statement.md",
            solution=DEMO_PROBLEM / "greedy.py",
            oracle=None,
            samples=DEMO_PROBLEM / "samples",
            float_tol=None,
            no_ai_explain=False,
            budget=None,
            cache_dir=DEMO_CACHE,
            offline=True,
        ),
        console,
    )


def bench(args: argparse.Namespace, console: Console) -> int:
    """Run Refute against every planted bug and write up what happened."""
    root = BENCH
    if not (root / "problems").is_dir():
        row(console, "bench", "no problems in " + (root / "problems").as_posix())
        return 1
    try:
        client = build_client(cache_dir=args.cache_dir, offline=args.offline)
    except LLMError as exc:
        row(console, "config", str(exc))
        return 1

    problems = load_problems(root)
    planted = sum(len(problem.bugs) for problem in problems)
    console.print(
        "refute "
        + __version__
        + "  bench  "
        + plural(len(problems), "problem")
        + ", "
        + plural(planted, "bug")
    )
    console.print("")

    status = Status(console)
    started = time.monotonic()
    record = run_bench(
        root,
        client,
        resume=args.resume,
        on_step=lambda slug, message: status.update(slug, message),
    )
    status.clear()

    for problem in problems:
        pairs = [pair for pair in record.pairs if pair.problem == problem.slug]
        if not pairs:
            continue
        found = sum(1 for pair in pairs if pair.found)
        row(
            console,
            problem.slug,
            str(found) + " of " + str(len(pairs)) + " found",
        )
    console.print("")
    total = len(record.pairs)
    found = sum(1 for pair in record.pairs if pair.found)
    console.print("Found " + str(found) + " of " + str(total) + " planted bugs.")
    console.print("")
    hero_row(console, "results", Text(short_path(root / "RESULTS.md")))
    hero_row(console, "record", Text(short_path(root / "results.json")))
    console.print("")
    row(
        console,
        "time",
        format(time.monotonic() - started, ".0f")
        + " s, "
        + plural(client.calls + client.cached, "model call"),
        style=DIM,
    )
    return 0 if found == total else 2


def settings_line(settings: Settings) -> str:
    """The configuration in one line, with no key in it."""
    return (
        settings.provider
        + ", model "
        + (settings.model or "unset")
        + ", key "
        + ("set" if settings.api_key else "missing")
        + ", "
        + str(settings.max_rpm)
        + " rpm / "
        + str(settings.max_tpm)
        + " tpm"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="refute",
        description=(
            "Find the smallest input that proves a program wrong. "
            "Refute runs untrusted code; the Dockerfile with --network none "
            "is the recommended way to run it."
        ),
    )
    sub = parser.add_subparsers(dest="command")

    ping_parser = sub.add_parser("ping", help="check the model configuration")
    ping_parser.add_argument("--cache-dir", type=Path, default=None)
    ping_parser.add_argument(
        "--offline", action="store_true", help="use the cache only, never the network"
    )

    run_parser = sub.add_parser("run", help="look for an input that the target gets wrong")
    run_parser.add_argument("statement", type=Path)
    run_parser.add_argument("solution", type=Path)
    run_parser.add_argument("--oracle", type=Path, default=None)
    run_parser.add_argument("--samples", type=Path, default=None)
    run_parser.add_argument("--float-tol", type=float, default=None)
    run_parser.add_argument("--no-ai-explain", action="store_true")
    run_parser.add_argument("--budget", type=int, default=None)
    run_parser.add_argument("--cache-dir", type=Path, default=None)
    run_parser.add_argument("--offline", action="store_true")

    sub.add_parser("demo", help="replay the recorded example offline (phase 2)")
    bench_parser = sub.add_parser(
        "bench", help="run Refute against every planted bug in bench/problems"
    )
    bench_parser.add_argument(
        "--resume", action="store_true", help="skip the pairs already recorded"
    )
    bench_parser.add_argument("--cache-dir", type=Path, default=None)
    bench_parser.add_argument("--offline", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    console = make_console()

    if args.command == "ping":
        try:
            client = build_client(cache_dir=args.cache_dir, offline=args.offline)
        except LLMError as exc:
            row(console, "config", str(exc))
            return 1
        console.print(settings_line(client.settings), style=DIM)
        return ping(client, console)

    if args.command == "run":
        return run(args, console)

    if args.command == "demo":
        return demo(console)

    if args.command == "bench":
        return bench(args, console)

    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
