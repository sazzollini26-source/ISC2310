"""
Track referee games, pay, and mileage from referee_data.csv.

Setup:
  1. No installs needed (standard library only).
  2. Open referee_data.csv and add one row per game you work.
  3. Run:  python referee_tracker.py

Prints season totals (games, pay, mileage) and warns if your
certification expires within 60 days.
"""

import csv
from datetime import datetime, date

DATA_FILE = "referee_data.csv"

# Update these when you recertify:
CERT_NAME = "USSF Grassroots Referee"   # e.g. USSF / NFHS / IAABO / your org
CERT_EXPIRES = "2027-05-31"             # YYYY-MM-DD


def load_rows():
    with open(DATA_FILE, newline="") as f:
        return list(csv.DictReader(f))


def main():
    rows = load_rows()
    total_pay = sum(float(r["pay"] or 0) for r in rows)
    total_miles = sum(float(r["mileage"] or 0) for r in rows)

    print(f"Referee season summary ({len(rows)} games logged)")
    print(f"  Total pay:     ${total_pay:,.2f}")
    print(f"  Total mileage: {total_miles:,.1f} mi")

    expires = datetime.strptime(CERT_EXPIRES, "%Y-%m-%d").date()
    days_left = (expires - date.today()).days
    print(f"\nCertification: {CERT_NAME} expires {CERT_EXPIRES} ({days_left} days left).")
    if days_left <= 60:
        print("  ** RENEW SOON ** register for the recertification clinic.")


if __name__ == "__main__":
    main()
