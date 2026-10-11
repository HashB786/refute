"""Builds report.html and report.json from a finished run.

docs/reference/report.html is the visual source of truth: this module
reproduces its markup and class names and fills them with real data. The CSS
comes from refute/style.py, which is a verbatim copy of the reference's.
Nothing from the reference's own numbers or sentences is carried over; that
file is placeholder data from a toy run.

The page is one file. No script, no network, no web fonts.
"""

from __future__ import annotations

import difflib
import html
import json
import math
import re
import time
from dataclasses import asdict
from pathlib import Path

from .models import Counterexample, Report, ShrinkStep, TestRecord, Verdict, out_paths
from .style import CSS

MAX_LINES = 8
MAX_CIRCLES = 3
MAX_STEP_ROWS = 12
MAX_BARS = 160
SHORT_VALUE = 24

#: The one hand-drawn circle, taken from the reference. Every circle on the
#: page is this same path, moved and stretched by --x, --y and --w.
CIRCLE_PATH = (
    "M8 27C6 10 28 3 52 4C80 5 97 14 94 30C91 47 66 57 42 56C18 55 3 44 5 28"
    "C6 17 18 9 33 7"
)

#: The squared sheet is 28px to a square; a bar strip is 5 units to a bar.
BAR_PITCH = 5
BAR_WIDTH = 3
BAR_FLOOR = 8.0
BAR_SPAN = 28.1
BAR_BASE = 58.0
STRIP_HEIGHT = 64
AXIS_Y = 60

_WORD = re.compile(r"\S+")


def esc(text: str) -> str:
    """Escape text for the page. Everything that reaches the page goes through here."""
    return html.escape(text, quote=False)


def plural(count: int, word: str) -> str:
    return str(count) + " " + word + ("" if count == 1 else "s")


# ---------------------------------------------------------------------------
# the sentences


def is_short(value: str) -> bool:
    flat = value.strip()
    return bool(flat) and "\n" not in flat and len(flat) <= SHORT_VALUE


def finding(name: str, found: Counterexample, timeout: float = 2.0) -> str:
    """The h1: what was proved, as one sentence.

    The console puts "Refuted." in front of this; the page opens with the
    finding itself.
    """
    if found.verdict == Verdict.RE.value:
        return name + " crashes on this input."
    if found.verdict == Verdict.TLE.value:
        return name + " takes longer than " + format(timeout, "g") + " s on this input."
    got = found.actual.strip()
    want = found.expected.strip()
    if is_short(got) and is_short(want):
        return name + " prints " + got + ". The right answer is " + want + "."
    return name + " gives a wrong answer on this input."


def differing(got: str, want: str, float_tol: float | None = None) -> set[int]:
    """Indices of the tokens in `got` that do not match `want`."""
    from .stress import tokens_equal

    left = got.split()
    right = want.split()
    out: set[int] = set()
    for index, token in enumerate(left):
        if index >= len(right):
            out.add(index)
        elif not tokens_equal(token, right[index], float_tol):
            out.add(index)
    return out


def _lede(report: Report) -> str:
    found = report.counterexample
    if found is None:
        return (
            plural(len(report.tests), "test")
            + ", inputs up to "
            + plural(report.trust.largest_tokens, "number")
            + ", and "
            + report.target
            + " matched the reference solutions every time. That is evidence, "
            "not proof."
        )
    text = (
        "This is the smallest failing input Refute found, after "
        + plural(len(report.tests), "test")
        + ". "
    )
    answer = found.expected.strip()
    shown = answer if is_short(answer) else "the same answer"
    if report.trust.trusted_oracle:
        text += "Your reference solution prints " + shown + "."
    else:
        text += "Two independent reference solutions both print " + shown + "."
    extra = len(differing(found.actual, found.expected)) - MAX_CIRCLES
    if extra > 0:
        text += " There are " + plural(extra, "more difference") + "."
    return text


# ---------------------------------------------------------------------------
# the squared sheet


def _places(text: str) -> list[tuple[int, int, int]]:
    """(row, column, length) for every token, in order, in squares."""
    out: list[tuple[int, int, int]] = []
    for row, line in enumerate(text.splitlines()):
        for match in _WORD.finditer(line):
            out.append((row, match.start(), len(match.group())))
    return out


def _lines(text: str) -> list[str]:
    """The lines of a block, cut to eight with a last line saying there was more."""
    lines = text.splitlines() or [""]
    if len(lines) <= MAX_LINES:
        return lines
    return lines[: MAX_LINES - 1] + ["..."]


