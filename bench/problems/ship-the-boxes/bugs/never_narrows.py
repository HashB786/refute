# kind: non-termination
# The lower end of the search is moved to the middle instead of past it, so
# once the two ends sit next to each other the search stops moving.
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
    high = sum(w)
    while low < high:
        mid = (low + high) // 2
        if days_needed(w, mid) <= d:
            high = mid
        else:
            low = mid
    print(low)


main()
