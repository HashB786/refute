# kind: off-by-one
# The inner loop stops one short, so no block can end at the last number.
import sys


def main():
    data = sys.stdin.read().split()
    n = int(data[0])
    a = [int(x) for x in data[1 : 1 + n]]
    best = a[0]
    for i in range(n):
        total = 0
        for j in range(i, n - 1):
            total += a[j]
            if total > best:
                best = total
    print(best)


main()
