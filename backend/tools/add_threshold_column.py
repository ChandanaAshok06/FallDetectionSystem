"""Adds users.personalized_threshold to an existing database (safe to run more than once).

Run from the backend folder:  python tools\add_threshold_column.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text
from app.db.session import engine

insp = inspect(engine)
if "users" not in insp.get_table_names():
    print("No 'users' table found in this database; nothing to do.")
elif "personalized_threshold" in [c["name"] for c in insp.get_columns("users")]:
    print("Column personalized_threshold already exists; nothing to do.")
else:
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN personalized_threshold FLOAT"))
    print("Added column users.personalized_threshold.")