# kind: wrong algorithm
# On a repeat it throws the whole stretch away and begins again at the letter
# it just read, losing the letters in between that were still usable.
import sys


def main():
    s = sys.stdin.read().strip()
    seen = set()
    best = 0
    for letter in s:
        if letter in seen:
            seen = {letter}
        else:
            seen.add(letter)
        if len(seen) > best:
            best = len(seen)
    print(best)


main()
