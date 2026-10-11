import random
import sys

rng = random.Random(int(sys.argv[1]))
size = rng.randint(1, 12)
alphabet = "abcdefghijklmnopqrstuvwxyz"[: rng.choice([1, 2, 3, 6, 26])]
print("".join(rng.choice(alphabet) for _ in range(size)))
