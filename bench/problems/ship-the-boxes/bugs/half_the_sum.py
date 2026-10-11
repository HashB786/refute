# kind: bad initialization
# Starts the upper end of the search at half the total weight, which is below
# the answer whenever few days are allowed.
import sys


def days_needed(w, cap):
    days = 1
    load = 0
    for weight in w:
        if load + weight <= cap:
            load += weight
        else:
            days += 1
            load = weight
    return days


def main():
    data = sys.stdin.read().split()
    n = int(data[0])
    d = int(data[1])
    w = [int(x) for x in data[2 : 2 + n]]
    low = max(w)
    high = sum(w) // 2
    while low < high:
        mid = (low + high) // 2
        if days_needed(w, mid) <= d:
            high = mid
        else:
            low = mid + 1
    print(low)


main()