def _cells(text: str, circles: str = "") -> str:
    body = "".join(
        '<div class="line">' + esc(line) + "</div>" for line in _lines(text)
    )
    return '<div class="cells">' + body + circles + "</div>"


def _row(label: str, text: str, classes: str = "", circles: str = "") -> str:
    opening = '<div class="row ' + classes + '">' if classes else '<div class="row">'
    return (
        "  "
        + opening
        + '<span class="label">'
        + esc(label)
        + "</span>"
        + _cells(text, circles)
        + "</div>"
    )


def _circles(found: Counterexample, float_tol: float | None) -> str:
    """One circle per differing token on the got row, at most three."""
    marked = sorted(differing(found.actual, found.expected, float_tol))
    places = _places(found.actual)
    out: list[str] = []
    for index in marked[:MAX_CIRCLES]:
        if index >= len(places):
            continue
        row, column, width = places[index]
        if row >= MAX_LINES:
            continue
        out.append(
            '<svg class="circle" viewBox="0 0 100 60" preserveAspectRatio="none"'
            ' aria-hidden="true" focusable="false" style="--x:'
            + str(column)
            + ";--y:"
            + str(row)
            + ";--w:"
            + str(width)
            + '"><path d="'
            + CIRCLE_PATH
            + '"/></svg>'
        )
    return "".join(out)


def hero(found: Counterexample, float_tol: float | None, timeout: float) -> str:
    """The input and the two answers, with the mismatch circled."""
    rows = [_row("input", found.input_text)]
    rows.append('  <div class="gap"></div>')
    if found.verdict == Verdict.RE.value:
        message = [line for line in found.actual.splitlines() if line.strip()]
        rows.append(_row("error", message[-1] if message else "no message", "got"))
    elif found.verdict == Verdict.TLE.value:
        rows.append(
            _row("time", "longer than " + format(timeout, "g") + " s", "got")
        )
    else:
        rows.append(_row("expected", found.expected))
        rows.append(_row("got", found.actual, "got", _circles(found, float_tol)))
    return (
        '<figure class="sheet hero" tabindex="0" aria-label="The smallest failing'
        ' input and the two outputs">\n'
        + "\n".join(rows)
        + "\n</figure>"
    )


# ---------------------------------------------------------------------------
# how it got small


NL = "\n"


def _stream(text: str) -> list[str]:
    """An input as one sequence of tokens, with newlines kept as their own item."""
    out: list[str] = []
    for index, line in enumerate(text.splitlines()):
        if index:
            out.append(NL)
        out.extend(line.split())
    return out


def _struck(current: list[str], following: list[str]) -> set[int]:
    """Positions in `current` that the next step removed or changed."""
    out: set[int] = set()
    matcher = difflib.SequenceMatcher(a=current, b=following, autojunk=False)
    for tag, start, end, _, _ in matcher.get_opcodes():
        if tag in ("delete", "replace"):
            out.update(range(start, end))
    return out


def _step_line(stream: list[str], struck: set[int]) -> str:
    parts: list[str] = []
    for index, token in enumerate(stream):
        if token == NL:
            parts.append('<span class="nl">/</span>')
        elif index in struck:
            parts.append("<s>" + esc(token) + "</s>")
        else:
            parts.append(esc(token))
    return '<div class="line">' + " ".join(parts) + "</div>"


def _step_row(label: str, line: str) -> str:
    return (
        '  <div class="row"><span class="label">'
        + esc(label)
        + '</span><div class="cells">'
        + line
        + "</div></div>"
    )


def staircase(steps: list[ShrinkStep]) -> str:
    """One row per accepted step, with what the next step took away struck out."""
    streams = [_stream(step.input_text) for step in steps]
    rendered: list[str] = []
    for index, stream in enumerate(streams):
        following = streams[index + 1] if index + 1 < len(streams) else stream
        struck = _struck(stream, following) if index + 1 < len(streams) else set()
        rendered.append(_step_line(stream, struck))

    rows: list[str] = []
    if len(rendered) <= MAX_STEP_ROWS:
        for index, line in enumerate(rendered):
            label = "found" if index == 0 else ("smallest" if index == len(rendered) - 1 else "")
            rows.append(_step_row(label, line))
    else:
        for index in range(5):
            rows.append(_step_row("found" if index == 0 else "", rendered[index]))
        hidden = len(rendered) - 11
        rows.append(_step_row(plural(hidden, "more step"), '<div class="line"></div>'))
        for offset, line in enumerate(rendered[-6:]):
            last = offset == 5
            rows.append(_step_row("smallest" if last else "", line))
    return (
        '<figure class="sheet steps" tabindex="0" aria-label="Each accepted'
        ' shrinking step, from the first failing input to the smallest">\n'
        + "\n".join(rows)
        + "\n</figure>"
    )


