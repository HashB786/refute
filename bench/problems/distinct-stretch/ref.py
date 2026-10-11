import sys


def main():
    s = sys.stdin.read().strip()
    n = len(s)
    best = 0
    for i in range(n):
        seen = set()
        for j in range(i, n):
            if s[j] in seen:
                break
            seen.add(s[j])
            if len(seen) > best:
                best = len(seen)
    print(best)


main()
