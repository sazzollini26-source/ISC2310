#!/usr/bin/env python3
"""Launcher: `python run.py` opens the terminal, `python run.py --help` lists commands."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mkt.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
