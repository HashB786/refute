# Refute

Refute finds the smallest input that proves a program wrong. A language model writes the throwaway test tooling for a problem: two independent reference solutions, an input validator and an input generator. Every piece is checked by running it. The model never decides that your code is wrong; execution does.

Built for EvoHacks 2026 (theme: AI for Automation). Every line of code in this repo must be written on Oct 10-11, 2026. Do not copy code from other projects.

Prior art the README must credit: CounterGen (Codeforces blog entry 145626, open source, LLM-assisted counterexamples) and CodeHacker (arXiv 2602.20213, adversarial test generation research). Do not claim the core idea is new. State what this project adds: cross-checked oracles, a calibrated validator and a validated generator, validator-constrained shrinking, verified fixes, and a benchmark that reports its misses.

## Principles (apply to every phase)
1. AI proposes, execution verifies. Model-written text is labeled as such everywhere unless a run confirmed it.
2. No fake results. Benchmark numbers and sample reports come from real runs, misses included. Mock the LLM in tests only, never execution. Placeholder data in docs/reference/ must never be copied into the README or docs.
3. "No counterexample found" is not "correct". Report coverage (tests run, largest size, oracle agreement) and say it is not a proof.
4. Plain, specific wording in README, CLI output and report. No emojis, no hype words, no unexplained acronyms.
5. Dependencies: Python 3.11+, httpx, rich, pytest. Nothing else. Works on Windows, macOS and Linux.
6. Flat package, small files, no base classes or plugin systems, no TODO stubs, no dead code.
7. For practice and learning. Never for live contests; say so in the README.
8. DESIGN.md governs all HTML, CSS, console output, README and docs wording. It is added to the repo before Phase 1B. docs/reference/report.html is the visual reference for the report: reproduce its markup and CSS, do not invent a style.

## Rules for the coding agent (tight usage budget)
- Do not ask questions; make reasonable decisions and note them in the commit message.
- Do not explore the repo. Do not re-read files you just wrote. Write each file in one go.
- Run only `pytest -q -x` and the single command a phase names. No full reruns after small edits.
- Real model calls only at steps marked "manual check". Tests use FakeLLM.
- No network except the LLM endpoint. Do not install browsers or download fonts or assets.
- Use pathlib, and subprocess without shell=True, so everything runs on Windows.
- If the same failure happens twice, stop and report the smallest repro. Do not loop.
- End of a phase: tests green, `git commit`, print at most 10 lines of status, stop.

## Pipeline
Input: a problem statement (markdown) and a target solution (.py, or .cpp if g++ exists). Optional: `--oracle ref.py` (trusted; skips model-written oracles), `--samples DIR` (1.in, 1.out, ...), `--float-tol X`.

1. Samples. From --samples, or ask the model to extract them. Discard any extracted sample that does not appear verbatim (whitespace-normalized) in the statement.
2. Tooling. Four model-written Python scripts, each returned as one fenced code block. Each passes the guard, runs as a separate process, and is repaired up to 3 times with the concrete failure fed back. Tooling depends only on the statement and samples, never on the target, so one cached set serves every target for that problem.
   - oracle_a: simplest correct brute force. oracle_b: a different algorithm. Both must reproduce every sample output.
   - validator: reads stdin, exit 0 only if the input meets the statement's format and constraints. Must accept all samples. Calibration: deterministically mutate each sample (drop the last token, append a token, push the first integer out of range) and require it to reject at least one mutation per sample. If it accepts all, repair; if still weak, continue and mark "weak validator" in the trust section.
   - generator: `gen.py SEED SIZE MODE`, seeded only through random.Random(SEED). SIZE 1..30 scales the main dimension. MODE is tiny_values (small ranges to force ties, duplicates, zeros), normal, or extreme (bounds, negatives). Smoke set (sizes 1,2,5,10,30 x 3 modes x 5 seeds) must pass the validator 100% and produce varied outputs.
