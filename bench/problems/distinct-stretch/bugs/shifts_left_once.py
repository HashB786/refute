# kind: off-by-one
# On a repeat it moves the start of the stretch one letter along instead of
# past the earlier copy, so the stretch it measures can still hold a repeat.
import sys


def main():
    s = sys.stdin.read().strip()
    seen = set()
    left = 0
    best = 0
    for right in range(len(s)):
        if s[right] in seen:
            seen.discard(s[left])
            left += 1
        seen.add(s[right])
        if right - left + 1 > best:
            best = right - left + 1
    print(best)


main()
