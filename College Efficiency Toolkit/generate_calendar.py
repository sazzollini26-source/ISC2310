"""
Reads WEEKLY_BLOCKS from schedule_config.py and writes my_schedule.ics.

Setup:
  1. No installs needed (uses only the Python standard library).
  2. Run:  python generate_calendar.py
  3. Import the created my_schedule.ics into Google/Apple Calendar.

Events repeat weekly. A block whose end time is earlier than its start
(e.g. 22:00 -> 01:00) is treated as ending the next day.
"""

from datetime import datetime, timedelta
import schedule_config

# Anchor: the Monday of the week you want the schedule to start from.
ANCHOR_MONDAY = datetime(2026, 9, 14)  # change to your term-start Monday

DAY_OFFSET = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def to_ics_dt(dt):
    return dt.strftime("%Y%m%dT%H%M%S")


def build_event(block, uid):
    day_start = ANCHOR_MONDAY + timedelta(days=DAY_OFFSET[block["day"]])
    sh, sm = map(int, block["start"].split(":"))
    eh, em = map(int, block["end"].split(":"))
    start = day_start.replace(hour=sh, minute=sm)
    end = day_start.replace(hour=eh, minute=em)
    if end <= start:                 # crosses midnight
        end += timedelta(days=1)

    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}@isc2310-college-efficiency",
        f"DTSTAMP:{to_ics_dt(datetime.now())}",
        f"DTSTART:{to_ics_dt(start)}",
        f"DTEND:{to_ics_dt(end)}",
        f"SUMMARY:{block['title']}",
        f"CATEGORIES:{block['category']}",
        f"RRULE:FREQ=WEEKLY;BYDAY={block['day']}",
    ]
    if block.get("reminder_min"):
        lines += [
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"TRIGGER:-PT{block['reminder_min']}M",
            f"DESCRIPTION:{block['title']}",
            "END:VALARM",
        ]
    lines.append("END:VEVENT")
    return "\n".join(lines)


def main():
    cal = ["BEGIN:VCALENDAR", "VERSION:2.0",
           "PRODID:-//ISC2310//College Efficiency Toolkit//EN"]
    for i, block in enumerate(schedule_config.WEEKLY_BLOCKS):
        cal.append(build_event(block, uid=f"block-{i}"))
    cal.append("END:VCALENDAR")

    with open("my_schedule.ics", "w") as f:
        f.write("\n".join(cal) + "\n")
    print(f"Wrote my_schedule.ics with {len(schedule_config.WEEKLY_BLOCKS)} recurring blocks.")
    print("Import it into Google Calendar (Settings -> Import & export)")
    print("or Apple Calendar (File -> Import).")


if __name__ == "__main__":
    main()
