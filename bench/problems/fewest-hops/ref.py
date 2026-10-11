import sys


def main():
    data = sys.stdin.read().split()
    n = int(data[0])
    m = int(data[1])
    far = 10 ** 6
    best = [[far] * (n + 1) for _ in range(n + 1)]
    for room in range(1, n + 1):
        best[room][room] = 0
    for i in range(m):
        a = int(data[2 + 2 * i])
        b = int(data[3 + 2 * i])
        best[a][b] = 1
        best[b][a] = 1
    for through in range(1, n + 1):
        for start in range(1, n + 1):
            for end in range(1, n + 1):
                step = best[start][through] + best[through][end]
                if step < best[start][end]:
                    best[start][end] = step
    answer = best[1][n]
    print(answer if answer < far else -1)


main()
