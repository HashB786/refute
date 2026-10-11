"""The design rules from DESIGN.md, checked against rendered pages.

DESIGN.md governs the report, the console and the wording, and ends with a
list of lint rules. Each one is a test here. The pages under test are a
refuted fixture, a not-refuted fixture and docs/index.html once it exists.
"""

from __future__ import annotations

import ast
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from refute import report as page
from refute import style
from refute.models import TestRecord, Verdict

from .test_report import not_refuted, records, refuted

ROOT = Path(__file__).resolve().parent.parent
BANNED_PROPERTIES = (
    "box-shadow",
    "text-shadow",
    "filter",
    "backdrop-filter",
    "animation",
    "@keyframes",
    "transition",
    "border-radius",
    "text-transform",
)
BANNED_WORDS = (
    "powerful",
    "seamless",
    "supercharge",
    "leverage",
    "unlock",
    "revolutionary",
    "cutting-edge",
    "state-of-the-art",
    "ai-powered",
    "next-generation",
    "effortless",
    "game-changing",
    "delve",
    "blazing",
    "magic",
)
EMOJI = re.compile(
    "[☀-➿\U0001f000-\U0001faff️]"
)

PAGES = {
    "refuted": page.render(refuted()),
    "not refuted": page.render(not_refuted()),
}
if (ROOT / "docs" / "index.html").is_file():
    PAGES["landing"] = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")

NAMES = sorted(PAGES)


def html(name: str) -> str:
    return PAGES[name]


def css_of(name: str) -> str:
    found = re.search(r"<style>(.*?)</style>", html(name), re.DOTALL)
    return found.group(1) if found else ""


# ---------------------------------------------------------------------------
# the CSS is the reference's, character for character


def test_the_stylesheet_is_a_copy_of_the_reference():
    reference = re.search(
        r"<style>\n(.*?)\n</style>",
        (ROOT / "docs" / "reference" / "report.html").read_text(encoding="utf-8"),
        re.DOTALL,
    ).group(1)
    assert style.REFERENCE.strip() == reference.strip()


def test_the_only_addition_is_the_weak_trust_line():
    extra = style.CSS.replace(style.REFERENCE, "")
    rules = [line for line in extra.splitlines() if line.strip() and not line.strip().startswith("/*")]
    assert rules == [".facts .weak{color:var(--red)}"]


def test_the_stylesheet_reaches_the_page():
    for name in NAMES:
        if name == "landing":
            continue
        assert ".sheet{" in css_of(name), name


# ---------------------------------------------------------------------------
# no network, no script, no fonts


@pytest.mark.parametrize("name", NAMES)
def test_a_page_asks_nothing_of_the_network(name):
    body = html(name)
    assert "http://" not in body
    assert "https://" not in body
    assert "url(" not in body
    assert "@import" not in body
    assert "@font-face" not in body
    assert "<script" not in body.lower()


# ---------------------------------------------------------------------------
# nothing decorative


@pytest.mark.parametrize("name", NAMES)
def test_no_shadows_rounding_or_motion(name):
    body = html(name)
    for banned in BANNED_PROPERTIES:
        assert banned not in body, name + " uses " + banned


@pytest.mark.parametrize("name", NAMES)
def test_the_only_gradient_is_the_squared_paper(name):
    body = html(name)
    uses = re.findall(r"[a-z-]*gradient\([^)]*\)", body)
    assert len(uses) <= 2, name
    for use in uses:
        assert "var(--grid)" in use, use


@pytest.mark.parametrize("name", NAMES)
def test_every_color_comes_from_the_palette(name):
    allowed = {value.lower() for value in style.PALETTE.values()}
    for found in re.findall(r"#[0-9a-fA-F]{3,8}", css_of(name)):
        assert found.lower() in allowed, found + " in " + name


@pytest.mark.parametrize("name", NAMES)
def test_no_emoji_anywhere(name):
    found = EMOJI.search(html(name))
    assert found is None, repr(found.group(0)) + " in " + name


# ---------------------------------------------------------------------------
# the document itself


@pytest.mark.parametrize("name", NAMES)
def test_the_document_declares_what_it_is(name):
    body = html(name)
    assert body.startswith("<!doctype html>")
    assert '<html lang="en">' in body
    assert 'name="viewport"' in body
    assert 'name="color-scheme"' in body
    assert "<title>" in body
    assert body.count("<h1>") == 1


@pytest.mark.parametrize("name", NAMES)
def test_inline_styles_only_place_a_circle(name):
    for declaration in re.findall(r'\sstyle="([^"]*)"', html(name)):
        for piece in declaration.split(";"):
            if not piece.strip():
                continue
            prop = piece.split(":")[0].strip()
            assert prop in ("--x", "--y", "--w"), prop + " in " + name


@pytest.mark.parametrize("name", NAMES)
def test_every_drawing_is_labelled_or_hidden(name):
    for tag in re.findall(r"<svg\s[^>]*>", html(name)):
        labelled = 'role="img"' in tag and "aria-label=" in tag
        hidden = 'aria-hidden="true"' in tag
        assert labelled or hidden, tag[:90] + " in " + name


