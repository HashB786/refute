# kind: off-by-one
# Reads one door too few, so the last door in the input is never there.
import sys
from collections import deque


def main():
    data = sys.stdin.read().split()
    n = int(data[0])
    m = int(data[1])
    doors = [[] for _ in range(n + 1)]
    for i in range(m - 1):
        a = int(data[2 + 2 * i])
        b = int(data[3 + 2 * i])
        doors[a].append(b)
        doors[b].append(a)

    steps = [-1] * (n + 1)
    steps[1] = 0
    waiting = deque([1])
    while waiting:
        room = waiting.popleft()
        for nextroom in doors[room]:
            if steps[nextroom] < 0:
                steps[nextroom] = steps[room] + 1
                waiting.append(nextroom)
    print(steps[n])


main()
