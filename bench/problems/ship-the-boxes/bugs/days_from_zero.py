# kind: off-by-one
# Counts the first day as day zero, so every capacity looks like it needs one
# day fewer than it does.
import sys


def days_needed(w, cap):
    days = 0
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
    high = sum(w)
    while low < high:
        mid = (low + high) // 2
        if days_needed(w, mid) <= d:
            high = mid
        else:
            low = mid + 1
    print(low)


main()
