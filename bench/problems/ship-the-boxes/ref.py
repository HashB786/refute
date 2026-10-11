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
    for cap in range(max(w), sum(w) + 1):
        if days_needed(w, cap) <= d:
            print(cap)
            return


main()
