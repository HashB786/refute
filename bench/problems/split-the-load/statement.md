# Splitting the load

There are n crates to put on two lorries. Crate i weighs wi. Every crate goes
on one of the two lorries, and a lorry is allowed to leave empty.

Make the two loads as close as you can and print the difference between them.

## Input

The first line holds one integer n.

The second line holds n integers w1 w2 ... wn.

## Constraints

- 1 <= n <= 12
- 1 <= wi <= 50

## Output

One line with one integer: the smallest difference between the two loads that
any split reaches.

## Examples

Input:

```
4
1 2 3 4
```

Output:

```
0
```

Input:

```
2
2 2
```

Output:

```
0
```

In the first example 1 and 4 go on one lorry and 2 and 3 on the other. In the
second one crate goes on each.
