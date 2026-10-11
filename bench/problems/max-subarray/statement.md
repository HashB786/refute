# Largest block sum

A row of n integers is given. Choose one block of neighbouring numbers, at
least one number long, so that the numbers in the block add up to as much as
possible. Print that sum.

## Input

The first line holds one integer n.

The second line holds n integers a1 a2 ... an.

## Constraints

- 1 <= n <= 30
- -100 <= ai <= 100

## Output

One line with one integer: the largest sum any block of neighbouring numbers
reaches. A block cannot be empty, so the answer is negative when every number
is negative.

## Examples

Input:

```
5
-2 1 -3 4 -1
```

Output:

```
4
```

Input:

```
4
-1 2 3 -1
```

Output:

```
5
```

The first example takes the 4 on its own. The second takes the 2 and the 3
together.
