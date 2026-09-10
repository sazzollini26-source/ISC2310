"""
Weekly schedule for a college senior.
Edit the WEEKLY_BLOCKS list, then run generate_calendar.py to produce
an .ics file you can import into Google Calendar or Apple Calendar.

Each block:
  title        -> event name
  day          -> MO, TU, WE, TH, FR, SA, SU
  start        -> "HH:MM" 24-hour
  end          -> "HH:MM" 24-hour (if earlier than start, event ends next day)
  category     -> free-text label (e.g. "Study", "Referee", "Class")
  reminder_min -> minutes before start to alert (0 = no alert)
"""

WEEKLY_BLOCKS = [
    # --- Study blocks ---
    # Monday deep-work study block, 10 PM to 1 AM (ends early Tuesday).
    {"title": "Deep Study Block", "day": "MO", "start": "22:00", "end": "01:00",
     "category": "Study", "reminder_min": 15},

    # --- Refereeing (adjust to your real assignments) ---
    {"title": "Submit weekly referee availability", "day": "SU", "start": "18:00",
     "end": "18:15", "category": "Referee", "reminder_min": 30},
    {"title": "Pre-game gear check (whistle, cards, watch, uniform, ID badge)",
     "day": "SA", "start": "08:00", "end": "08:15", "category": "Referee",
     "reminder_min": 60},
    {"title": "Log referee games worked + pay/mileage", "day": "SU",
     "start": "19:00", "end": "19:20", "category": "Referee", "reminder_min": 0},

    # --- Add your class times, capstone, and job-application blocks here ---
    # {"title": "ISC2310 Lecture", "day": "TU", "start": "09:30", "end": "10:45",
    #  "category": "Class", "reminder_min": 10},
]
