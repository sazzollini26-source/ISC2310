"""
Sort a messy folder (e.g. Downloads) into subfolders by file type.

Setup:
  1. No installs needed.
  2. Edit SOURCE_DIR below to your Downloads path.
  3. Run:  python downloads_organizer.py
     (Moves files into Documents/, Images/, Spreadsheets/, Code/, Other/.)
"""

import os
import shutil

SOURCE_DIR = os.path.expanduser("~/Downloads")   # change if needed

CATEGORIES = {
    "Documents":    [".pdf", ".docx", ".doc", ".txt", ".pptx"],
    "Images":       [".png", ".jpg", ".jpeg", ".gif", ".heic"],
    "Spreadsheets": [".csv", ".xlsx", ".xls"],
    "Code":         [".py", ".ipynb", ".js", ".java", ".r"],
}


def category_for(ext):
    for name, exts in CATEGORIES.items():
        if ext.lower() in exts:
            return name
    return "Other"


def main():
    if not os.path.isdir(SOURCE_DIR):
        print(f"Folder not found: {SOURCE_DIR} — edit SOURCE_DIR and retry.")
        return
    moved = 0
    for entry in os.listdir(SOURCE_DIR):
        path = os.path.join(SOURCE_DIR, entry)
        if not os.path.isfile(path):
            continue
        ext = os.path.splitext(entry)[1]
        dest_dir = os.path.join(SOURCE_DIR, category_for(ext))
        os.makedirs(dest_dir, exist_ok=True)
        shutil.move(path, os.path.join(dest_dir, entry))
        moved += 1
    print(f"Organized {moved} files into subfolders inside {SOURCE_DIR}.")


if __name__ == "__main__":
    main()
