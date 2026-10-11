# Benchmark results

Found 17 of 18 planted bugs.

This run used a budget of 300 tests or 45 seconds per bug, with 15 seconds to shrink. A larger budget would find more.

- Refute was handed each statement and each planted bug and nothing else.
- It never saw ref.py or truth_gen.py; those exist so bench/verify.py can show that every bug really is one.
- Every bug reproduces the examples in its statement by construction, so none of them can be caught by reading the statement.
- The bugs were written by the coding agent that built Refute. They are not collected from real submissions, and a benchmark marking its own homework is weaker evidence than one that does not.
- The tooling for a problem is written once from the statement and reused for every bug of that problem, so the model calls below are per problem, not per bug.
- Model replies are cached by a hash of the prompt. A second run of this benchmark reads the tooling back from that cache, so the calls and tokens below are what writing the tooling cost, not what the run that produced this file spent.

## Per bug

| problem | bug | kind | found | verdict | tests | input | seconds |
|---|---|---|---|--:|--:|--:|--:|
| distinct-stretch | misses last letter | off-by-one | yes | WA | 16 | 1 | 11.3 |
| distinct-stretch | restarts the stretch | wrong algorithm | yes | WA | 112 | 1 | 27.4 |
| distinct-stretch | shifts left once | off-by-one | yes | WA | 96 | 1 | 22.7 |
| fewest-hops | drops last door | off-by-one | yes | WA | 64 | 4 | 22.8 |
| fewest-hops | stack not queue | wrong algorithm | yes | WA | 256 | 16 | 50.2 |
| fewest-hops | unreachable is zero | missed edge case | yes | WA | 16 | 2 | 11.3 |
| max-subarray | misses last | off-by-one | yes | WA | 32 | 3 | 16.8 |
| max-subarray | skips first | off-by-one | yes | WA | 32 | 3 | 18.0 |
| max-subarray | zero start | bad initialization | yes | WA | 16 | 2 | 14.9 |
| pick-talks | by length | wrong greedy | no | - | 300 | - | 37.9 |
| pick-talks | by start | wrong greedy | yes | WA | 192 | 7 | 40.4 |
| pick-talks | touching clashes | off-by-one | yes | WA | 112 | 5 | 23.9 |
| ship-the-boxes | days from zero | off-by-one | yes | WA | 64 | 4 | 22.9 |
| ship-the-boxes | half the sum | bad initialization | yes | WA | 64 | 4 | 18.5 |
| ship-the-boxes | never narrows | non-termination | yes | TLE | 64 | 4 | 35.7 |
| split-the-load | both lorries used | missed edge case | yes | WA | 16 | 2 | 14.9 |
| split-the-load | greedy largest | wrong greedy | yes | WA | 256 | 7 | 54.0 |
| split-the-load | zero best | bad initialization | yes | WA | 16 | 2 | 16.1 |

Tests is how many ran before the first confirmed failure. Input is the size of the smallest failing input Refute kept, counted in whitespace-separated tokens, so a word counts as one.

## Per problem

| problem | samples | repairs | oracles agree | validator | generated valid | model calls | tokens | seconds |
|---|--:|--:|---|---|---|--:|--:|--:|
| distinct-stretch | 2 | 0 | 38 of 38 compared | 4 of 4 rejected | 75 of 75 | 5 | 3781 | 66 |
| fewest-hops | 2 | 1 | 62 of 62 compared | 6 of 6 rejected | 75 of 75 | 6 | 7173 | 97 |
| max-subarray | 2 | 0 | 22 of 22 compared | 6 of 6 rejected | 75 of 75 | 5 | 3757 | 56 |
| pick-talks | 2 | 0 | 63 of 63 compared | 6 of 6 rejected | 75 of 75 | 5 | 4321 | 108 |
| ship-the-boxes | 2 | 0 | 60 of 60 compared | 6 of 6 rejected | 75 of 75 | 5 | 4540 | 84 |
| split-the-load | 2 | 1 | 61 of 61 compared | 6 of 6 rejected | 75 of 75 | 6 | 5534 | 92 |

## Totals

- Found 17 of 18.
- Median tests to the first confirmed failure: 64.
- Median smallest input: 3.0 tokens.
- Median seconds per bug found: 23.
- Verdicts: TLE 1, WA 16.
- By kind: bad initialization 3 of 3, missed edge case 2 of 2, non-termination 1 of 1, off-by-one 7 of 7, wrong algorithm 2 of 2, wrong greedy 2 of 3.
- Model calls in total: 32.

## Misses

Each of these is a bug that is really there, which bench/verify.py shows, and that this run did not reach.

- **pick-talks / by length** (wrong greedy): 300 tests on inputs of up to 25 tokens all matched both reference solutions, so the budget ran out before a difference turned up.
