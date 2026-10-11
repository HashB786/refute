# Refute

Refute finds the smallest input that proves a program wrong. A language model
writes the throwaway test tooling for a problem: two independent reference
solutions, an input validator and an input generator. Every piece is checked
by running it. The model never decides that your code is wrong; execution
does.

## What a run looks like

This is `refute demo` on the worked example in this repository, a greedy
solution to a coin-change problem. The model output is replayed from a
recording in `examples/cache`, so it needs no key and no network. Everything
else happens for real: the two reference solutions, the validator, the
generator, the target and the suggested fix are all run as subprocesses while
you wait.

```
demo       replaying recorded model output; every program here is really run

refute 0.1.0  coin-change  greedy.py

samples    2 examples given with --samples
tooling    reference A ok, reference B ok, validator ok, generator ok
stress     128 tests, 1 failure
shrink     5 numbers -> 5 numbers in 0 steps
explain    diagnosis written, fix verified

Refuted. greedy.py prints 5. The right answer is 2.

  input     3 10
            1 5 6
  expected  2
  got       (5)

  run it    python examples/coin-change/greedy.py < refute-out/counterexample.in
  report    refute-out/report.html

time       166.2 s, 0 model calls, 5882 tokens
```

The greedy solution takes the largest coin that fits. For coins 1, 5 and 6 and
an amount of 10 it takes a 6 and then four 1s, which is five coins, where two
5s would do. `refute-out/report.html` shows the input with the wrong number
circled, every test that ran, and what was checked before any of it was
believed. A copy of that page is committed as `docs/sample-report.html`.

On this run the shrinker accepted no steps. The input it was handed was
already five numbers, and nothing smaller that it tried still failed, so the
report leaves out its "how it got small" section rather than showing a
staircase of one. The 5882 tokens are what writing that tooling cost when it
was first recorded, not what this run spent; it spent none.

## Quickstart, about a minute

Needs Python 3.11 or newer. Works on Windows, macOS and Linux.

```
git clone <this repository>
cd refute
python -m venv .venv
.venv/bin/pip install -e .          # .venv\Scripts\pip on Windows
.venv/bin/refute demo
```

That is the whole offline path: no key, no network. To run Refute on your own
solution you need a model endpoint. Put the settings in a `.env` file next to
the repository:

```
REFUTE_PROVIDER=openai
REFUTE_BASE_URL=https://api.example.com/openai/v1
REFUTE_API_KEY=...
REFUTE_MODEL=some-model
```

`REFUTE_PROVIDER` is `openai` for any endpoint that speaks
`/chat/completions`, or `anthropic` for `/v1/messages`. Check it with
`refute ping`, which makes one call and prints the provider, the model, the
latency, the token usage and any rate-limit headers the endpoint sent. Then:

```
refute run statement.md solution.py
```

`statement.md` is the problem in plain markdown. `solution.py` is the program
you doubt; a `.cpp` file works too if `g++` is on the path. Useful options:
`--samples DIR` for a directory of `1.in` and `1.out` files instead of letting
the model read the examples out of the statement, `--oracle ref.py` to supply
a reference solution you trust and skip the model-written ones,
`--no-ai-explain` to stop before the diagnosis and the suggested fix,
`--budget N` to cap the number of tests, `--cache-dir D` to choose where
model replies are kept, and `--offline` to work from that cache alone.

`refute run` exits 0 when it refutes the target, 2 when it does not, and 1 on
an error.

## How the model is used, and how it is checked

The model writes four Python scripts per problem, and each one has to earn its
place by running:

- Two reference solutions, by different methods. Both must reproduce every
  example in the statement. A failure is reported only when both of them
  print the same answer; where they disagree the test is set aside and
  counted, and nothing is claimed about your program.
- A validator. It must accept every example, and it must reject deliberately
  broken copies of them: one with the last token removed, one with a token
  added, one with the first number pushed out of range. A validator that
  accepts all three is not reading the constraints, and the report says so.
- A generator, run as `gen.py SEED SIZE MODE`. It must produce valid input
  across fifteen combinations of size and mode, give the same input for the
  same seed, and give different inputs for different seeds.

Anything the model writes is parsed first and refused if it imports outside a
short list or reaches for `open`, `exec`, `eval` or dunder attributes. A
script that fails any check goes back to the model with the exact failure,
three times at most. The diagnosis in the report is the one thing nothing can
check, so it is labelled as the model's words and left unchecked. A suggested
fix is called verified only after it passes the examples, the counterexample
and two hundred tests on seeds the first run never used.

Model replies are cached on disk by a hash of the prompt, so the tooling for a
problem is written once and reused for every solution you test against it.

## Benchmark

