"""The report: the sentences, the squared sheet, the staircase, the strip.

These build a Report by hand and render it, so each rule in DESIGN.md can be
checked on its own. tests/test_run.py renders one from a real run.
"""

from __future__ import annotations

import json
import re

import pytest

from refute import report as page
from refute.models import (
    Counterexample,
    Report,
    ShrinkStep,
    TestRecord,
    Trust,
    Verdict,
)

# ---------------------------------------------------------------------------
# fixtures, shared with tests/test_design.py


GREEDY = """import sys


def main():
    data = sys.stdin.read().split()
    amount = int(data[1])
    used = 0
    for coin in sorted(coins, reverse=True):
        used += amount // coin
        amount %= coin
    print(used)
"""

FIXED = """import sys


def main():
    data = sys.stdin.read().split()
    amount = int(data[1])
    best = [0] + [10 ** 9] * amount
    for total in range(1, amount + 1):
        best[total] = min(best[total - c] + 1 for c in coins if c <= total)
    print(best[amount])
"""

STEPS = [
    ("6 14\n1 6 7 11 16 20\n", "found"),
    ("5 14\n1 6 7 11 16\n", "lowered a count and dropped the last number it counted"),
    ("4 14\n1 6 7 11\n", "lowered a count and dropped the last number it counted"),
    ("3 14\n1 7 11\n", "lowered a count and dropped the last number it counted"),
    ("3 14\n1 6 10\n", "lowered a number"),
    ("3 6\n1 3 4\n", "lowered a number"),
]


