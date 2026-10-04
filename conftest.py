"""
Put the project root on sys.path so `from app... import ...` works in tests
without the package having to be pip-installed.

A root conftest.py is the reliable way to do this. Relying on pytest's rootdir
inference works until somebody runs `pytest tests/test_one_thing.py` from a
different directory, and then the security suite is skipped rather than failing
— which is the worst possible outcome for a suite whose job is to fail.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
