import sys


def main():
    data = sys.stdin.read().split()
    n = int(data[0])
    talks = []
    for i in range(n):
        start = int(data[1 + 2 * i])
        end = int(data[2 + 2 * i])
        talks.append((start, end))

    best = 0
    for mask in range(1 << n):
        chosen = [talks[i] for i in range(n) if mask >> i & 1]
        chosen.sort()
        ok = True
        for a, b in zip(chosen, chosen[1:]):
            if b[0] < a[1]:
                ok = False
                break
        if ok and len(chosen) > best:
            best = len(chosen)
    print(best)


main()
