import random
import sys

rng = random.Random(int(sys.argv[1]))
n = rng.randint(1, 6)
d = rng.randint(1, n)
top = rng.choice([3, 9, 50])
print(n, d)
print(" ".join(str(rng.randint(1, top)) for _ in range(n)))
