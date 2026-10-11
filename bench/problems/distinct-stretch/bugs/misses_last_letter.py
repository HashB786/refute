# kind: off-by-one
# The inner loop stops one letter short, so no stretch can end at the last
# letter of the word.
import sys


def main():
    s = sys.stdin.read().strip()
    n = len(s)
    best = 0
    for i in range(n):
        seen = set()
        for j in range(i, n - 1):
            if s[j] in seen:
                break
            seen.add(s[j])
            if len(seen) > best:
                best = len(seen)
    print(best)


main()
