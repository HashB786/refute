# Refute design

## The idea
The report is a page of marked working, like a solved problem in a squared school notebook. Three inks say who is speaking:
- blue: what your program printed or wrote
- ink (graphite on paper, chalk on slate): Refute's own words and the expected answer
- red: the mismatch, circled once, by hand

The red circle is the one bold gesture in the product. Everything else is quiet: plain paper, serif prose, no cards, no icons, no decoration. Every page opens with the finding itself, never with a logo or a tagline. Dark mode is the same notebook as a chalkboard.

`docs/reference/report.html` is the visual source of truth. Copy its CSS verbatim into `refute/style.py` and keep its markup and class names. Only data and sentences change. Everything inside that file (numbers, names, explanation, diff) is placeholder data from a toy run: never copy it anywhere else. A report is one file, no JavaScript, no external requests.

## Palette and type
| token | light (notebook) | dark (chalkboard) | used for |
|---|---|---|---|
| paper | #fcfcfa | #1f2b29 | page |
| grid | #d9e0f0 | #2f403d | squared-sheet lines only |
| ink | #33363c | #e8e7df | Refute's words, the expected answer |
| pencil | #5f636b | #a3b0ab | secondary text, struck numbers, passing tests |
| blue | #1d3ba6 | #98b6ff | what your program printed or wrote |
| red | #cf222e | #ff9488 | the mismatch and the failing test, nothing else |

Do not add colors. Dark mode follows prefers-color-scheme; no toggle.
- Serif stack (all prose, headings, labels): Charter, "Bitstream Charter", "Sitka Text", Cambria, "Iowan Old Style", Georgia, serif
- Mono stack (program text only: inputs, outputs, commands, code): ui-monospace, "Cascadia Mono", "SF Mono", Menlo, Consolas, "DejaVu Sans Mono", monospace
- System fonts only: no web fonts, no network. Body 18px/28px, h1 weight 400, h2 22px bold, measure at most 34em. Sentence case everywhere. No uppercase, no letter-spaced labels, no eyebrow labels above headings.

## The report, in this order
1. `h1`, the finding as a sentence. Wrong answer: "{file} prints {got}. The right answer is {expected}." If a value is longer than 24 characters or spans lines: "{file} gives a wrong answer on this input." Crash: "{file} crashes on this input." Time limit: "{file} takes longer than {limit} s on this input." Not refuted: "No failing input found."
2. `p.lede`. "This is the smallest failing input Refute found, after {k} tests. Two independent reference solutions both print {expected}." With --oracle: "Your reference solution prints {expected}." Not refuted: "{k} tests, inputs up to {s} numbers, and {file} matched the reference solutions every time. That is evidence, not proof."
3. Hero `figure.sheet.hero` with rows input, expected, got (crash: input, error; time limit: input, time). One character per square. The circle is the reference's constant `svg.circle` path with `--x --y --w` set to the column, row and width in squares of a differing token on the got row; at most 3 circles, more differences go into the lede ("and {n} more differences"). Output beyond 8 lines is cut with a last line "...". Long lines scroll, never wrap or truncate. Then "Run it yourself:" and a `pre.cmd` with the exact command.
4. "How it got small", only if the shrinker accepted at least 2 steps. `figure.sheet.steps`, one row per accepted step, newline shown as `/` in pencil, numbers removed or changed by the next step struck (difflib on tokens against the next row). Labels only "found" on the first row and "smallest" on the last. More than 12 steps: keep the first 5 and last 6, with "{n} more steps" as the label of an empty row between them.
5. "What was tested", one `svg.strip`: one bar per test in run order, at most 160 bars (ceil(n/160) tests per bar). Height grows with the largest input size in the bar (log scale); red if it holds the failure, hollow if it holds a set-aside test, else pencil. A sentence under it gives counts, the largest input, and what was set aside and why.
6. "Why you can trust this", a `dl.facts` with dotted leaders: reference A and B reproduce the samples; the two agree (x of y compared); validator accepts the samples; validator rejects broken copies; generated inputs the validator accepts. A weak or failed item shows its value in red plus a short reason ("weak: accepted every broken copy"). Never green, never a check mark.
7. "What the model said" (skipped with --no-ai-explain): the line "Written by the model. Refute has not checked it.", then the diagnosis in italic.
8. "A suggested fix": "Verified: it passes the {n} samples, the counterexample and 200 new random tests." or "Unverified suggestion: it did not pass {what}." Then the diff (removed lines in pencil with the code struck, added lines in blue).
9. "What this run does not show": three plain sentences (the counterexample is a fact and the explanation is not; random testing can miss rare failures; a fix that passed random tests is not a proof).
10. `footer`, one sentence: version, date and time, duration, model, number of calls and how many came from cache.

