#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "requests==2.34.2",
#   "matplotlib==3.11.2",
# ]
# ///
"""wiki-audience CLI entry point.

Run with `uv run scripts/wiki_audience.py <cmd> ...` (preferred) or with a
venv that has `requests` and `matplotlib` installed (see requirements.txt).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wa.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