# ---------------------------------------------------------------------------
# what was tested


def _bar_height(size: int, largest: int) -> float:
    if largest <= 1 or size <= 1:
        return BAR_FLOOR
    share = math.log(size) / math.log(largest)
    return round(BAR_FLOOR + BAR_SPAN * share, 1)


def strip(tests: list[TestRecord]) -> str:
    """One bar per test in run order, at most a hundred and sixty of them."""
    if not tests:
        tests = []
    per_bar = max(1, math.ceil(len(tests) / MAX_BARS)) if tests else 1
    groups = [tests[start : start + per_bar] for start in range(0, len(tests), per_bar)]
    largest = max((record.size for record in tests), default=1)

    bars: list[str] = []
    for index, group in enumerate(groups):
        size = max(record.size for record in group)
        height = _bar_height(size, largest)
        verdicts = [Verdict(record.verdict) for record in group]
        if any(verdict.is_failure() for verdict in verdicts):
            classes = ' class="fail"'
        elif any(verdict.is_skipped() for verdict in verdicts):
            classes = ' class="skip"'
        else:
            classes = ""
        bars.append(
            "<rect"
            + classes
            + ' x="'
            + str(index * BAR_PITCH)
            + '" y="'
            + format(BAR_BASE - height, ".1f")
            + '" width="'
            + str(BAR_WIDTH)
            + '" height="'
            + format(height, ".1f")
            + '"/>'
        )
    width = max(BAR_PITCH, len(groups) * BAR_PITCH)
    label = (
        plural(len(tests), "test")
        + " in the order they ran. Taller bars are larger inputs."
    )
    if any(Verdict(record.verdict).is_failure() for record in tests):
        label += " The red bar is the failure."
    if any(Verdict(record.verdict).is_skipped() for record in tests):
        label += " Hollow bars were set aside."
    return (
        '<svg class="strip" viewBox="0 0 '
        + str(width)
        + " "
        + str(STRIP_HEIGHT)
        + '" role="img" aria-label="'
        + esc(label)
        + '">'
        + "".join(bars)
        + '<line x1="0" y1="'
        + str(AXIS_Y)
        + '" x2="'
        + str(width)
        + '" y2="'
        + str(AXIS_Y)
        + '"/></svg>'
    )


REASONS = {
    Verdict.INVALID.value: "the validator rejected the generated input",
    Verdict.ORACLE_CONFLICT.value: "the two reference solutions disagreed",
    Verdict.ORACLE_TIMEOUT.value: "a reference solution did not answer",
}


def tested_sentence(report: Report) -> str:
    """Counts, the largest input, and what was set aside and why."""
    total = len(report.tests)
    if report.counterexample is not None:
        text = (
            plural(total, "test")
            + " ran before the first confirmed failure, on inputs of up to "
            + plural(report.trust.largest_tokens, "number")
            + "."
        )
    else:
        text = (
            plural(total, "test")
            + " ran, on inputs of up to "
            + plural(report.trust.largest_tokens, "number")
            + ", and none of them failed."
        )
    text += (
        " Each bar is one test in the order it ran, and taller bars are larger"
        " inputs."
    )
    if report.counterexample is not None:
        text += " The red bar is the failure."
    aside = [
        plural(count, "test") + " because " + REASONS.get(name, name)
        for name, count in sorted(report.trust.skipped.items())
        if count
    ]
    if aside:
        text += " Set aside: " + "; ".join(aside) + "."
    return text


# ---------------------------------------------------------------------------
# why you can trust this