@pytest.mark.parametrize("name", NAMES)
def test_letter_spacing_is_only_the_heading_and_the_squares(name):
    for value in re.findall(r"letter-spacing:([^;}]+)", css_of(name)):
        cleaned = value.strip()
        assert cleaned == "calc(var(--cell) - 1ch)" or cleaned.endswith("em"), cleaned


# ---------------------------------------------------------------------------
# contrast


def _luminance(colour: str) -> float:
    channels = [int(colour[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    adjusted = [
        value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * adjusted[0] + 0.7152 * adjusted[1] + 0.0722 * adjusted[2]


def contrast(one: str, other: str) -> float:
    first, second = _luminance(one), _luminance(other)
    return (max(first, second) + 0.05) / (min(first, second) + 0.05)


@pytest.mark.parametrize("text,paper", style.TEXT_ON_PAPER)
def test_every_text_colour_is_readable_on_its_paper(text, paper):
    ratio = contrast(style.PALETTE[text], style.PALETTE[paper])
    assert ratio >= 4.5, text + " on " + paper + " is only " + format(ratio, ".2f") + ":1"


def test_the_grid_is_not_used_for_text():
    # It is squared-sheet ruling, and it is not readable as text in either
    # scheme. The rule above would fail if it ever became a text colour.
    assert contrast(style.PALETTE["grid"], style.PALETTE["paper"]) < 4.5
    assert contrast(style.PALETTE["grid-dark"], style.PALETTE["paper-dark"]) < 4.5


# ---------------------------------------------------------------------------
# size


def test_a_six_hundred_test_report_stays_under_a_hundred_and_fifty_kilobytes():
    tests = records(600, largest=30)
    tests[-1].verdict = Verdict.WA.value
    big = page.render(refuted(tests=tests))
    size = len(big.encode("utf-8"))
    assert size < 150 * 1024, str(size) + " bytes"


def test_the_strip_never_draws_more_than_a_hundred_and_sixty_bars():
    for count in (1, 159, 160, 161, 600, 5000):
        tests = [
            TestRecord(index + 1, 1, "normal", index, Verdict.PASS.value)
            for index in range(count)
        ]
        bars = page.strip(tests).count("<rect")
        assert bars <= page.MAX_BARS, str(count) + " tests drew " + str(bars)
        assert bars >= 1


# ---------------------------------------------------------------------------
# wording


def _report_strings() -> list[str]:
    """Every string literal in refute/report.py, which is where the page's prose lives."""
    tree = ast.parse((ROOT / "refute" / "report.py").read_text(encoding="utf-8"))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _wording_sources() -> dict[str, str]:
    out: dict[str, str] = {}
    readme = ROOT / "README.md"
    if readme.is_file():
        out["README.md"] = readme.read_text(encoding="utf-8")
    for path in sorted((ROOT / "docs").glob("*.md")):
        out[path.name] = path.read_text(encoding="utf-8")
    index = ROOT / "docs" / "index.html"
    if index.is_file():
        out["docs/index.html"] = index.read_text(encoding="utf-8")
    out["refute/report.py strings"] = "\n".join(_report_strings())
    return out


@pytest.mark.parametrize("word", BANNED_WORDS)
def test_none_of_the_banned_words_are_used(word):
    for where, text in _wording_sources().items():
        assert word not in text.lower(), word + " in " + where


def test_the_prose_on_the_page_has_no_banned_word():
    for name in NAMES:
        lowered = html(name).lower()
        for word in BANNED_WORDS:
            assert word not in lowered, word + " in " + name


def test_no_exclamation_marks_in_the_readme_or_the_pages():
    sources = _wording_sources()
    if "README.md" in sources:
        assert "!" not in sources["README.md"]
    for name in NAMES:
        prose = re.sub(r"<[^>]+>", " ", html(name))
        assert "!" not in prose, name


def test_the_page_never_claims_more_than_it_showed():
    for name in NAMES:
        lowered = html(name).lower()
        assert "is correct" not in lowered or "not a proof" in lowered
        assert "proof" not in lowered or "not a proof" in lowered or "not proof" in lowered


class _WellFormed(HTMLParser):
    """Closes every tag it opens, in order. A report is read by a browser."""

    VOID = {"meta", "br", "hr", "img", "input", "link", "path", "rect", "line", "use"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.open: list[str] = []
        self.problems: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.open.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.open:
            self.problems.append("closed " + tag + " with nothing open")
        elif self.open[-1] != tag:
            self.problems.append("closed " + tag + " inside " + self.open[-1])
        else:
            self.open.pop()


@pytest.mark.parametrize("name", NAMES)
def test_the_page_is_well_formed(name):
    parser = _WellFormed()
    parser.feed(html(name))
    assert parser.problems == [], name
    assert parser.open == [], name + " left " + str(parser.open) + " open"