3. Stress. Passes over sizes 1,2,3,4,5,6,7,8,10,12,15,20,30 x 3 modes; each pass uses 8 new seeds per (size, mode); thread pool. Per test: generate, validate, run oracle_a, run target. Run oracle_b only when target and oracle_a differ, and on every 10th test to estimate agreement. Verdicts: PASS, WA (token compare), RE (nonzero exit), TLE (target times out while oracles finish), ORACLE_CONFLICT (oracles disagree; excluded, counted), ORACLE_TIMEOUT and INVALID (skipped, counted). A failure is confirmed only when both oracles agree. Stop at the first confirmed failure or the budget (default 600 tests or 90 s). Record every test (index, size, mode, verdict) for the report.
4. Shrink (time-boxed, default 30 s). Find the smallest size with a confirmed failure (60 seeds per smaller size, 200 at the failing size), keep the shortest failing input, then minimize values (each number tried as 0, 1, -1, v//2, v-1) and delete tokens or lines. Accept a candidate only if the validator accepts it, both oracles agree, and the same verdict kind persists. Repeat to fixpoint. Re-run the result 3 times. Keep the list of accepted steps (input text at each step) in shrink.json.
5. Diagnose and fix (skip with --no-ai-explain). The model gets statement, target source, minimal input, expected and actual output, and returns a diagnosis of at most 4 sentences plus a corrected full solution. The fix is "verified" only if it passes the samples, the counterexample and 200 fresh stress tests; otherwise label it "unverified suggestion".
6. Output directory `refute-out/`: report.html, report.json, counterexample.in, expected.out, actual.out, shrink.json. Plus a console summary. Report sections and visuals follow DESIGN.md and docs/reference/report.html.

## Guard and sandbox
- Guard (model-written scripts and fixes only): parse with ast; allow imports only from sys, math, random, itertools, collections, heapq, bisect, functools, fractions, decimal, string, re, array; reject open, exec, eval, compile, __import__, globals, and dunder attribute access.
- Sandbox: fresh temp dir per run; subprocess with timeouts (validator and generator 5 s, oracle 10 s, target 2 s by default); POSIX memory limit via resource; kill the process group on timeout; input only via stdin; captured output capped at 1 MB. Windows must work.
- The README states plainly that Refute runs untrusted code and recommends the provided Dockerfile with --network none.

## LLM layer (refute/llm.py)
- REFUTE_PROVIDER = openai (any OpenAI-compatible /chat/completions endpoint) or anthropic (/v1/messages). Also REFUTE_BASE_URL, REFUTE_API_KEY, REFUTE_MODEL, REFUTE_MAX_TOKENS (default 3000), optional REFUTE_REASONING_EFFORT (sent as `reasoning_effort`; if the endpoint rejects it with HTTP 400, retry once without it and stop sending it). temperature 0.2.
- Config loading: read KEY=VALUE lines from ./.env with a tiny parser (no dependency); real environment variables win. Never log or print keys.
- Throttle by both requests and tokens: sliding 60 s window, REFUTE_MAX_RPM (default 20) and REFUTE_MAX_TPM (default 6000). Token use comes from the response `usage`, else len(text)/3. Before a call, wait until the prompt estimate plus 1500 reserved output tokens fits. On 429 honor Retry-After, else back off; also back off on 5xx. A response with finish_reason "length" counts as a failed attempt.
- Every call has a `purpose` (samples, oracle_a, oracle_b, validator, generator, diagnose, fix). FakeLLM in tests answers by purpose.
- Disk cache in .refute_cache/ (override with --cache-dir). Key = sha256(purpose + prompt); value stores model, provider, usage, response. `--offline` = cache only, clear error on a miss.
- Determinism so recorded runs replay: seed every random choice; sort anything fed back to the model; replace temp paths in feedback with <tmp>.
- .env and .refute_cache/ stay in .gitignore; examples/cache/ is committed.

## Layout
pyproject.toml (console script `refute`), refute/{cli,llm,guard,sandbox,synth,stress,shrink,fix,report,style,models}.py, tests/, examples/coin-change/, examples/cache/, bench/problems/, docs/, README.md, LICENSE (MIT), .gitignore, Dockerfile.
The report CSS lives in refute/style.py as a string constant (no package-data files).
CLI: `refute ping`, `refute run STATEMENT SOLUTION [--oracle F] [--samples D] [--no-ai-explain] [--budget N] [--cache-dir D] [--offline]`, `refute demo`, `refute bench [--resume]`.

## Phases (one per session; stop after each)
Phase 1A, foundations. pyproject.toml, .gitignore (extend if it exists), LICENSE, refute/models.py, llm.py (providers, .env loader, throttle, retries, cache, offline, FakeLLM), guard.py, sandbox.py, cli.py with `refute ping` (prints provider, model, latency, token usage and any x-ratelimit-* response headers). Tests: guard rejects bad imports, builtins and dunders and accepts allowed code; sandbox timeout, nonzero exit, stdin delivery, output cap; llm cache hit, miss, offline error, .env parsing with real env winning, throttle logic with a fake clock. Manual check: `refute ping` with a real key.

Phase 1B, pipeline. First check that DESIGN.md exists; if not, stop and say so. synth.py, stress.py, shrink.py, `refute run` with the console output described in DESIGN.md, examples/coin-change/ (original statement: minimum coins for an amount with arbitrary denominations that always include 1; samples; a greedy solution that passes the samples but is wrong). Tests: verbatim sample filter, validator calibration mutations, generator smoke set, shrinker on a synthetic failing predicate, shrink steps recorded, stress and shrink end to end with FakeLLM and canned tooling. Manual check: with a real key, `refute run examples/coin-change/statement.md examples/coin-change/greedy.py --no-ai-explain` prints the minimal counterexample.

Phase 2, report and offline demo. fix.py; report.py and style.py producing report.html that reproduces docs/reference/report.html with real data (and a "not refuted" variant), report.json; tests/test_design.py implementing the lint rules in DESIGN.md. Manual check: run the example once with a real key and --cache-dir examples/cache to record model outputs. `refute demo` replays them offline and says so (execution is real, model outputs are recorded). README quickstart.

Phase 3, benchmark. bench/problems/<slug>/ with statement.md, ref.py (correct), truth_gen.py (small random input generator), bugs/*.py. 6 classic problems (arrays, greedy, DP, binary search, graphs, strings; at least 2 where a wrong greedy is the bug), at least 3 bugs each (wrong greedy, off-by-one, bad initialization, missed edge case, non-termination). Write all statements and code from scratch. A script verifies that each bug passes the statement's samples and fails against ref.py on some truth_gen input. `refute bench` runs Refute blind (it never sees ref.py) with --no-ai-explain, generating tooling once per problem and reusing it for every bug of that problem; `--resume` skips finished pairs recorded in bench/results.json. It writes bench/RESULTS.md: per bug found or missed, verdict, tests to first failure, minimal input length, seconds; per problem the model calls and the trust figures (oracle agreement, validator calibration); then totals (found X of N, medians). State that every bug passes the samples by construction, that the bugs were written by the coding agent and not collected from real submissions, and that tooling is shared across bugs of a problem. List the misses and their likely reasons.

Phase 4, optional, only with budget left. (a) Single-edit repair by AST mutation for .py targets (comparison operators, +-1 constants, range bounds, min/max swaps); accept an edit only if it passes the full stress suite; report it as a verified minimal patch. (b) Uzbek check: run Refute on a statement written in Uzbek that the user supplies as examples/coin-change/statement.uz.md and record in the README whether it worked, pass or fail.

Phase 5, submission. Final README (what it does, 60-second offline quickstart, how AI is used and checked, real benchmark table, limitations, security note, prior art, practice-only note), docs/index.html (static page following DESIGN.md, showing a real report from the recorded demo and the real benchmark, for GitHub Pages), docs/devpost.md (at most 350 words, standard Devpost headings, every claim backed by bench/RESULTS.md), docs/demo-script.md (a 2:45 script with exact on-screen commands and timings: failing report first for 15 s, the problem 20 s, live run 70 s, code walkthrough of synth.py and shrink.py 40 s, results and limits 20 s), Dockerfile.
