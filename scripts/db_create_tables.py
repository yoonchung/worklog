import os
import sys
from pathlib import Path
from sqlalchemy import create_engine

# Ensure project root is on sys.path so `app` can be imported when running
# this script directly (e.g. `python scripts/db_create_tables.py`).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import Base, User, Repository, PullRequest, Summary

# Defaults to a local Postgres DB; override with DATABASE_URL env var
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/worklog_db",
)

engine = create_engine(DATABASE_URL, echo=True, future=True)

if __name__ == "__main__":
    # This will create tables for all models imported in `models.Base`
    Base.metadata.create_all(engine)
    print("Tables created/checked successfully.")
