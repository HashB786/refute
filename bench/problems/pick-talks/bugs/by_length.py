# kind: wrong greedy
# Takes the shortest talk first, which can block two longer ones.
import sys


def main():
    data = sys.stdin.read().split()
    n = int(data[0])
    talks = []
    for i in range(n):
        talks.append((int(data[1 + 2 * i]), int(data[2 + 2 * i])))
    talks.sort(key=lambda talk: talk[1] - talk[0])
    taken = []
    for start, end in talks:
        clashes = False
        for other_start, other_end in taken:
            if start < other_end and other_start < end:
                clashes = True
                break
        if not clashes:
            taken.append((start, end))
    print(len(taken))


main()
