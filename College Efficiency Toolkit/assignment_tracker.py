"""
Show upcoming assignment/exam deadlines from assignments.csv, sorted by
how soon they are due.

Setup:
  1. No installs needed.
  2. Add your deadlines to assignments.csv (one row each).
  3. Run:  python assignment_tracker.py
"""

import csv
from datetime import datetime, date

DATA_FILE = "assignments.csv"


def main():
    with open(DATA_FILE, newline="") as f:
        rows = list(csv.DictReader(f))

    today = date.today()
    upcoming = []
    for r in rows:
        due = datetime.strptime(r["due_date"], "%Y-%m-%d").date()
        upcoming.append(((due - today).days, due, r))
    upcoming.sort()

    print("Upcoming deadlines")
    print("-" * 55)
    for days_left, due, r in upcoming:
        if days_left < 0:
            tag = "PAST DUE"
        elif days_left == 0:
            tag = "DUE TODAY"
        elif days_left <= 3:
            tag = "SOON"
        else:
            tag = f"{days_left}d"
        print(f"[{tag:>9}] {due}  {r['course']:<10} {r['task']}")


if __name__ == "__main__":
    main()
