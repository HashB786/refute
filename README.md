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

time       99.8 s, 0 model calls, 0 tokens
```

The greedy solution takes the largest coin that fits. For coins 1, 5 and 6 and
an amount of 10 it takes a 6 and then four 1s, which is five coins, where two
5s would do. `refute-out/report.html` shows the input with the wrong number
circled, every test that ran, and what was checked before any of it was
believed.

On this run the shrinker accepted no steps. The input it was handed was
already five numbers, and nothing smaller that it tried still failed, so the
report leaves out its "how it got small" section rather than showing a
staircase of one.

## Quickstart, about a minute

Needs Python 3.11 or newer. Works on Windows, macOS and Linux.

```
git clone <this repository>
cd refute
python -m venv .venv
.venv/bin/pip install -e .          # .venv\Scripts\pip on Windows
.venv/bin/refute demo
```

That is the whole offline path. To run Refute on your own solution you need a
model endpoint. Put the settings in a `.env` file next to the repository:

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
you doubt. Useful options: `--samples DIR` for a directory of `1.in` and
`1.out` files instead of letting the model read the examples out of the
statement, `--oracle ref.py` to supply a reference solution you trust and skip
the model-written ones, `--no-ai-explain` to stop before the diagnosis and the
suggested fix, `--budget N` to cap the number of tests, and `--offline` to
work from the cache alone.

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
  across fifteen size and mode combinations, give the same input for the same
  seed, and give different inputs for different seeds.

Anything the model writes is parsed first and refused if it imports outside a
short list or reaches for `open`, `exec`, `eval` or dunder attributes. A
script that fails any check goes back to the model with the exact failure,
three times at most. The diagnosis in the report is the one thing nothing can
check, so it is labelled as the model's words and left unchecked. A suggested
fix is called verified only after it passes the examples, the counterexample
and two hundred tests on seeds the first run never used.

Model replies are cached on disk by a hash of the prompt, so the tooling for a
problem is written once and reused for every solution you test against it.

## What it does not show

"No counterexample found" is not "correct". When Refute finds nothing it
reports how many tests ran, how large the inputs got and how often the two
reference solutions agreed, and says plainly that this is evidence and not a
proof. A counterexample is a fact, because your program and both references
were run on it. The explanation beside it is not.

## Security

Refute runs code it did not write: the model's scripts and your target. The
guard and the sandbox catch mistakes, not attacks. They are a fresh temporary
directory per run, a timeout and a process-group kill, a cap on output, and a
memory limit where the platform has one. Treat them as such. Use the supplied
Dockerfile with `--network none` if what you are testing came from somewhere
you do not trust.

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

## Licence

MIT. See LICENSE.
