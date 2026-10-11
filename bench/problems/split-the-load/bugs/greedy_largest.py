# kind: wrong greedy
# Puts each crate, heaviest first, on whichever lorry is lighter.
import sys


def main():
    data = sys.stdin.read().split()
    n = int(data[0])
    w = sorted((int(x) for x in data[1 : 1 + n]), reverse=True)
    left = 0
    right = 0
    for weight in w:
        if left <= right:
            left += weight
        else:
            right += weight
    print(abs(left - right))


main()
