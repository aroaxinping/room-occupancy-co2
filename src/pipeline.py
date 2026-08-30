"""Scheduled ingestion: poll, store, and say so when it stops working.

The vendor API returns present state only, so history exists only if something
collects it. Two failure modes matter more than throughput here, and the
original five months of data demonstrate both: the record already contains two
outages of about 26 days each, which is what a scheduled job failing silently
looks like after the fact.

So this writes append-only partitioned files that a re-run cannot corrupt, and
reports staleness rather than waiting to be asked.

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


def coverage() -> pd.DataFrame:
    """What was collected per day, and where the gaps are."""
    files = sorted((paths.DATA_DIR / "live").glob("date=*/readings.parquet"))
    if not files:
        return pd.DataFrame(columns=["rows", "expected", "coverage"])

    rows = {f.parent.name.removeprefix("date="): len(pd.read_parquet(f)) for f in files}
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

    last = max(pd.read_parquet(f).index.max()
               for f in (paths.DATA_DIR / "live").glob("date=*/readings.parquet"))
    age = datetime.now(timezone.utc) - last.tz_localize("UTC")
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
