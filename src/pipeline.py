"""Scheduled ingestion: poll, store, and say so when it stops working.

The vendor API returns present state only, so history exists only if something
collects it. Two failure modes matter more than throughput here, and the
original five months of data demonstrate both: the record already contains two
outages of about 26 days each, which is what a scheduled job failing silently
looks like after the fact.

So this writes append-only partitioned files that a re-run cannot corrupt, and
reports staleness rather than waiting to be asked.

`coverage()` scores each day against this poll's five-minute cadence, which is
the right question for "is the collector working" and the wrong one for "is
this day complete". The vendor's stored history is one-minute, so a day at 100%
here is still missing four fifths of the rows the cloud holds. src/backfill.py
therefore judges completeness from `partition_rows()` and its own ledger, and
uses `coverage()` only to find days the poll itself under-covered.

    python3 src/pipeline.py <deviceId>          one poll
    python3 src/pipeline.py <deviceId> --status coverage report, no network
"""
import json
import sys
import time
import urllib.error
from datetime import datetime, timedelta, timezone

import pandas as pd

import paths
from ingest import read_sensor

# The sensor refreshes every ~6 minutes; polling faster only re-reads a held
# value. At this cadence a day costs 288 of the 10000 calls allowed.
CADENCE_MINUTES = 5
RETRIES = 3
BACKOFF_SECONDS = 5
# Beyond this the collector is considered broken rather than merely late.
STALE_AFTER = timedelta(hours=1)


def _log(event: str, **fields) -> None:
    """One JSON object per line, to a file outside the repository.

    The log is itself a coarse occupancy series -- it records when readings
    were taken and what they were -- so it lives with the data, never in the
    tree.
    """
    line = json.dumps({"ts": datetime.now(timezone.utc).isoformat(),
                       "event": event, **fields})
    log_dir = paths.DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "ingest.jsonl").open("a") as fh:
        fh.write(line + "\n")
    print(line)


def partition_for(ts: pd.Timestamp):
    d = paths.DATA_DIR / "live" / f"date={ts.date().isoformat()}"
    d.mkdir(parents=True, exist_ok=True)
    return d / "readings.parquet"


def store(reading: dict) -> int:
    """Append one reading to its day's partition, idempotently.

    Re-running the poller must never duplicate or corrupt a partition, so the
    frame is deduplicated on timestamp and the newest value wins.
    """
    path = partition_for(reading["ts"])
    frame = pd.DataFrame([reading]).set_index("ts")
    if path.exists():
        frame = pd.concat([pd.read_parquet(path), frame])
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame.to_parquet(path)
    return len(frame)


def poll(device_id: str) -> dict:
    """One reading, retried on transient failures.

    A poll that fails is not worth crashing over -- the next one is five
    minutes away -- but it must be recorded, or an outage looks like an absence
    of people rather than an absence of data.
    """
    for attempt in range(1, RETRIES + 1):
        try:
            reading = read_sensor(device_id)
            rows = store(reading)
            _log("reading", co2=reading["co2"], temp=reading["temp"],
                 battery=reading.get("battery"), rows_in_partition=rows)
            return reading
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            _log("poll_failed", attempt=attempt, error=f"{type(exc).__name__}: {exc}")
            if attempt == RETRIES:
                raise
            time.sleep(BACKOFF_SECONDS * attempt)


def partitions() -> list:
    return sorted((paths.DATA_DIR / "live").glob("date=*/readings.parquet"))


def bins_covered(index) -> int:
    """How many five-minute bins of a day hold at least one reading.

    Rows are a poor completeness measure: the device's true interval drifts
    around a minute, so a complete day from the vendor is anywhere from 1,340
    to 1,400 rows and no fixed target separates "complete" from "nearly". Bins
    do separate them, because a bin is either covered or it is a hole, and a
    hole is the only thing the backfill exists to repair. 288 of 288 means
    nothing is missing at the resolution everything downstream resamples to.
    """
    return len(set(pd.DatetimeIndex(index).floor(f"{CADENCE_MINUTES}min")))


def partition_stats() -> dict:
    """{"YYYY-MM-DD": {"rows": n, "bins": b}} for every partition on disk.

    Split out of coverage() because src/backfill.py's ledger needs raw counts
    without the five-minute-cadence assumption baked into `expected`. The
    vendor's stored history is one-minute, so a day scoring 100% against the
    poll still has four fifths of its resolution waiting in the cloud, and a
    completeness judgement made on `coverage` alone would call such a day done.
    """
    stats = {}
    for f in partitions():
        frame = pd.read_parquet(f)
        stats[f.parent.name.removeprefix("date=")] = {
            "rows": len(frame), "bins": bins_covered(frame.index)}
    return stats


def partition_rows() -> dict:
    """{"YYYY-MM-DD": rows} for every day partition on disk."""
    return {day: s["rows"] for day, s in partition_stats().items()}


def coverage() -> pd.DataFrame:
    """What was collected per day, and where the gaps are."""
    rows = partition_rows()
    if not rows:
        return pd.DataFrame(columns=["rows", "expected", "coverage"])

    frame = pd.DataFrame({"rows": pd.Series(rows)})
    frame.index = pd.to_datetime(frame.index)
    frame = frame.reindex(pd.date_range(frame.index.min(), frame.index.max(), freq="D"))
    frame["rows"] = frame["rows"].fillna(0).astype(int)
    frame["expected"] = 24 * 60 // CADENCE_MINUTES
    frame["coverage"] = (frame["rows"] / frame["expected"]).clip(upper=1.0)
    return frame


def status() -> int:
    cov = coverage()
    if cov.empty:
        print("no readings collected yet")
        return 1

    last = max(pd.read_parquet(f).index.max() for f in partitions())
    # read_sensor stamps in UTC, but a partition written by an older build may
    # be naive, so normalise rather than assuming either.
    last = last.tz_localize("UTC") if last.tzinfo is None else last.tz_convert("UTC")
    age = datetime.now(timezone.utc) - last
    poor = cov[cov["coverage"] < 0.9]

    print(f"days collected   : {len(cov)}")
    print(f"median coverage  : {cov['coverage'].median():.1%}")
    print(f"last reading     : {age.total_seconds() / 60:.0f} min ago"
          f"{'   STALE -- the collector is not running' if age > STALE_AFTER else ''}")
    if len(poor):
        print(f"days below 90%   : {len(poor)}")
    return 0 if age <= STALE_AFTER else 2


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "--status":
        sys.exit(status())
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    poll(sys.argv[1])
