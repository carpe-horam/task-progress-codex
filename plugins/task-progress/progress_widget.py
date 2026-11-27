#!/usr/bin/env python3
"""Command entry for the portable open-progress-widget Skill."""
from pathlib import Path
import runpy

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("skills") / "open-progress-widget/scripts/open_progress.py"), run_name="__main__")
