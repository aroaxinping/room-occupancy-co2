"""Where the data lives -- deliberately outside the repository.

An occupancy series records when a home is empty, so it must never be publish-
able by accident. A .gitignore entry is a rule that someone can edit, override
with `git add -f`, or lose when files are moved around. Keeping the data outside
the repository tree instead makes committing it impossible rather than merely
discouraged.

Override with ROOM_OCCUPANCY_DATA to point somewhere else.
"""
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get(
    "ROOM_OCCUPANCY_DATA",
    Path.home() / "Library" / "Application Support" / "room-occupancy-co2",
))

RAW = DATA_DIR / "raw"
PROCESSED = DATA_DIR / "processed"


def processed(name: str) -> Path:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    return PROCESSED / name
