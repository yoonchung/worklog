import os
import sys
from pathlib import Path
from sqlalchemy import create_engine

# Ensure project root is on sys.path so `app` can be imported when running
# this script directly (e.g. `python scripts/db_drop_tables.py`).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import Base

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://$(whoami)@localhost:5432/worklog_db",
)

engine = create_engine(DATABASE_URL, echo=True, future=True)

if __name__ == "__main__":
    Base.metadata.drop_all(engine)
    print("All tables dropped.")