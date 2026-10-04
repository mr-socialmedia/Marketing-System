#!/usr/bin/env python3
"""
Entry point.

A thin shim so the agent runs as `python run.py ...` without the package having
to be installed. Everything real is in app/main.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
