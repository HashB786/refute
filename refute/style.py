"""The report's CSS, copied from docs/reference/report.html.

DESIGN.md makes that file the visual source of truth and says to copy its CSS
here and keep its class names. REFERENCE is that copy, character for
character apart from the closing newline; tests/test_design.py compares the
two and fails if they drift.

One rule is added after it. DESIGN.md says a weak or failed line in the trust
list shows its value in red, and the reference has no weak example, so it has
no rule for one. Nothing else is added and nothing is changed.
"""

from __future__ import annotations

#: The twelve colors the report is allowed to use: six for paper, six for
#: the chalkboard. Named here so the design tests can check that no other
#: color reached the page.
PALETTE: dict[str, str] = {
    "paper": "#fcfcfa",
    "grid": "#d9e0f0",
    "ink": "#33363c",
    "pencil": "#5f636b",
    "blue": "#1d3ba6",
    "red": "#cf222e",
    "paper-dark": "#1f2b29",
    "grid-dark": "#2f403d",
    "ink-dark": "#e8e7df",
    "pencil-dark": "#a3b0ab",
    "blue-dark": "#98b6ff",
    "red-dark": "#ff9488",
}

#: Text colors and the paper they sit on, in both schemes, for the contrast
#: check in tests/test_design.py.
TEXT_ON_PAPER: tuple[tuple[str, str], ...] = (
    ("ink", "paper"),
    ("pencil", "paper"),
    ("blue", "paper"),
    ("red", "paper"),
    ("ink-dark", "paper-dark"),
    ("pencil-dark", "paper-dark"),
    ("blue-dark", "paper-dark"),
    ("red-dark", "paper-dark"),
)

REFERENCE = """:root{
  color-scheme: light dark;
  --paper:#fcfcfa; --grid:#d9e0f0; --ink:#33363c; --pencil:#5f636b; --blue:#1d3ba6; --red:#cf222e;
  --serif: Charter, "Bitstream Charter", "Sitka Text", Cambria, "Iowan Old Style", Georgia, serif;
  --mono: ui-monospace, "Cascadia Mono", "SF Mono", Menlo, Consolas, "DejaVu Sans Mono", monospace;
  --cell: 28px;
}
@media (prefers-color-scheme: dark){
  :root{ --paper:#1f2b29; --grid:#2f403d; --ink:#e8e7df; --pencil:#a3b0ab; --blue:#98b6ff; --red:#ff9488; }
}
*{box-sizing:border-box}
html{background:var(--paper);-webkit-text-size-adjust:100%}
body{margin:0;color:var(--ink);font:18px/28px var(--serif);text-rendering:optimizeLegibility}
main{max-width:672px;margin:0 auto;padding:56px 20px 112px}
h1{font-weight:400;font-size:clamp(30px,6.2vw,48px);line-height:1.12;letter-spacing:-.012em;margin:0 0 20px;text-wrap:balance}
h2{font-size:22px;line-height:28px;font-weight:700;margin:56px 0 14px}
p{margin:0 0 14px;max-width:34em}
.lede{font-size:20px;margin-bottom:28px}
.note,.by,footer{color:var(--pencil)}
.note{font-size:16px;line-height:24px}
.by{font-size:16px;margin-bottom:6px}
.model{font-style:italic}
a{color:inherit}
code{font:15px var(--mono)}
pre.cmd{margin:-6px 0 0;overflow-x:auto}
.cmd code{user-select:all}

/* the squared sheet: one character per square */
.sheet{margin:0 0 14px;padding:var(--cell);border:1px solid var(--grid);overflow-x:auto;
  background-image:linear-gradient(var(--grid) 1px,transparent 1px),linear-gradient(90deg,var(--grid) 1px,transparent 1px);
  background-size:var(--cell) var(--cell)}
.sheet:focus-visible{outline:2px solid var(--blue);outline-offset:3px}
.row{display:grid;grid-template-columns:calc(var(--cell)*var(--lab,4)) max-content;align-items:start}
.label{font:italic 16px/var(--cell) var(--serif);color:var(--pencil)}
.hero .label{font-size:18px}
.cells{position:relative;font:calc(var(--cell)*1.08)/var(--cell) var(--mono);letter-spacing:calc(var(--cell) - 1ch);white-space:pre}
.line{height:var(--cell);padding-left:calc((var(--cell) - 1ch)/2)}
.gap{height:var(--cell)}
.got .cells{color:var(--blue)}
.hero{--cell:36px}
.steps{--cell:20px;--lab:3}
.steps .cells{font-size:calc(var(--cell)*.95)}
.steps s{color:var(--pencil);text-decoration-line:line-through;text-decoration-thickness:2px}
.nl{color:var(--pencil)}
.circle{position:absolute;left:calc((var(--x) - .4)*var(--cell));top:calc((var(--y) - .27)*var(--cell));
  width:calc((var(--w) + .8)*var(--cell));height:calc(var(--cell)*1.55);overflow:visible;pointer-events:none}
.circle path{fill:none;stroke:var(--red);stroke-width:2.6;stroke-linecap:round;stroke-linejoin:round;vector-effect:non-scaling-stroke}

/* one bar per test */
.strip{display:block;width:100%;height:auto;margin:6px 0 14px}
.strip rect{fill:var(--pencil)}
.strip .skip{fill:none;stroke:var(--pencil);stroke-width:1}
.strip .fail{fill:var(--red)}
.strip line{stroke:var(--pencil);stroke-width:1}

.facts{margin:0 0 14px;max-width:34em}
.facts div{display:flex;align-items:baseline;gap:8px}
.facts dt{display:flex;flex:1 1 auto;align-items:baseline;gap:8px}
.facts dt::after{content:"";flex:1 1 16px;border-bottom:1px dotted var(--pencil);transform:translateY(-5px)}
.facts dd{margin:0;flex:none;font-variant-numeric:tabular-nums}

.diff{font:15px/22px var(--mono);margin:0 0 14px;white-space:pre;overflow-x:auto}
.diff .del{color:var(--pencil)}
.diff .del s{text-decoration-thickness:1.5px}
.diff .add{color:var(--blue)}
ul{padding-left:1.15em;margin:0 0 14px}
li{margin-bottom:6px;max-width:34em}
footer{margin-top:56px;font-size:15px;line-height:24px;max-width:34em}

@media (max-width:560px){
  body{font-size:17px}
  main{padding-top:36px}
  .sheet{--cell:22px;--lab:3}
  .hero{--cell:26px}
  .steps{--cell:16px;--lab:4}
  .label,.hero .label{font-size:15px}
  .facts div{display:block;margin-bottom:8px}
  .facts dt{display:block}
  .facts dt::after{content:none}
  .facts dd{color:var(--pencil)}
}
"""

#: The one addition. See the module docstring. DESIGN.md asks for the value of
#: a weak or failed trust line to be shown in red, and the reference has no
#: weak example, so it carries no rule for one.
WEAK_BLOCK = """
/* a trust line that failed */
.facts .weak{color:var(--red)}
"""

CSS = REFERENCE + WEAK_BLOCK