`bench/problems` holds six classic problems, one each for arrays, greedy
algorithms, dynamic programming, binary search, graphs and strings, with
eighteen planted bugs between them. `bench/verify.py` checks the data by
running it: every bug reproduces the examples in its own statement, and every
bug disagrees with that problem's reference solution on some generated input.
`refute bench` then runs Refute against each bug blind, with
`--no-ai-explain`, never seeing the reference.

| problem | bugs found | tests to first failure | smallest input | oracles agree |
|---|--:|--:|--:|---|
| distinct-stretch | 3 of 3 | 96 | 1 | 38 of 38 |
| fewest-hops | 3 of 3 | 64 | 4 | 62 of 62 |
| max-subarray | 3 of 3 | 32 | 3 | 22 of 22 |
| pick-talks | 2 of 3 | 152 | 6 | 63 of 63 |
| ship-the-boxes | 3 of 3 | 64 | 4 | 60 of 60 |
| split-the-load | 3 of 3 | 16 | 2 | 61 of 61 |
| all six | 17 of 18 | 64 | 3 | 306 of 306 |

Tests to first failure and smallest input are medians over the bugs that were
found, and the smallest input is counted in whitespace-separated tokens. The
budget was 300 tests or 45 seconds a bug, with 15 seconds to shrink. Tooling
is shared across the bugs of a problem, so all eighteen cost 32 model calls.

By kind: off-by-one 7 of 7, bad initialization 3 of 3, missed edge case 2 of
2, non-termination 1 of 1, wrong algorithm 2 of 2, wrong greedy 2 of 3. The
miss was a wrong greedy on interval scheduling: 300 tests on inputs of up to
25 tokens all matched both references, so the budget ran out before a
difference turned up. `bench/RESULTS.md` is the full write-up, bug by bug,
and it lists that miss and its reason.

Two things to hold against these numbers. The bugs were written by the same
agent that built Refute, not collected from real submissions, so the benchmark
is marking its own homework. And every bug passes its statement's examples by
construction, which is the situation Refute is for but is not the only
situation that matters.

## Limitations

- "No counterexample found" is not "correct". When Refute finds nothing it
  reports how many tests ran, how large the inputs got and how often the two
  reference solutions agreed, and says plainly that this is evidence and not a
  proof.
- A counterexample is a fact, because your program and both references were
  run on it. The explanation beside it is not.
- A fix that passed two hundred fresh random tests is evidence, not a proof.
- Everything rests on the model being able to write a correct brute force for
  the problem. For a problem where it cannot, the two references tend to
  disagree, and Refute sets those tests aside and reports the disagreement
  rather than guessing.
- The shrinker is time-boxed, so on a slow machine it can stop short of the
  smallest input. The report says how many steps it accepted.
- Problems whose input is not a few lines of numbers, and interactive
  problems, are out of scope.

## Security

Refute runs code it did not write: the model's scripts and your target. The
guard and the sandbox catch mistakes, not attacks. They are a fresh temporary
directory per run, a timeout and a process-group kill, a cap on output, and a
memory limit where the platform has one. Treat them as such. Use the supplied
`Dockerfile` with `--network none` if what you are testing came from somewhere
you do not trust:

```
docker build -t refute .
docker run --rm --network none -v "$PWD:/work" refute \
    run /work/statement.md /work/solution.py --no-ai-explain --offline
```

## For practice, not for contests

This is a tool for learning and for practice on problems you already have.
Do not use it during a live contest. Doing so breaks the rules of every judge
worth competing on.

## Prior art

The idea of having a language model write test tooling to hunt for
counterexamples is not new here.

- CounterGen, Codeforces blog entry 145626: open source, generates
  counterexamples with a model's help.
- CodeHacker, arXiv 2602.20213: research on adversarial test generation
  against submitted code.

What this adds is the checking around the model. Two independent oracles that
have to agree before any verdict is reported. A validator that is calibrated
against broken inputs instead of being taken at its word. A generator proved
on a smoke set. Shrinking that only accepts an input the validator still
accepts. Fixes that are verified by running them. And a benchmark that
reports its misses.

## Layout

```
refute/            the package: cli, llm, guard, sandbox, synth, stress,
                   shrink, fix, report, style, bench, models
examples/          the worked example and the recorded model output for it
bench/             six problems, eighteen planted bugs, verify.py, RESULTS.md
docs/              index.html, the sample report, the write-up, the demo
                   script, and the design reference the report follows
tests/             the test suite
```

`DESIGN.md` governs the report, the console output and the wording.
`docs/reference/report.html` is the visual source of truth for the report, and
`refute/style.py` holds a verbatim copy of its stylesheet.

## Licence

MIT. See LICENSE.
