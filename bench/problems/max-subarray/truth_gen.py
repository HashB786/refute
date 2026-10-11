import random
import sys

rng = random.Random(int(sys.argv[1]))
n = rng.randint(1, 8)
low, high = rng.choice([(-5, 5), (-100, -1), (0, 9), (-20, 20)])
values = [rng.randint(low, high) for _ in range(n)]
print(n)
print(" ".join(str(value) for value in values))
