# Fewest doors

A building has n rooms, numbered 1 to n, and m doors. Each door joins two
different rooms and can be used in either direction. No two doors join the
same pair of rooms.

Print the smallest number of doors you have to go through to get from room 1
to room n, or -1 when there is no way through.

## Input

The first line holds two integers n and m.

Each of the next m lines holds two integers a and b, a door between room a
and room b.

## Constraints

- 2 <= n <= 12
- 0 <= m <= 20
- 1 <= a < b <= n
- no pair of rooms appears twice

## Output

One line with one integer: the smallest number of doors from room 1 to room
n, or -1 when room n cannot be reached.

## Examples

Input:

```
4 4
1 4
1 2
2 3
3 4
```

Output:

```
1
```

Input:

```
5 5
1 3
3 4
4 5
1 2
2 3
```

Output:

```
3
```

The first example has a door straight from room 1 to room 4. The second goes
through rooms 3 and 4 on the way to room 5.
