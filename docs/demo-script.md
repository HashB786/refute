# Demo script, 2 minutes 45 seconds

Two windows. A browser at 100% zoom on `docs/sample-report.html`, and a
terminal in the repository root, font 20 pt or larger, with the paper and ink
colours from DESIGN.md. No music with lyrics. Every number spoken is on screen
at the time or in `bench/RESULTS.md`.

Run `refute demo` once before recording, so the model output is in the cache
and the run in the video needs no network.

One honest note about the edit. The run in section three takes about two
minutes on the machine this was timed on: roughly forty seconds to re-run the
tooling checks, twenty-five to stress, thirty to shrink. It does not fit in a
seventy second slot. Cut once, in the gap between the `tooling` line and the
`stress` line, where nothing happens on screen but a progress counter. Nothing
else is cut, nothing is sped up, and no output is retyped.

## 0:00 - 0:15, the finding, before any explanation

On screen: the browser, at the top of `docs/sample-report.html`. Hold still on
the hero sheet for the full fifteen seconds. Do not scroll.

Say: "A greedy solution to a coin change problem prints five. The right answer
is two. The input is three coins and an amount, and the wrong number is
circled. A tool called Refute found that input, and this is its report."

## 0:15 - 0:35, the problem

On screen: the terminal.

```
cat examples/coin-change/greedy.py
```

Say: "Here is the solution. It takes the largest coin that fits, which is what
most people write first. It passes both examples in the statement, so reading
the statement will not catch it. For coins one, five and six, paying ten takes
a six and then four ones. Two fives would do."

## 0:35 - 1:45, the live run

On screen: run it. Let the progress line move.

```
refute demo
```

Say, while the tooling line is being written: "A language model has just
written four throwaway scripts for this problem: two reference solutions by
different methods, an input validator, and an input generator. None of them is
trusted because the model wrote them. Each one had to earn its place by
running. Both references had to reproduce both examples in the statement. The
validator had to accept both examples and then reject three deliberately
broken copies of each: one with a token missing, one with a token too many,
one with a number pushed out of range. The generator had to produce valid
input at fifteen combinations of size and mode, and give different inputs for
different seeds."

[cut here]

Say, as the stress line appears: "Now it generates inputs, smallest first,
runs both references and runs the solution. A failure is only reported when
both references print the same answer. A hundred and twenty-eight tests, one
failure, five numbers."

Say, at the finding: "The model's diagnosis is in the report, labelled as the
model's words and unchecked. The fix next to it was checked by running it."

## 1:45 - 2:25, the code

On screen: the terminal, two extracts.

```
sed -n '/def validator_check/,/return check/p' refute/synth.py
```

Say: "This is the whole idea in one function. The validator the model wrote
has to accept every example, and then it has to reject those broken copies. A
validator that accepts all of them is not reading the constraints, and the
report says so instead of hiding it."

```
sed -n '/^def compound_moves/,/^def line_moves/p' refute/shrink.py
```

Say: "And this is what makes a counted input shrink at all. An input shaped n,
then n numbers, cannot lose a number by itself, because the validator rejects
the result for not matching its own count. So the count and the number it
counts come down together, as one step."

## 2:25 - 2:45, results and limits

On screen: the browser, at the Results table on `docs/index.html`.

Say: "Six classic problems, eighteen planted bugs, seventeen found. The miss
is listed with its reason. Two caveats. The bugs were written by the same
agent that built Refute. And finding nothing is not proof: it reports how hard
it tried and calls that evidence."
