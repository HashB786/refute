# kind: bad initialization
# Starts the best sum at zero, so it can never answer with a negative number.
import sys


def main():
    data = sys.stdin.read().split()
    n = int(data[0])
    a = [int(x) for x in data[1 : 1 + n]]
    best = 0
    total = 0
    for value in a:
        total += value
        if total < 0:
            total = 0
        if total > best:
            best = total
    print(best)


main()
