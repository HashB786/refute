# Refute

## What it does

Refute finds the smallest input that proves a program wrong. Point it at a
statement and a solution you doubt; it returns one small failing input, what
your program printed, the right answer, and how to repeat it.

## How we built it

A language model writes four throwaway scripts per problem: two reference
solutions by different methods, a validator and a seeded generator. None is
trusted because the model wrote it; each earns its place by running.
The references must reproduce every example in the statement. The validator
must accept every example and reject broken copies of them: one token short,
one too many, one number out of range. The generator must produce valid input
across fifteen size and mode combinations, repeat for a seed, and differ
between seeds. A script that fails goes back with the exact failure.

Then it stress tests, smallest inputs first, and shrinks what fails: binary
search on each number, deleting lines and tokens, and lowering a count while
dropping the item it counted, so inputs shaped "n, then n numbers" shrink at
all. A candidate is kept only if the validator still accepts it, both
references agree, and the failure is the same kind.

## Accomplishments

On six classic problems with eighteen planted bugs, Refute found 17 of 18.
The median run reached its first confirmed failure in 64 tests and shrank the
input to 3 tokens. The two references agreed on all 306 inputs where both ran,
every validator rejected every broken example, and all 450 generated inputs
were valid. Tooling is written once per problem, so eighteen bugs cost 32
model calls.

## What we learned

The model is wrong often enough that checking it is the whole product. Its
first generator worked the input out from the size and ignored the seed, which
would have made every test in a pass identical. A smoke test caught that.

## What's next

The benchmark marks its own homework: the bugs were written by the agent that
built Refute, not collected from real submissions. The one miss, a wrong
greedy on interval scheduling, needs a generator aimed at the shapes that
break it rather than uniform sampling.
