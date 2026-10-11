# kind: missed edge case
# Insists that both lorries carry something. With one crate there is no such
# split, and it falls back to zero instead of the weight of that crate.
import sys


def main():
    data = sys.stdin.read().split()
    n = int(data[0])
    w = [int(x) for x in data[1 : 1 + n]]
    total = sum(w)
    best = None
    for mask in range(1, (1 << n) - 1):
        side = 0
        for i in range(n):
            if mask >> i & 1:
                side += w[i]
        gap = abs(total - 2 * side)
        if best is None or gap < best:
            best = gap
    print(best if best is not None else 0)


main()
