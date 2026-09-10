# College Efficiency Toolkit

A small set of beginner-friendly Python scripts (standard library only — no
`pip install` needed) to automate the recurring admin work of a college senior:
calendar scheduling, refereeing, deadlines, and file cleanup.

## What's inside

| File | What it does |
|------|--------------|
| `schedule_config.py` | Your weekly recurring blocks (edit this). Includes the **Monday 10 PM–1 AM study block**. |
| `generate_calendar.py` | Turns the config into `my_schedule.ics` to import into Google/Apple Calendar. |
| `referee_tracker.py` | Season totals for games, pay, and mileage + certification-expiry warning. |
| `referee_data.csv` | One row per game you referee (template with an example row). |
| `assignment_tracker.py` | Lists upcoming deadlines sorted by how soon they're due. |
| `assignments.csv` | Your assignment/exam deadlines (template). |
| `downloads_organizer.py` | Sorts a messy Downloads folder into subfolders by file type. |

## Setup (one time)
1. Install Python 3.9+ (you likely already have it for ISC2310).
2. Open a terminal in this folder: `cd "College Efficiency Toolkit"`.
3. Run any script with `python <script>.py`.

## Placeholders you should fill in
These use my best-guess defaults — replace them with your real info:
- **Term start date** in `generate_calendar.py` (`ANCHOR_MONDAY`).
- **Class times** and other blocks in `schedule_config.py`.
- **Referee league/sport, certification name + expiry** in `referee_tracker.py`,
  and your real games in `referee_data.csv`.
- **Your deadlines** in `assignments.csv`.
- **Downloads path** in `downloads_organizer.py`.
