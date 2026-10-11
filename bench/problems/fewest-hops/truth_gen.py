import random
import sys

rng = random.Random(int(sys.argv[1]))
n = rng.randint(2, 7)
pairs = [(a, b) for a in range(1, n + 1) for b in range(a + 1, n + 1)]
rng.shuffle(pairs)
m = rng.randint(0, len(pairs))
doors = pairs[:m]
print(n, m)
for a, b in doors:
    print(a, b)
