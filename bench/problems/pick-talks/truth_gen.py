import random
import sys

rng = random.Random(int(sys.argv[1]))
n = rng.randint(1, 6)
top = rng.choice([4, 8, 20])
talks = []
for _ in range(n):
    start = rng.randint(0, top - 1)
    end = rng.randint(start + 1, top)
    talks.append((start, end))
print(n)
for start, end in talks:
    print(start, end)