def records(count: int, largest: int = 8) -> list[TestRecord]:
    """Tests whose sizes climb to `largest`, as a real run's would."""
    out: list[TestRecord] = []
    per = max(1, count // largest)
    for index in range(count):
        size = min(largest, 1 + index // per)
        out.append(TestRecord(index + 1, size, "normal", 1000 + index, Verdict.PASS.value))
    return out


def refuted(**overrides) -> Report:
    tests = records(95)
    tests[40].verdict = Verdict.INVALID.value
    tests[-1].verdict = Verdict.WA.value
    data = dict(
        problem="coin-change",
        target="greedy.py",
        refuted=True,
        counterexample=Counterexample(
            "3 6\n1 3 4\n", "2\n", "3\n", Verdict.WA.value, 3, "normal", 7
        ),
        tests=tests,
        shrink_steps=[
            ShrinkStep(index, action, text, len(text.split()))
            for index, (text, action) in enumerate(STEPS)
        ],
        trust=Trust(
            tests_run=95,
            largest_size=8,
            largest_tokens=8,
            oracle_checks=9,
            oracle_agreements=9,
            validator_mutations=6,
            validator_rejections=6,
            samples=2,
            samples_calibrated=2,
            smoke_runs=75,
            smoke_accepted=75,
            skipped={Verdict.INVALID.value: 1},
            model="test/model",
            model_calls=7,
            model_cached=0,
            model_tokens=900,
        ),
        diagnosis="It always takes the largest coin that fits.",
        fix_source=FIXED,
        fix_verified=True,
        target_source=GREEDY,
        command="python greedy.py < refute-out/counterexample.in",
        version="0.1.0",
        target_timeout=2.0,
        started_at=1760000000.0,
        elapsed=14.2,
    )
    data.update(overrides)
    return Report(**data)


def not_refuted(**overrides) -> Report:
    tests = records(600, largest=30)
    tests[7].verdict = Verdict.ORACLE_CONFLICT.value
    data = dict(
        problem="coin-change",
        target="correct.py",
        refuted=False,
        counterexample=None,
        tests=tests,
        shrink_steps=[],
        trust=Trust(
            tests_run=600,
            largest_size=30,
            largest_tokens=32,
            oracle_checks=60,
            oracle_agreements=59,
            samples=2,
            samples_calibrated=2,
            smoke_runs=75,
            smoke_accepted=75,
            skipped={Verdict.ORACLE_CONFLICT.value: 1},
            model="test/model",
            model_calls=4,
            model_cached=4,
            model_tokens=500,
        ),
        target_source=FIXED,
        version="0.1.0",
        started_at=1760000000.0,
        elapsed=61.0,
    )
    data.update(overrides)
    return Report(**data)


# ---------------------------------------------------------------------------
# the finding


def test_the_finding_quotes_both_answers_when_they_are_short():
    found = Counterexample("3 6\n1 3 4\n", "2\n", "3\n", Verdict.WA.value, 3, "normal", 1)
    assert page.finding("greedy.py", found) == "greedy.py prints 3. The right answer is 2."


def test_a_long_or_multi_line_answer_is_described_instead():
    long_one = Counterexample("x\n", "y " * 40, "z " * 40, Verdict.WA.value, 1, "n", 1)
    assert page.finding("s.py", long_one) == "s.py gives a wrong answer on this input."
    lines = Counterexample("x\n", "1\n2\n", "1\n3\n", Verdict.WA.value, 1, "n", 1)
    assert page.finding("s.py", lines) == "s.py gives a wrong answer on this input."


def test_a_crash_and_a_time_limit_have_their_own_sentence():
    crash = Counterexample("x\n", "", "boom", Verdict.RE.value, 1, "n", 1)
    assert page.finding("s.py", crash) == "s.py crashes on this input."
    slow = Counterexample("x\n", "", "", Verdict.TLE.value, 1, "n", 1)
    assert page.finding("s.py", slow, 2.0) == "s.py takes longer than 2 s on this input."


def test_the_page_does_not_say_refuted_but_the_console_does():
    # DESIGN.md: every page opens with the finding itself.
    from refute.cli import finding as console_finding
    from refute.models import Limits

    found = Counterexample("x\n", "2\n", "3\n", Verdict.WA.value, 1, "n", 1)
    assert not page.finding("s.py", found).startswith("Refuted")
    assert console_finding("s.py", found, Limits()).startswith("Refuted. ")


# ---------------------------------------------------------------------------
# the lede


def test_the_lede_counts_the_tests_and_names_both_references():
    text = page._lede(refuted())
    assert "after 95 tests" in text
    assert "Two independent reference solutions both print 2." in text


def test_a_trusted_oracle_changes_the_lede():
    text = page._lede(refuted(trust=refuted().trust.__class__(samples=2, trusted_oracle=True)))
    assert "Your reference solution prints" in text
    assert "independent" not in text


def test_extra_differences_are_counted_in_the_lede():
    found = Counterexample("x\n", "1 1 1 1 1\n", "2 2 2 2 2\n", Verdict.WA.value, 1, "n", 1)
    text = page._lede(refuted(counterexample=found))
    assert "There are 2 more differences." in text


def test_the_not_refuted_lede_says_it_is_evidence_not_proof():
    text = page._lede(not_refuted())
    assert "600 tests" in text
    assert "inputs up to 32 numbers" in text
    assert "correct.py matched the reference solutions every time" in text
    assert text.endswith("That is evidence, not proof.")


# ---------------------------------------------------------------------------
# the squared sheet and the circle


def test_the_hero_holds_the_input_and_both_answers():
    html = page.hero(refuted().counterexample, None, 2.0)
    assert 'class="sheet hero"' in html
    assert '<div class="line">3 6</div>' in html
    assert '<div class="line">1 3 4</div>' in html
    assert ">input</span>" in html
    assert ">expected</span>" in html
    assert '<div class="row got">' in html
    assert '<div class="gap"></div>' in html


def test_the_circle_sits_on_the_differing_token():
    html = page.hero(refuted().counterexample, None, 2.0)
    assert 'style="--x:0;--y:0;--w:1"' in html
    assert page.CIRCLE_PATH in html
    assert html.count('class="circle"') == 1


def test_a_circle_is_placed_by_row_column_and_width():
    found = Counterexample("x\n", "10 20\n99 7\n", "10 20\n99 4321\n", Verdict.WA.value, 1, "n", 1)
    html = page.hero(found, None, 2.0)
    # The fifth token differs: second row, column 3, four characters wide.
    assert 'style="--x:3;--y:1;--w:4"' in html


def test_at_most_three_tokens_are_circled():
    found = Counterexample("x\n", "1 1 1 1 1\n", "2 2 2 2 2\n", Verdict.WA.value, 1, "n", 1)
    html = page.hero(found, None, 2.0)
    assert html.count('class="circle"') == page.MAX_CIRCLES


def test_long_output_is_cut_to_eight_lines_with_a_last_line():
    found = Counterexample(
        "\n".join(str(n) for n in range(40)) + "\n", "1\n", "2\n", Verdict.WA.value, 1, "n", 1
    )
    html = page.hero(found, None, 2.0)
    lines = re.findall(r'<div class="line">(.*?)</div>', html)
    assert len(lines) == page.MAX_LINES + 2  # eight for the input, one each answer
    assert lines[page.MAX_LINES - 1] == "..."


def test_a_crash_shows_the_error_and_a_timeout_shows_the_time():
    crash = Counterexample("1\n", "", "Traceback\nValueError: no\n", Verdict.RE.value, 1, "n", 1)
    html = page.hero(crash, None, 2.0)
    assert ">error</span>" in html
    assert "ValueError: no" in html
    assert "circle" not in html

    slow = Counterexample("1\n", "", "", Verdict.TLE.value, 1, "n", 1)
    html = page.hero(slow, None, 2.0)
    assert ">time</span>" in html
    assert "longer than 2 s" in html


# ---------------------------------------------------------------------------
# how it got small


def test_the_staircase_strikes_what_the_next_step_took_away():
    html = page.staircase(refuted().shrink_steps)
    rows = re.findall(r'<div class="line">(.*?)</div>', html)
    assert len(rows) == len(STEPS)
    # The first row loses its count and its last coin to the second row.
    assert rows[0].startswith("<s>6</s>")
    assert rows[0].endswith("<s>20</s>")
    # The last row is the smallest and has nothing struck.
    assert "<s>" not in rows[-1]
    assert rows[-1] == '3 6 <span class="nl">/</span> 1 3 4'


def test_a_newline_is_drawn_as_a_slash_in_pencil():
    html = page.staircase(refuted().shrink_steps)
    assert html.count('<span class="nl">/</span>') == len(STEPS)


def test_only_the_first_and_last_rows_are_labelled():
    html = page.staircase(refuted().shrink_steps)
    labels = re.findall(r'<span class="label">(.*?)</span>', html)
    assert labels[0] == "found"
    assert labels[-1] == "smallest"
    assert all(label == "" for label in labels[1:-1])


def test_more_than_twelve_steps_keeps_the_first_five_and_last_six():
    steps = [
        ShrinkStep(index, "lowered a number", str(40 - index) + "\n", 1)
        for index in range(20)
    ]
    html = page.staircase(steps)
    labels = re.findall(r'<span class="label">(.*?)</span>', html)
    assert len(labels) == page.MAX_STEP_ROWS
    assert labels[0] == "found"
    assert labels[5] == "9 more steps"  # 20 rows shown as 5 + 1 + 6
    assert labels[-1] == "smallest"
    rows = re.findall(r'<div class="line">(.*?)</div>', html)
    assert rows[5] == ""  # the row that stands for the hidden ones


# ---------------------------------------------------------------------------
# what was tested


def test_one_bar_per_test_up_to_a_hundred_and_sixty():
    html = page.strip(records(95))
    assert html.count("<rect") == 95
    assert 'viewBox="0 0 475 64"' in html

    html = page.strip(records(600, largest=30))
    assert html.count("<rect") == 150  # 600 tests at four per bar
    assert 'viewBox="0 0 750 64"' in html


def test_bar_height_grows_with_the_log_of_the_input_size():
    # The reference file's bars, for a largest size of 8, are 8.0, 17.4, 22.9,
    # 26.7, 29.8, 32.2, 34.3 and 36.1. This formula gives all of those except
    # size 3, where it gives 22.8 against the reference's 22.9; the exact
    # value is 22.8458, so the reference has rounded that one up.
    assert page._bar_height(1, 8) == 8.0
    assert page._bar_height(2, 8) == 17.4
    assert page._bar_height(3, 8) == 22.8
    assert page._bar_height(4, 8) == 26.7
    assert page._bar_height(6, 8) == 32.2
    assert page._bar_height(7, 8) == 34.3
    assert page._bar_height(8, 8) == 36.1
    heights = [page._bar_height(size, 8) for size in range(1, 9)]
    assert heights == sorted(heights)


def test_a_single_size_run_uses_the_floor_height():
    assert page._bar_height(1, 1) == page.BAR_FLOOR
    assert page._bar_height(5, 1) == page.BAR_FLOOR


def test_the_failing_bar_is_red_and_a_set_aside_bar_is_hollow():
    tests = records(95)
    tests[40].verdict = Verdict.INVALID.value
    tests[-1].verdict = Verdict.WA.value
    html = page.strip(tests)
    assert html.count('class="fail"') == 1
    assert html.count('class="skip"') == 1
    assert "The red bar is the failure." in html
    assert "Hollow bars were set aside." in html


def test_a_bar_that_holds_both_a_failure_and_a_skip_is_red():
    tests = records(4)
    tests[0].verdict = Verdict.INVALID.value
    tests[1].verdict = Verdict.WA.value
    html = page.strip(tests[:2])
    assert 'class="fail"' in html or 'class="skip"' in html
    grouped = page.strip(tests[:2])
    assert grouped.count("<rect") == 2


def test_the_strip_ends_with_its_axis():
    html = page.strip(records(10))
    assert '<line x1="0" y1="60" x2="50" y2="60"/>' in html


def test_the_sentence_under_the_strip_says_what_was_set_aside():
    text = page.tested_sentence(refuted())
    assert "95 tests ran before the first confirmed failure" in text
    assert "inputs of up to 8 numbers" in text
    assert "The red bar is the failure." in text
    assert "1 test because the validator rejected the generated input" in text


def test_the_sentence_for_a_run_with_no_failure():
    text = page.tested_sentence(not_refuted())
    assert "600 tests ran" in text
    assert "none of them failed" in text
    assert "the two reference solutions disagreed" in text
    assert "red bar" not in text


# ---------------------------------------------------------------------------
# why you can trust this


def test_the_trust_list_has_a_line_for_each_checked_thing():
    html = page.facts(refuted())
    for label in (
        "Reference solution A reproduces the samples",
        "Reference solution B reproduces the samples",
        "The two reference solutions agree",
        "The validator accepts the samples",
        "The validator rejects broken copies of them",
        "Generated inputs the validator accepts",
    ):
        assert "<dt>" + label + "</dt>" in html
    assert "<dd>9 of 9 compared</dd>" in html
    assert "<dd>75 of 75</dd>" in html
    assert 'class="weak"' not in html


def test_a_weak_validator_is_marked_and_given_a_reason():
    trust = refuted().trust
    trust.weak_validator = True
    trust.samples_calibrated = 0
    html = page.facts(refuted(trust=trust))
    assert '<dd class="weak">0 of 2, accepted every broken copy</dd>' in html


def test_references_that_disagreed_are_marked():
    trust = refuted().trust
    trust.oracle_agreements = 7
    html = page.facts(refuted(trust=trust))
    assert '<dd class="weak">7 of 9 compared, they disagreed' in html


def test_a_trusted_oracle_drops_the_second_reference_rows():
    trust = refuted().trust
    trust.trusted_oracle = True
    html = page.facts(refuted(trust=trust))
    assert "Your reference solution reproduces the samples" in html
    assert "Reference solution B" not in html
    assert "The two reference solutions agree" not in html


# ---------------------------------------------------------------------------
# the fix


def test_removed_lines_are_struck_and_added_lines_are_not():
    html = page.diff(GREEDY, FIXED)
    assert '<span class="del">' in html
    assert '<span class="add">' in html
    assert "<s>used = 0</s>" in html
    assert "+    best = [0] + [10 ** 9] * amount" in html
    assert "<s>" not in html.split('<span class="add">')[1]


def test_the_strike_starts_at_one_column_for_the_whole_block():
    # The indentation a block shares sits outside the strike, so the struck
    # lines keep one straight left edge. tests/test_design.py checks the same
    # rule against the reference's own figure.
    html = page.diff("    a = 1\n", "    a = 2\n")
    assert "-    <s>a = 1</s>" in html
    deeper = page.diff("  a = 1\n      b = 2\n", "  a = 9\n")
    assert "-  <s>a = 1</s>" in deeper
    assert "-  <s>    b = 2</s>" in deeper


def test_common_indent_is_what_every_line_of_a_block_begins_with():
    assert page.common_indent(["    x", "        y"]) == "    "
    assert page.common_indent(["x", "    y"]) == ""
    assert page.common_indent(["", "   "]) == ""
    assert page.common_indent([]) == ""


def test_the_diff_escapes_the_code_it_shows():
    html = page.diff("x = a < b\n", "x = a > b\n")
    assert "&lt;" in html and "&gt;" in html
    assert "a < b" not in html


def test_a_verified_fix_says_what_it_passed():
    assert page.fix_sentence(refuted()) == (
        "Verified: it passes the 2 samples, the counterexample and 200 new random tests."
    )


def test_an_unverified_fix_says_why():
    text = page.fix_sentence(refuted(fix_verified=False, fix_reason="it crashed on sample 1"))
    assert text == "Unverified suggestion: it crashed on sample 1."


# ---------------------------------------------------------------------------
# the footer


def test_the_footer_gives_the_version_the_time_and_the_model():
    text = page.footer(refuted())
    assert text.startswith("<footer>Refute 0.1.0 ran on ")
    # The time is local to whoever ran it, so match the shape, not the value.
    assert re.search(r"ran on \d{1,2} [A-Z][a-z]+ \d{4} at \d\d:\d\d and took", text)
    assert "and took 14 seconds" in text
    assert "Model: test/model, 7 calls, none from cache." in text


def test_the_footer_counts_calls_served_from_the_cache():
    assert "4 calls, all from cache." in page.footer(not_refuted())
    trust = refuted().trust
    trust.model_cached = 3
    assert "7 calls, 3 from cache." in page.footer(refuted(trust=trust))


def test_a_run_shorter_than_a_second_still_says_one_second():
    assert "took 1 second." in page.footer(refuted(elapsed=0.2))


# ---------------------------------------------------------------------------
# the whole page


def test_the_refuted_page_has_every_section_in_order():
    html = page.render(refuted())
    order = [
        "<h1>",
        'class="lede"',
        'class="sheet hero"',
        "Run it yourself:",
        "<h2>How it got small</h2>",
        'class="sheet steps"',
        "<h2>What was tested</h2>",
        'class="strip"',
        "<h2>Why you can trust this</h2>",
        'class="facts"',
        "<h2>What the model said</h2>",
        'class="model"',
        "<h2>A suggested fix</h2>",
        'class="diff"',
        "<h2>What this run does not show</h2>",
        "<footer>",
    ]
    positions = [html.index(piece) for piece in order]
    assert positions == sorted(positions)
    assert html.count("<h1>") == 1
    assert html.startswith("<!doctype html>")


def test_the_model_text_is_labelled_as_unchecked():
    html = page.render(refuted())
    assert '<p class="by">Written by the model. Refute has not checked it.</p>' in html


def test_the_staircase_is_left_out_when_nothing_was_accepted():
    only_found = [ShrinkStep(0, "found", "3 6\n1 3 4\n", 5)]
    html = page.render(refuted(shrink_steps=only_found))
    assert "How it got small" not in html
    assert 'class="sheet steps"' not in html
    assert 'class="sheet hero"' in html


def test_the_model_sections_are_left_out_with_no_ai_explain():
    html = page.render(refuted(diagnosis="", fix_source=""))
    assert "What the model said" not in html
    assert "A suggested fix" not in html
    assert 'class="diff"' not in html
    # Everything that execution proved is still there.
    assert "What was tested" in html
    assert "Why you can trust this" in html


def test_the_not_refuted_page_leads_with_the_strip_and_keeps_three_sections():
    html = page.render(not_refuted())
    assert "<h1>No failing input found.</h1>" in html
    assert 'class="sheet hero"' not in html
    assert 'class="sheet steps"' not in html
    assert 'class="circle"' not in html
    assert 'class="model"' not in html
    assert 'class="diff"' not in html
    assert "Run it yourself" not in html
    # The strip is the hero: it comes before the first heading.
    assert html.index('class="strip"') < html.index("<h2>")
    assert "Why you can trust this" in html
    assert "What this run does not show" in html
    assert "That is not a proof that the program is correct." in html


def test_nothing_on_the_page_claims_a_proof():
    for html in (page.render(refuted()), page.render(not_refuted())):
        lowered = html.lower()
        assert "proven" not in lowered
        assert "guarantee" not in lowered


def test_the_page_is_one_file_with_no_script_and_no_network():
    html = page.render(refuted())
    assert "<script" not in html
    assert "http://" not in html and "https://" not in html
    assert "url(" not in html


# ---------------------------------------------------------------------------
# report.json


def test_the_json_carries_the_whole_run():
    data = json.loads(page.to_json(refuted()))
    assert data["refuted"] is True
    assert data["problem"] == "coin-change"
    assert data["counterexample"]["input_text"] == "3 6\n1 3 4\n"
    assert len(data["tests"]) == 95
    assert len(data["shrink_steps"]) == len(STEPS)
    assert data["trust"]["smoke_runs"] == 75
    assert data["oracle_agreement"] == 1.0
    assert data["fix_verified"] is True


def test_the_json_of_a_run_with_no_failure_says_so():
    data = json.loads(page.to_json(not_refuted()))
    assert data["refuted"] is False
    assert data["counterexample"] is None
    assert data["shrink_steps"] == []
    assert data["oracle_agreement"] == pytest.approx(59 / 60)


def test_write_puts_both_files_in_the_output_directory(tmp_path):
    paths = page.write(tmp_path / "out", refuted())
    assert paths["report_html"].is_file()
    assert paths["report_json"].is_file()
    assert "<h1>" in paths["report_html"].read_text(encoding="utf-8")
    assert json.loads(paths["report_json"].read_text(encoding="utf-8"))["refuted"]


def test_the_trust_note_says_who_wrote_what_and_who_checked_it():
    refuted_note = page.trust_note(refuted())
    assert refuted_note.startswith("A failure counts only when both reference")
    assert "checked by running it" in refuted_note

    # There was no failure, so the note about confirming one would be beside
    # the point.
    quiet = page.trust_note(not_refuted())
    assert "A failure counts only" not in quiet
    assert "set aside and counted" in quiet
    assert "checked by running it" in quiet

    trust = refuted().trust
    trust.trusted_oracle = True
    mine = page.trust_note(refuted(trust=trust))
    assert "--oracle" in mine
    assert "reference solutions" not in mine
