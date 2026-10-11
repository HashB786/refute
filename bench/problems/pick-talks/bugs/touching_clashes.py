# kind: off-by-one
# Treats a talk that starts exactly when another ends as a clash.
import sys


def main():
    data = sys.stdin.read().split()
    n = int(data[0])
    talks = []
    for i in range(n):
        talks.append((int(data[1 + 2 * i]), int(data[2 + 2 * i])))
    talks.sort(key=lambda talk: talk[1])
    taken = 0
    free_at = -1
    for start, end in talks:
        if start > free_at:
            taken += 1
            free_at = end
    print(taken)


main()
