# kind: wrong algorithm
# Takes the room it saw last instead of the room it saw first, so it reports
# the length of the first route it stumbles on rather than the shortest.
import sys


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

    seen = [False] * (n + 1)
    waiting = [(1, 0)]
    seen[1] = True
    while waiting:
        room, steps = waiting.pop()
        if room == n:
            print(steps)
            return
        for nextroom in doors[room]:
            if not seen[nextroom]:
                seen[nextroom] = True
                waiting.append((nextroom, steps + 1))
    print(-1)


main()
