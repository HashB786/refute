# Shipping the boxes

There are n boxes on a quay, in a fixed order that cannot be changed. Box i
weighs wi. A ship calls once a day for d days. Each day the ship takes the
boxes from the front of the queue, in order, for as long as their total weight
stays within its capacity, and it must take at least one box a day.

Every box has to be gone after d days. Print the smallest capacity that
manages it.

## Input

The first line holds two integers n and d.

The second line holds n integers w1 w2 ... wn.

## Constraints

- 1 <= n <= 20
- 1 <= d <= n
- 1 <= wi <= 50

## Output

One line with one integer: the smallest capacity that clears the quay in d
days.

## Examples

Input:

```
3 3
1 2 3
```

Output:

```
3
```

Input:

```
4 4
5 1 1 1
```

Output:

```
5
```

In both examples there are as many days as boxes, so one box a day is enough
and the capacity only has to cover the heaviest box.