def facts(report: Report) -> str:
    trust = report.trust
    samples = trust.samples
    rows: list[tuple[str, str, bool, str]] = []
    if trust.trusted_oracle:
        rows.append(
            ("Your reference solution reproduces the samples", _of(samples, samples), False, "")
        )
    else:
        rows.append(
            ("Reference solution A reproduces the samples", _of(samples, samples), False, "")
        )
        rows.append(
            ("Reference solution B reproduces the samples", _of(samples, samples), False, "")
        )
        agreed = trust.oracle_agreements
        checks = trust.oracle_checks
        rows.append(
            (
                "The two reference solutions agree",
                (str(agreed) + " of " + str(checks) + " compared")
                if checks
                else "none compared",
                checks > 0 and agreed < checks,
                "they disagreed, so those tests were set aside",
            )
        )
    rows.append(("The validator accepts the samples", _of(samples, samples), False, ""))
    rows.append(
        (
            "The validator rejects broken copies of them",
            _of(trust.samples_calibrated, samples),
            trust.weak_validator,
            "accepted every broken copy",
        )
    )
    rows.append(
        (
            "Generated inputs the validator accepts",
            _of(trust.smoke_accepted, trust.smoke_runs),
            trust.smoke_runs > 0 and trust.smoke_accepted < trust.smoke_runs,
            "some generated inputs were not valid",
        )
    )

    body: list[str] = []
    for label, value, weak, reason in rows:
        shown = value + ", " + reason if weak and reason else value
        tag = '<dd class="weak">' if weak else "<dd>"
        body.append(
            "  <div><dt>"
            + esc(label)
            + "</dt>"
            + tag
            + esc(shown)
            + "</dd></div>"
        )
    return '<dl class="facts">\n' + "\n".join(body) + "\n</dl>"


def _of(part: int, whole: int) -> str:
    return str(part) + " of " + str(whole)


def trust_note(report: Report) -> str:
    """The line under the trust list, saying who wrote what and who checked it."""
    checked = (
        " The model wrote the reference solutions and the validator, so each"
        " was checked by running it."
    )
    if report.trust.trusted_oracle:
        return (
            "You supplied the reference solution with --oracle, so Refute"
            " trusted it. The model wrote the validator and the generator, so"
            " each was checked by running it."
        )
    if report.counterexample is not None:
        return (
            "A failure counts only when both reference solutions print the"
            " same answer." + checked
        )
    return (
        "Where the two reference solutions disagreed, the test was set aside"
        " and counted rather than held against the program." + checked
    )


# ---------------------------------------------------------------------------
# the suggested fix


def diff(before: str, after: str) -> str:
    """Removed lines struck in pencil, added lines in blue.

    Leading whitespace is left outside the strike, because a line drawn
    through an indent reads as a line through nothing.
    """
    old = before.splitlines()
    new = after.splitlines()
    blocks: list[str] = []
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    for tag, start, end, other_start, other_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag in ("delete", "replace"):
            removed = []
            for line in old[start:end]:
                indent = line[: len(line) - len(line.lstrip())]
                rest = line[len(indent) :]
                piece = "-" + esc(indent)
                if rest:
                    piece += "<s>" + esc(rest) + "</s>"
                removed.append(piece)
            blocks.append('<span class="del">' + "\n".join(removed) + "</span>")
        if tag in ("insert", "replace"):
            added = ["+" + esc(line) for line in new[other_start:other_end]]
            blocks.append('<span class="add">' + "\n".join(added) + "</span>")
    return (
        '<pre class="diff" aria-label="Suggested change to the solution">'
        + "\n".join(blocks)
        + "</pre>"
    )


def fix_sentence(report: Report) -> str:
    if report.fix_verified:
        return (
            "Verified: it passes the "
            + plural(report.trust.samples, "sample")
            + ", the counterexample and 200 new random tests."
        )
    reason = report.fix_reason or "it did not pass"
    if not reason.endswith("."):
        reason += "."
    return "Unverified suggestion: " + reason


# ---------------------------------------------------------------------------
# the footer


def footer(report: Report) -> str:
    when = time.localtime(report.started_at)
    day = str(when.tm_mday)
    month = time.strftime("%B", when)
    clock = time.strftime("%H:%M", when)
    seconds = max(1, round(report.elapsed))
    trust = report.trust
    if trust.model_calls == 0:
        calls = "no calls"
    else:
        calls = plural(trust.model_calls, "call")
    if trust.model_cached == 0:
        cache = "none from cache"
    elif trust.model_cached >= trust.model_calls:
        cache = "all from cache"
    else:
        cache = str(trust.model_cached) + " from cache"
    return (
        "<footer>Refute "
        + esc(report.version)
        + " ran on "
        + day
        + " "
        + month
        + " "
        + str(when.tm_year)
        + " at "
        + clock
        + " and took "
        + plural(seconds, "second")
        + ". Model: "
        + esc(trust.model or "none")
        + ", "
        + calls
        + ", "
        + cache
        + ".</footer>"
    )


# ---------------------------------------------------------------------------
# the page


LIMITS_REFUTED = (
    "The counterexample is a fact: your program and both reference solutions"
    " were run on it. The explanation above is not.",
    "Random testing can miss rare failures. Inputs of the largest sizes were"
    " only sampled.",
    "A fix that passed 200 new random tests is evidence, not a proof that it"
    " is correct.",
)

