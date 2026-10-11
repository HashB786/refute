# The longest stretch of different letters

A word of lowercase letters is given. Find the longest stretch of neighbouring
letters inside it in which no letter appears twice, and print how long that
stretch is.

## Input

One line holding the word s.

## Constraints

- 1 <= length of s <= 30
- s holds lowercase letters a to z only

## Output

One line with one integer: the length of the longest stretch of neighbouring
letters with no letter repeated inside it.

## Examples

Input:

```
abcabcbb
```

Output:

```
3
```

Input:

```
bbbbb
```

Output:

```
1
```

In the first example abc is as long as such a stretch gets. In the second
every stretch longer than one letter repeats the b.
