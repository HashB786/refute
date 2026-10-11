# kind: missed edge case
# Every room starts at distance zero, so a room that is never reached looks
# like it is no doors away.
import sys
from collections import deque


def main():
    data = sys.stdin.read().split()
    n = int(data[0])
    m = int(data[1])
    doors = [[] for _ in range(n + 1)]
    for i in range(m):
        a = int(data[2 + 2 * i])
        b = int(data[3 + 2 * i])
        doors[a].append(b)
        doors[b].append(a)

    steps = [0] * (n + 1)
    seen = [False] * (n + 1)
    seen[1] = True
    waiting = deque([1])
    while waiting:
        room = waiting.popleft()
        for nextroom in doors[room]:
            if not seen[nextroom]:
                seen[nextroom] = True
                steps[nextroom] = steps[room] + 1
                waiting.append(nextroom)
    print(steps[n])


main()
