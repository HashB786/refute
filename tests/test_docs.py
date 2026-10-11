"""The published pages must agree with the runs they claim to report.

Phase 5 is where invented numbers would do the most damage, because these are
the files a reader sees first. Every figure in README.md and docs/index.html
is checked here against bench/results.json, which a real `refute bench` run
wrote, and against the recorded report the landing page was built from.
"""

from __future__ import annotations

import json
import re
import statistics
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
RECORD = json.loads((ROOT / "bench" / "results.json").read_text(encoding="utf-8"))
README = (ROOT / "README.md").read_text(encoding="utf-8")
INDEX = (DOCS / "index.html").read_text(encoding="utf-8")
DEVPOST = (DOCS / "devpost.md").read_text(encoding="utf-8")
SCRIPT = (DOCS / "demo-script.md").read_text(encoding="utf-8")

PAIRS = RECORD["pairs"]
FOUND = [pair for pair in PAIRS if pair["found"]]
PROBLEMS = {row["problem"]: row for row in RECORD["problems"]}


def medians(slug: str | None = None) -> tuple[int, int]:
    rows = [pair for pair in FOUND if slug is None or pair["problem"] == slug]
    return (
        int(statistics.median([pair["tests"] for pair in rows])),
        int(statistics.median([pair["input_tokens"] for pair in rows])),
    )


# ---------------------------------------------------------------------------
# every file Phase 5 adds is there


@pytest.mark.parametrize(
    "name",
    ["index.html", "sample-report.html", "devpost.md", "demo-script.md", "build.py"],
)
def test_the_docs_are_in_the_repository(name):
    assert (DOCS / name).is_file()


def test_the_dockerfile_runs_untrusted_code_as_nobody_special():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "--network none" in text
    assert 'ENTRYPOINT ["refute"]' in text
    assert "USER refute" in text
    assert text.index("USER refute") < text.index("ENTRYPOINT")


# ---------------------------------------------------------------------------
# the headline figure


def test_the_headline_is_the_one_the_benchmark_recorded():
    headline = str(len(FOUND)) + " of " + str(len(PAIRS))
    assert headline in README
    assert headline in INDEX
    assert headline in DEVPOST


def test_the_readme_table_matches_the_record_row_by_row():
    for slug, problem in sorted(PROBLEMS.items()):
        mine = [pair for pair in PAIRS if pair["problem"] == slug]
        found = [pair for pair in mine if pair["found"]]
        tests, tokens = medians(slug)
        row = (
            "| "
            + slug
            + " | "
            + str(len(found))
            + " of "
            + str(len(mine))
            + " | "
            + str(tests)
            + " | "
            + str(tokens)
            + " | "
            + str(problem["oracle_agreements"])
            + " of "
            + str(problem["oracle_checks"])
            + " |"
        )
        assert row in README, row


def test_the_readme_total_row_matches_the_record():
    tests, tokens = medians()
    compared = sum(row["oracle_checks"] for row in PROBLEMS.values())
    agreed = sum(row["oracle_agreements"] for row in PROBLEMS.values())
    row = (
        "| all six | "
        + str(len(FOUND))
        + " of "
        + str(len(PAIRS))
        + " | "
        + str(tests)
        + " | "
        + str(tokens)
        + " | "
        + str(agreed)
        + " of "
        + str(compared)
        + " |"
    )
    assert row in README, row


def test_the_landing_table_matches_the_record_row_by_row():
    table = re.search(r"<table>.*?</table>", INDEX, re.DOTALL).group(0)
    by_slug = {}
    for row in re.findall(r"<tr>(.*?)</tr>", table, re.DOTALL):
        cells = [
            " ".join(re.sub(r"<[^>]+>", "", cell).split())
            for cell in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.DOTALL)
        ]
        by_slug[cells[0]] = cells

    for slug, problem in sorted(PROBLEMS.items()):
        mine = [pair for pair in PAIRS if pair["problem"] == slug]
        found = [pair for pair in mine if pair["found"]]
        tests, tokens = medians(slug)
        cells = by_slug[slug]
        assert cells[1] == str(len(found)) + " of " + str(len(mine))
        assert cells[2] == str(tests)
        assert cells[3] == str(tokens)
        assert cells[4] == (
            str(problem["oracle_agreements"]) + " of " + str(problem["oracle_checks"])
        )


def test_the_published_budget_is_the_one_that_was_used():
    budget = RECORD["budget"]
    phrase = (
        str(int(budget["max_tests"]))
        + " tests or "
        + str(int(budget["max_seconds"]))
        + " seconds"
    )
    assert phrase in README
    assert phrase in INDEX
    assert str(int(budget["shrink_seconds"])) + " seconds to shrink" in README


def test_the_model_call_total_is_the_one_that_was_spent():
    calls = sum(row["model_calls"] for row in PROBLEMS.values())
    assert str(calls) + " model calls" in README
    assert str(calls) + " model calls" in INDEX
    assert str(calls) in DEVPOST


