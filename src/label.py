"""Record what the room's occupancy actually was, so the model can be scored.

The estimate has never been measured against truth. It is checked against
periods whose occupancy is known by argument -- nobody is in an office at 4am --
which shows the model is not nonsense but cannot produce a precision, a recall,
or an answer to "does it tell one person from two". Only labels can, and labels
only exist if someone writes them down at the time. They cannot be recovered
afterwards, which is why this is a ten-second command rather than a form.

    label 2                     two people, from now
    label 0                     empty, from now
    label 1 --door-open         one person, door open
    label 2 --at 09:30          two people, from 09:30 today
    label --list                the last few entries
    label --undo                remove the last entry

Entries are state changes, not intervals: each says what became true and when.
Intervals are reconstructed at read time, so a forgotten "back to zero" costs
one interval rather than corrupting the file.

Labels live with the data, outside the repository -- they describe when the
home was empty as directly as the readings do.
"""
import argparse
import json
import sys
from datetime import datetime, timezone

import pandas as pd

import paths

STORE = paths.DATA_DIR / "labels.jsonl"
FLAGS = ("door_open", "window_open", "ac_on", "purifier_off")


def _now() -> datetime:
    return datetime.now().astimezone()


def record(occupants: int, at: datetime = None, note: str = None, **flags) -> dict:
    entry = {"ts": (at or _now()).isoformat(), "occupants": occupants}
    entry.update({k: v for k, v in flags.items() if v})
    if note:
        entry["note"] = note
    STORE.parent.mkdir(parents=True, exist_ok=True)
    with STORE.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")
    return entry


def load() -> pd.DataFrame:
    """Every recorded state change, oldest first."""
    rows = ([json.loads(line) for line in STORE.read_text().splitlines() if line.strip()]
            if STORE.exists() else [])
    if not rows:
        # The file exists but is empty after an --undo of the only entry.
        empty = pd.DataFrame({"ts": pd.Series(dtype="datetime64[ns, UTC]"),
                              "occupants": pd.Series(dtype="int64")})
        return empty.set_index("ts")
    frame = pd.DataFrame(rows)
    # ISO8601 rather than inferred: --at writes whole seconds and a bare
    # `label 2` writes microseconds, so inferring from the first row fails on
    # any file that mixes them.
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True, format="ISO8601")
    return frame.set_index("ts").sort_index()


def as_series(index: pd.DatetimeIndex, max_hours: float = 12.0) -> pd.Series:
    """Truth aligned to a reading index, for scoring the estimate against.

    A label holds until the next one, but not indefinitely: if nobody recorded
    a change for `max_hours`, the truth is unknown rather than unchanged, and
    those samples are left NaN so they are excluded from any score instead of
    silently inventing an occupancy.
    """
    labels = load()
    if labels.empty:
        return pd.Series(float("nan"), index=index)

    idx = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
    positions = labels.index.searchsorted(idx, side="right") - 1
    values = pd.Series(float("nan"), index=index)
    valid = positions >= 0
    values[valid] = labels["occupants"].to_numpy()[positions[valid]]

    age = pd.Series(float("inf"), index=index)
    age[valid] = (idx[valid] - labels.index[positions[valid]]).total_seconds() / 3600
    return values.where(age <= max_hours)


def main() -> int:
    p = argparse.ArgumentParser(prog="label", description=__doc__.split("\n")[0])
    p.add_argument("occupants", nargs="?", type=int, help="how many people are in the room now")
    p.add_argument("--at", metavar="HH:MM", help="time today, if recording it late")
    p.add_argument("--note")
    for flag in FLAGS:
        p.add_argument(f"--{flag.replace('_', '-')}", action="store_true")
    p.add_argument("--list", action="store_true", help="show the last entries")
    p.add_argument("--undo", action="store_true", help="drop the last entry")
    args = p.parse_args()

    if args.list:
        recent = load().tail(15)
        print(f"no labels yet at {STORE}" if recent.empty
              else recent.tz_convert(_now().tzinfo).to_string())
        return 0

    if args.undo:
        lines = STORE.read_text().splitlines() if STORE.exists() else []
        if not lines:
            print("nothing to undo")
            return 1
        STORE.write_text("\n".join(lines[:-1]) + ("\n" if lines[:-1] else ""))
        print(f"removed: {lines[-1]}")
        return 0

    if args.occupants is None:
        p.print_help()
        return 1

    at = None
    if args.at:
        hour, minute = (int(x) for x in args.at.split(":"))
        at = _now().replace(hour=hour, minute=minute, second=0, microsecond=0)

    entry = record(args.occupants, at=at, note=args.note,
                   **{f: getattr(args, f) for f in FLAGS})
    when = pd.Timestamp(entry["ts"]).strftime("%H:%M")
    extra = " ".join(f"+{f}" for f in FLAGS if entry.get(f))
    print(f"{when}  {entry['occupants']} occupant(s) {extra}".rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
