"""Builds docs/index.html out of a real report and the real benchmark record.

Nothing on the landing page is written by hand except its own sentences. The
finding and the squared sheet are lifted out of a report that a run produced,
and every number in the results table is read out of bench/results.json. That
is the point of generating the page rather than writing it: a figure on it
cannot drift away from the run it came from, and tests/test_design.py checks
the two against each other.

Produce the report first, then the page:

    refute demo
    python docs/build.py

By default it reads refute-out/report.html, copies it to
docs/sample-report.html so the page has something real to link to, and
writes docs/index.html.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from refute.report import esc, plural
from refute.style import CSS, LANDING

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

HEADING = "The smallest input that proves your solution wrong."

LEAD = (
    "Refute looks for the smallest input your program gets wrong. A language"
    " model writes the throwaway test tooling for a problem: two independent"
    " reference solutions, an input validator and an input generator.",
    "Running the code decides, not the model. Every piece of tooling has to"
    " prove itself by running before anything it says is believed, and a"
    " failure is only reported when both reference solutions agree on the"
    " answer.",
)

CHECKED = (
    "Two reference solutions, by different methods. Both have to reproduce"
    " every example in the statement, and a failure counts only when they"
    " agree with each other.",
    "A validator that has to accept every example and reject deliberately"
    " broken copies of them: one token short, one token too many, one number"
    " out of range.",
    "A generator that has to produce valid input at fifteen combinations of"
    " size and mode, give the same input for the same seed, and give"
    " different inputs for different seeds.",
    "A guard that parses anything the model writes and refuses it if it"
    " imports outside a short list or reaches for open, exec, eval or dunder"
    " attributes.",
    "A suggested fix is called verified only once it passes the examples, the"
    " counterexample and two hundred tests on seeds the first run never used.",
    "The diagnosis is the one thing nothing can check, so it is labelled as"
    " the model's words and left unchecked.",
)

LIMITS = (
    "No counterexample found is not the same as correct. A run that finds"
    " nothing reports how many tests it ran, how large the inputs got and how"
    " often the two reference solutions agreed, and says so.",
    "A counterexample is a fact, because your program and both references"
    " were run on it. The explanation beside it is not.",
    "The benchmark below marks its own homework: the bugs were written by the"
    " same agent that built Refute, not collected from real submissions.",
    "Refute runs code it did not write. The guard and the sandbox catch"
    " mistakes, not attacks, so use the Dockerfile with --network none for"
    " anything you do not trust.",
    "For practice, not for live contests.",
)

INSTALL = """git clone <this repository>
cd refute
python -m venv .venv
.venv/bin/pip install -e .
.venv/bin/refute demo"""


def take(report: str, pattern: str) -> str:
    found = re.search(pattern, report, re.DOTALL)
    if found is None:
        raise SystemExit("the report has no " + pattern)
    return found.group(1)


def results_table(record: dict) -> list[str]:
    """The benchmark table, every figure read out of the record."""
    pairs = record["pairs"]
    problems = sorted(record["problems"], key=lambda row: row["problem"])
    rows = [
        "<table>",
        "<caption>Planted bugs found, per problem. Agreement is how often the"
        " two reference solutions printed the same answer when both were"
        " run.</caption>",
        "<tr><th>problem</th><th class=\"n\">bugs found</th>"
        "<th class=\"n\">tests to first failure</th>"
        "<th class=\"n\">smallest input</th><th>oracles agree</th></tr>",
    ]
    for problem in problems:
        slug = problem["problem"]
        mine = [pair for pair in pairs if pair["problem"] == slug]
        found = [pair for pair in mine if pair["found"]]
        median_tests = (
            str(int(statistics.median([pair["tests"] for pair in found])))
            if found
            else "-"
        )
        median_input = (
            str(int(statistics.median([pair["input_tokens"] for pair in found])))
            if found
            else "-"
        )
        rows.append(
            "<tr><td>"
            + esc(slug)
            + '</td><td class="n">'
            + str(len(found))
            + " of "
            + str(len(mine))
            + '</td><td class="n">'
            + median_tests
            + '</td><td class="n">'
            + median_input
            + "</td><td>"
            + str(problem["oracle_agreements"])
            + " of "
            + str(problem["oracle_checks"])
            + "</td></tr>"
        )
    found = [pair for pair in pairs if pair["found"]]
    rows.append(
        "<tr><th>all six</th><th class=\"n\">"
        + str(len(found))
        + " of "
        + str(len(pairs))
        + '</th><th class="n">'
        + str(int(statistics.median([pair["tests"] for pair in found])))
        + '</th><th class="n">'
        + str(int(statistics.median([pair["input_tokens"] for pair in found])))
        + "</th><th></th></tr>"
    )
    rows.append("</table>")
    return rows


def results_prose(record: dict) -> list[str]:
    pairs = record["pairs"]
    found = [pair for pair in pairs if pair["found"]]
    missed = [pair for pair in pairs if not pair["found"]]
    budget = record["budget"]
    lines = [
        "<p>Refute was handed each statement and each planted bug and nothing"
        " else. It never saw the reference solution that the benchmark keeps"
        " to check its own bugs with. Every bug reproduces the examples in its"
        " statement, so none of them can be caught by reading the statement."
        " Tests to first failure and smallest input are medians over the bugs"
        " that were found; the smallest input is counted in"
        " whitespace-separated tokens.</p>",
        "<p>The budget was "
        + str(int(budget["max_tests"]))
        + " tests or "
        + str(int(budget["max_seconds"]))
        + " seconds a bug, with "
        + str(int(budget["shrink_seconds"]))
        + " seconds to shrink, and the tooling for a problem was written once"
        " and reused for every bug of that problem: "
        + str(sum(row["model_calls"] for row in record["problems"]))
        + " model calls for all "
        + str(len(pairs))
        + " bugs.</p>",
    ]
    if missed:
        names = ", ".join(
            pair["problem"] + " / " + pair["bug"].replace("_", " ")
            for pair in missed
        )
        lines.append(
            "<p>"
            + plural(len(missed), "bug")
            + " was missed: "
            + esc(names)
            + ". bench/RESULTS.md lists every bug, found or missed, with the"
            " reason it was missed.</p>"
            if len(missed) == 1
            else "<p>"
            + plural(len(missed), "bug")
            + " were missed: "
            + esc(names)
            + ". bench/RESULTS.md lists every bug, found or missed, with the"
            " reason each was missed.</p>"
        )
    return lines


def render(report: str, record: dict, sample: str) -> str:
    finding = take(report, r"<h1>(.*?)</h1>")
    hero = take(report, r'(<figure class="sheet hero".*?</figure>)')
    command = take(report, r'<pre class="cmd"><code>(.*?)</code></pre>')

    parts = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="color-scheme" content="light dark">',
        "<title>Refute</title>",
        "<style>",
        CSS.strip(),
        LANDING.strip(),
        "</style>",
        "</head>",
        "<body>",
        "<main>",
        "<h1>" + esc(HEADING) + "</h1>",
    ]
    for sentence in LEAD:
        parts.append("<p>" + esc(sentence) + "</p>")
    parts.append("")
    parts.append("<p>" + esc(finding) + "</p>")
    parts.append(hero)
    parts.append(
        '<p class="note">From the recorded demo in this repository, which'
        " replays the model output and runs every program for real.</p>"
    )
    parts.append("")
    parts.append("<h2>Install and run</h2>")
    parts.append("<pre><code>" + esc(INSTALL) + "</code></pre>")
    parts.append(
        "<p>That needs no key and no network. To run it on your own solution,"
        " put a model endpoint in a .env file and use:</p>"
    )
    parts.append("<pre><code>" + esc("refute run statement.md solution.py") + "</code></pre>")
    parts.append(
        '<p class="note">The demo above repeats its own finding with '
        + "<code>"
        + esc(command)
        + "</code>.</p>"
    )
    parts.append("")
    parts.append("<h2>How the model is checked</h2>")
    parts.append("<ul>")
    for item in CHECKED:
        parts.append("<li>" + esc(item) + "</li>")
    parts.append("</ul>")
    parts.append("")
    parts.append("<h2>Results</h2>")
    parts.extend(results_table(record))
    parts.extend(results_prose(record))
    parts.append("")
    parts.append("<h2>Limits</h2>")
    parts.append("<ul>")
    for item in LIMITS:
        parts.append("<li>" + esc(item) + "</li>")
    parts.append("</ul>")
    parts.append("")
    parts.append("<h2>More</h2>")
    parts.append("<ul>")
    parts.append(
        '<li>The sample report above in full: <a href="'
        + esc(sample)
        + '">'
        + esc(sample)
        + "</a></li>"
    )
    parts.append(
        '<li>Every bug the benchmark planted, found or missed:'
        ' <a href="../bench/RESULTS.md">bench/RESULTS.md</a></li>'
    )
    parts.append(
        '<li>The write-up: <a href="devpost.md">docs/devpost.md</a></li>'
    )
    parts.append(
        "<li>The source, the six benchmark problems and the recorded demo are"
        " in this repository.</li>"
    )
    parts.append("</ul>")
    parts.append("")
    parts.append(
        "<footer>Refute is for practice and for learning, not for live"
        " contests. MIT licence.</footer>"
    )
    parts.append("</main>")
    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "refute-out" / "report.html",
        help="a report a run produced; refute demo writes one",
    )
    parser.add_argument("--record", type=Path, default=ROOT / "bench" / "results.json")
    args = parser.parse_args(argv)

    if not args.report.is_file():
        print("no report at " + str(args.report) + ". Run refute demo first.")
        return 1
    if not args.record.is_file():
        print("no benchmark record at " + str(args.record) + ". Run refute bench first.")
        return 1

    report = args.report.read_text(encoding="utf-8")
    record = json.loads(args.record.read_text(encoding="utf-8"))
    if not record["pairs"]:
        print("the benchmark record is empty")
        return 1

    sample = "sample-report.html"
    shutil.copyfile(args.report, DOCS / sample)
    page = render(report, record, sample)
    (DOCS / "index.html").write_text(page, encoding="utf-8")
    print("wrote docs/index.html and docs/" + sample)
    print(
        "  finding and sheet from "
        + str(args.report)
        + ", numbers from "
        + str(args.record)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
