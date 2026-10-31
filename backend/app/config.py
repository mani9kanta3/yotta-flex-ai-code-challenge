"""
Every setting the project needs, read once from backend/.env.

The dates are fixed here rather than read from the clock. Results have to be
the same on every run, so "now" can never be the real time.
"""

import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")

DB_NAME = os.getenv("DB_NAME", "inventory")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "inventory_dev")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5434"))

DB_DIR = BACKEND_DIR / "db"
DATA_DIR = BACKEND_DIR / "data"

DEFAULT_SEED = 20261022

# The simulated period. The ID formats carry the year 2026, so it stays inside 2026.
START_AT = datetime(2026, 1, 1, 0, 0, 0)
AS_OF = datetime(2026, 9, 30, 23, 59, 59)