Not refuted: the strip is the hero. No sheet, circle, staircase, model text or fix. Keep sections 5, 6 and 9. No green, no check mark, no celebration.

## Console (rich)
ASCII only. Plain text is Refute; blue is what your program printed; red only marks the mismatch; dim for secondary text. No boxes, no emoji. During stress, one progress line updated in place, only when stdout is a TTY. Honor NO_COLOR; with no color the parentheses still carry the meaning. Width at most 80.

```
refute 0.1.0  coin-change  greedy.py

samples    2 found, 2 checked against the statement
tooling    reference A ok, reference B ok, validator ok, generator ok
stress     182 tests, 1 failure
shrink     8 numbers -> 5 numbers in 9 steps

Refuted. greedy.py prints 3. The right answer is 2.

  input     3 6
            1 3 4
  expected  2
  got       (3)

  run it    python greedy.py < refute-out/counterexample.in
  report    refute-out/report.html
```
Every differing token on the got line is wrapped in parentheses and colored red. Not refuted ends with: "Not refuted. 600 tests, inputs up to 30 numbers, no failure. That is evidence, not proof."

## Landing page, docs/index.html
Same tokens, same CSS (inlined), same dark mode. In order: h1 "The smallest input that proves your solution wrong."; two plain sentences (what Refute is, and that running the code, not the model, decides); the finding and hero sheet copied from the real recorded demo report as the first visual; "Install and run" as a `pre`; "How the model is checked" as a plain list; "Results" as a plain table from bench/RESULTS.md (no zebra stripes, no rounded corners, numbers right-aligned in tabular figures); "Limits"; then plain text links to the repository, the Devpost page and one sample report. No hero gradient, feature cards, icons, device frames, buttons or arrows.

## README and wording
First line says what Refute does. Then the real terminal output of `refute demo` in a fenced block, then the 60-second quickstart. No badges, no emoji, no centered logo, no bullet lists with bold lead-ins, sentence-case headings. Write what happened and what to do next. No exclamation marks.
Banned words anywhere: powerful, seamless, supercharge, leverage, unlock, revolutionary, cutting-edge, state-of-the-art, AI-powered, next-generation, effortless, game-changing, delve, blazing, magic.

## Not allowed
Gradients (except the squared-paper background), shadows, blur, rounded corners, icons, emoji, cards, badges, pills, chips, numbered section markers (the sections are not a sequence), hover or entrance animation, hero statistics, middle-dot meta strings, arrows appended to links, web fonts, green or red status badges, any color outside the palette.

## tests/test_design.py
Render a refuted fixture, a not-refuted fixture and, if present, docs/index.html, then assert:
- no http(s) references, `url(`, `@import`, `@font-face`, `<script>`
- no `box-shadow`, `text-shadow`, `filter`, `backdrop-filter`, `animation`, `@keyframes`, `transition`, `border-radius`, `text-transform`
- `gradient(` appears at most twice and only with `var(--grid)`
- every hex color in the CSS is one of the 12 palette values
- no emoji or pictographs (U+2600-27BF, U+1F000-1FAFF, U+FE0F)
- `<html lang="en">`, viewport meta, `color-scheme` meta, `<title>`, exactly one `<h1>`
- inline `style=` only sets `--x`, `--y`, `--w`
- every `<svg>` has `role="img"` plus `aria-label`, or `aria-hidden="true"`
- `letter-spacing` is only h1 tracking (em) or `calc(var(--cell) - 1ch)`
- each text color reaches 4.5:1 on paper in light and in dark (WCAG relative luminance)
- report size under 150 KB for a 600-test run
- none of the banned words in README.md, docs/*.md, docs/index.html, or the string literals of refute/report.py

## Before finishing a phase that touches design
List everything on the page that looks like a template default (a card, an icon, a gradient, a label above a heading, a decorative number) and remove it. If you cannot say what information an element carries, delete it.

## Video
Terminal with the paper and ink colors, font 20 pt or larger. Browser at 100% zoom showing report.html. Open on the report's hero for the first 5 seconds, then explain. No music with lyrics.
