# Pay the amount by always taking the largest coin that still fits.
# This is the solution most people write first, and it is wrong.
import sys


def main() -> None:
    data = sys.stdin.read().split()
    n = int(data[0])
    amount = int(data[1])
    coins = [int(token) for token in data[2 : 2 + n]]

    used = 0
    for coin in sorted(coins, reverse=True):
        used += amount // coin
        amount %= coin
    print(used)


main()