LIMITS_TRUSTED = (
    "The counterexample is a fact: your program and your reference solution"
    " were run on it. The explanation above is not.",
    "Random testing can miss rare failures. Inputs of the largest sizes were"
    " only sampled.",
    "A fix that passed 200 new random tests is evidence, not a proof that it"
    " is correct.",
)

LIMITS_NOT_REFUTED = (
    "No failing input was found. That is not a proof that the program is"
    " correct.",
    "Random testing can miss rare failures. Inputs of the largest sizes were"
    " only sampled.",
    "The reference solutions and the validator were written by a model, and"
    " each one was checked by running it, not by reading it.",
)


def _limits_list(report: Report) -> str:
    if report.counterexample is None:
        items = LIMITS_NOT_REFUTED
    elif report.trust.trusted_oracle:
        items = LIMITS_TRUSTED
    else:
        items = LIMITS_REFUTED
    return (
        "<ul>\n"
        + "\n".join("<li>" + esc(item) + "</li>" for item in items)
        + "\n</ul>"
    )


def render(report: Report, float_tol: float | None = None) -> str:
    """The whole page, as one string."""
    found = report.counterexample
    title = "Refute: " + report.target
    heading = (
        finding(report.target, found, report.target_timeout)
        if found is not None
        else "No failing input found."
    )

    parts: list[str] = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="color-scheme" content="light dark">',
        "<title>" + esc(title) + "</title>",
        "<style>",
        CSS.strip(),
        "</style>",
        "</head>",
        "<body>",
        "<main>",
        "<h1>" + esc(heading) + "</h1>",
        '<p class="lede">' + esc(_lede(report)) + "</p>",
        "",
    ]

    if found is not None:
        parts.append(hero(found, float_tol, report.target_timeout))
        parts.append('<p class="note cmd">Run it yourself:</p>')
        parts.append("<pre class=\"cmd\"><code>" + esc(report.command) + "</code></pre>")

        if len(report.shrink_steps) >= 3:
            parts.append("")
            parts.append("<h2>How it got small</h2>")
            parts.append(
                "<p>The first failing input had "
                + plural(report.shrink_steps[0].size_tokens, "number")
                + ". Refute removed and lowered numbers until nothing more could"
                " go.</p>"
            )
            parts.append(staircase(report.shrink_steps))
            parts.append(
                '<p class="note">Struck numbers were removed or changed in the'
                " next step. A step was kept only if the validator still accepted"
                " the input, both reference solutions still agreed, and the"
                " failure was the same kind.</p>"
            )
        parts.append("")
        parts.append("<h2>What was tested</h2>")
        parts.append(strip(report.tests))
    else:
        # Not refuted: the strip is the hero, so it comes before any heading.
        parts.append(strip(report.tests))

    parts.append("<p>" + esc(tested_sentence(report)) + "</p>")

    parts.append("")
    parts.append("<h2>Why you can trust this</h2>")
    parts.append(facts(report))
    parts.append('<p class="note">' + esc(trust_note(report)) + "</p>")

    if found is not None and report.diagnosis:
        parts.append("")
        parts.append("<h2>What the model said</h2>")
        parts.append(
            '<p class="by">Written by the model. Refute has not checked it.</p>'
        )
        parts.append('<p class="model">' + esc(report.diagnosis) + "</p>")

    if found is not None and report.fix_source and report.target_source:
        parts.append("")
        parts.append("<h2>A suggested fix</h2>")
        parts.append("<p>" + esc(fix_sentence(report)) + "</p>")
        parts.append(diff(report.target_source, report.fix_source))

    parts.append("")
    parts.append("<h2>What this run does not show</h2>")
    parts.append(_limits_list(report))
    parts.append("")
    parts.append(footer(report))
    parts.append("</main>")
    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts) + "\n"


def to_json(report: Report) -> str:
    data = asdict(report)
    data["oracle_agreement"] = report.trust.oracle_agreement
    data["refuted"] = report.counterexample is not None
    return json.dumps(data, indent=2, sort_keys=True, default=str)


def write(out_dir: Path, report: Report, float_tol: float | None = None) -> dict[str, Path]:
    """Write report.html and report.json, and return every path in the directory."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = out_paths(out_dir)
    paths["report_html"].write_text(render(report, float_tol), encoding="utf-8")
    paths["report_json"].write_text(to_json(report), encoding="utf-8")
    return paths
