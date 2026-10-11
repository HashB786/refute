# kind: bad initialization
# The best difference starts at zero, which no split can ever beat.
import sys


def main():
    data = sys.stdin.read().split()
    n = int(data[0])
    w = [int(x) for x in data[1 : 1 + n]]
    total = sum(w)
    best = 0
    for mask in range(1 << n):
        side = 0
        for i in range(n):
            if mask >> i & 1:
                side += w[i]
        gap = abs(total - 2 * side)
        if gap < best:
            best = gap
    print(best)


main()