def test_the_claims_about_the_tooling_hold_in_the_record():
    smoke = [row["smoke"] for row in PROBLEMS.values()]
    accepted = sum(int(text.split(" of ")[0]) for text in smoke)
    runs = sum(int(text.split(" of ")[1]) for text in smoke)
    assert accepted == runs
    assert str(runs) + " generated inputs" in DEVPOST

    compared = sum(row["oracle_checks"] for row in PROBLEMS.values())
    agreed = sum(row["oracle_agreements"] for row in PROBLEMS.values())
    assert agreed == compared
    assert "all " + str(compared) + " inputs where both ran" in DEVPOST


def test_the_miss_is_named_wherever_the_results_are_summarised():
    missed = [pair for pair in PAIRS if not pair["found"]]
    assert len(missed) == 1
    name = missed[0]["problem"] + " / " + missed[0]["bug"].replace("_", " ")
    assert name in INDEX
    assert missed[0]["problem"] in README
    assert missed[0]["kind"] in README


# ---------------------------------------------------------------------------
# the landing page is built from the recorded report, not written by hand


def test_the_landing_page_shows_the_recorded_report_finding():
    sample = (DOCS / "sample-report.html").read_text(encoding="utf-8")
    finding = re.search(r"<h1>(.*?)</h1>", sample).group(1)
    hero = re.search(
        r'<figure class="sheet hero".*?</figure>', sample, re.DOTALL
    ).group(0)
    assert finding in INDEX
    assert hero in INDEX
    assert "greedy.py" in finding


def test_the_landing_page_opens_with_the_heading_design_asks_for():
    assert "<h1>The smallest input that proves your solution wrong.</h1>" in INDEX
    order = [
        "<h1>",
        'class="sheet hero"',
        "<h2>Install and run</h2>",
        "<h2>How the model is checked</h2>",
        "<h2>Results</h2>",
        "<table>",
        "<h2>Limits</h2>",
        "<footer>",
    ]
    positions = [INDEX.index(piece) for piece in order]
    assert positions == sorted(positions)


def test_the_landing_page_inlines_the_report_stylesheet():
    from refute.style import CSS, LANDING

    assert CSS.strip() in INDEX
    assert LANDING.strip() in INDEX
    assert "<link" not in INDEX


def test_the_landing_page_links_only_to_files_that_exist():
    links = re.findall(r'<a href="([^"]+)"', INDEX)
    assert links
    for href in links:
        assert not href.startswith("http"), href
        assert (DOCS / href).resolve().is_file(), href


# ---------------------------------------------------------------------------
# the write-up and the script


def test_the_devpost_page_is_within_its_word_limit():
    words = re.sub(r"^#.*$", "", DEVPOST, flags=re.MULTILINE).split()
    assert len(words) <= 350, len(words)


def test_the_devpost_page_uses_the_standard_headings():
    assert re.findall(r"^## (.+)$", DEVPOST, re.MULTILINE) == [
        "What it does",
        "How we built it",
        "Accomplishments",
        "What we learned",
        "What's next",
    ]


def test_the_devpost_page_admits_whose_bugs_they_are():
    assert "marks its own homework" in DEVPOST
    assert "not collected from real submissions" in DEVPOST


def test_the_demo_script_runs_to_two_minutes_forty_five():
    slots = re.findall(r"^## (\d):(\d\d) - (\d):(\d\d)", SCRIPT, re.MULTILINE)
    assert len(slots) == 5
    spans = [
        (int(a) * 60 + int(b), int(c) * 60 + int(d)) for a, b, c, d in slots
    ]
    assert spans[0][0] == 0
    assert spans[-1][1] == 165
    for (_, end), (start, _) in zip(spans, spans[1:]):
        assert end == start
    assert [end - start for start, end in spans] == [15, 20, 70, 40, 20]


def test_the_demo_script_narration_can_be_said_in_the_time():
    """A script nobody can read aloud at the stated pace is not a script."""
    lengths = [15, 20, 70, 40, 20]
    blocks = re.split(r"^## ", SCRIPT, flags=re.MULTILINE)[1:]
    assert len(blocks) == len(lengths)
    for block, seconds in zip(blocks, lengths):
        spoken = " ".join(re.findall(r'Say[^:]*:\s*"(.*?)"', block, re.DOTALL))
        rate = len(spoken.split()) / seconds * 60
        assert 90 <= rate <= 180, block.splitlines()[0] + " is " + str(round(rate))


def test_the_demo_script_gives_commands_that_work():
    commands = re.findall(r"^```\n(.+?)\n```", SCRIPT, re.DOTALL | re.MULTILINE)
    assert any(line.strip() == "refute demo" for line in commands)
    for command in commands:
        if command.startswith("sed -n"):
            named = re.search(r"sed -n '.+?' (\S+)", command)
            assert named, command
            assert (ROOT / named.group(1)).is_file(), command
        if command.startswith("cat "):
            assert (ROOT / command.split()[1]).is_file(), command


def test_the_demo_script_says_where_the_recording_is_cut():
    # A video hiding a cut would be the same dishonesty as a faked number.
    assert "[cut here]" in SCRIPT
    assert "Cut once" in SCRIPT
    assert "nothing is sped up" in SCRIPT


def test_the_readme_shows_the_real_demo_output():
    assert "demo       replaying recorded model output" in README
    assert "Refuted. greedy.py prints 5. The right answer is 2." in README
    assert "0 model calls" in README
