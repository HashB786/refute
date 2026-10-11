# kind: off-by-one
# Blocks that start at the first number and run on are never tried.
import sys


def main():
    data = sys.stdin.read().split()
    n = int(data[0])
    a = [int(x) for x in data[1 : 1 + n]]
    best = a[0]
    for i in range(1, n):
        total = 0
        for j in range(i, n):
            total += a[j]
            if total > best:
                best = total
    print(best)


main()
