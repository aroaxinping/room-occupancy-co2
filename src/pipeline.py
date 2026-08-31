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

    python3 src/pipeline.py <co2DeviceId>                one poll of the CO2 meter
    python3 src/pipeline.py <co2DeviceId> rack=<rackId>  both sensors, one run
    python3 src/pipeline.py rack=<rackId>                the rack sensor alone
    python3 src/pipeline.py --status                     coverage report, no network

Storage layout: one partition tree per device
---------------------------------------------
There are two sensors in the room now (src/ingest.py says what they are), and
they could either share one tree with a `device` column or hold a tree each.
They hold a tree each:

    DATA_DIR/live/date=YYYY-MM-DD/readings.parquet        Meter Pro (CO2)
    DATA_DIR/live-rack/date=YYYY-MM-DD/readings.parquet   rack sensor

Four reasons, in the order they matter.

1. *The 124k rows already stored are not touched at all.* `live/` keeps its
   path, its schema and its contents, and the second device is purely
   additive. A device column would mean rewriting all 92 partitions before the
   next poll ran, because a half-migrated tree is one where `device` is
   sometimes absent and any `groupby("device")` silently drops the older half.
   The migration that is not needed is the one that cannot go wrong.

2. *Deduplication is by timestamp, and would stop being safe.* `store()` and
   `backfill.merge()` both collapse on the index alone --
   `~index.duplicated(keep="last")` -- which is correct exactly as long as one
   timestamp means one reading. Two devices reporting at different times still
   land on the same minute often enough, and every such collision would
   silently discard one device's row. A shared tree therefore forces a
   composite (ts, device) key through every dedup, every read and the ledger,
   and buys nothing that a join at analysis time does not already give.

3. *The schemas genuinely differ.* The rack has no CO2 and never will, so a
   shared tree carries a column that is null for half its rows -- and
   `dropna(subset=["co2"])`, which the backfill relies on, would come to mean
   "drop the rack".

4. *Completeness is per device.* A day can be complete for one sensor and thin
   for the other: they upload independently and the vendor serves them from
   different endpoints. `coverage()`, `status()` and the backfill ledger all
   answer per-device questions, and in a shared tree each of them becomes a
   groupby whose `expected` varies by device.

What the split costs is the join, and that cost is nil: the two series have to
be put on a common grid before they can be differenced at all, which is
`read_device()` plus a `join` in an analysis module, not a storage concern.

Rows carry no device column in either tree. The directory is the label, and
`read_device()` attaches it on the way out -- so a frame that has left storage
is self-describing without any partition ever having been rewritten.
"""
import argparse
import json
import sys
import time
import urllib.error
from datetime import datetime, timedelta, timezone

import pandas as pd

import paths
from ingest import DEFAULT_DEVICE, DEVICES, device_for, read_sensor

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


def device_root(device=DEFAULT_DEVICE):
    """The partition tree for one device. Never created as a side effect."""
    return paths.DATA_DIR / device_for(device).root


def partition_for(ts: pd.Timestamp, device=DEFAULT_DEVICE):
    d = device_root(device) / f"date={ts.date().isoformat()}"
    d.mkdir(parents=True, exist_ok=True)
    return d / "readings.parquet"


def store(reading: dict, device=DEFAULT_DEVICE) -> int:
    """Append one reading to its day's partition, idempotently.

    Re-running the poller must never duplicate or corrupt a partition, so the
    frame is deduplicated on timestamp and the newest value wins. That is safe
    only because one tree holds one device: see the layout note above.
    """
    path = partition_for(reading["ts"], device)
    frame = pd.DataFrame([reading]).set_index("ts")
    if path.exists():
        frame = pd.concat([pd.read_parquet(path), frame])
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame.to_parquet(path)
    return len(frame)


def poll(device_id: str, device=DEFAULT_DEVICE) -> dict:
    """One reading, retried on transient failures.

    A poll that fails is not worth crashing over -- the next one is five
    minutes away -- but it must be recorded, or an outage looks like an absence
    of people rather than an absence of data.
    """
    device = device_for(device)
    for attempt in range(1, RETRIES + 1):
        try:
            reading = read_sensor(device_id, device)
            rows = store(reading, device)
            _log("reading", device=device.slug, co2=reading.get("co2"),
                 temp=reading["temp"], battery=reading.get("battery"),
                 rows_in_partition=rows)
            return reading
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            _log("poll_failed", device=device.slug, attempt=attempt,
                 error=f"{type(exc).__name__}: {exc}")
            if attempt == RETRIES:
                raise
            time.sleep(BACKOFF_SECONDS * attempt)


def partitions(device=DEFAULT_DEVICE) -> list:
    return sorted(device_root(device).glob("date=*/readings.parquet"))


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


def partition_stats(device=DEFAULT_DEVICE) -> dict:
    """{"YYYY-MM-DD": {"rows": n, "bins": b}} for every partition on disk.

    Split out of coverage() because src/backfill.py's ledger needs raw counts
    without the five-minute-cadence assumption baked into `expected`. The
    vendor's stored history is one-minute, so a day scoring 100% against the
    poll still has four fifths of its resolution waiting in the cloud, and a
    completeness judgement made on `coverage` alone would call such a day done.
    """
    stats = {}
    for f in partitions(device):
        frame = pd.read_parquet(f)
        stats[f.parent.name.removeprefix("date=")] = {
            "rows": len(frame), "bins": bins_covered(frame.index)}
    return stats


def partition_rows(device=DEFAULT_DEVICE) -> dict:
    """{"YYYY-MM-DD": rows} for every day partition on disk."""
    return {day: s["rows"] for day, s in partition_stats(device).items()}


def coverage(device=DEFAULT_DEVICE) -> pd.DataFrame:
    """What was collected per day, and where the gaps are."""
    rows = partition_rows(device)
    if not rows:
        return pd.DataFrame(columns=["rows", "expected", "coverage"])

    frame = pd.DataFrame({"rows": pd.Series(rows)})
    frame.index = pd.to_datetime(frame.index)
    frame = frame.reindex(pd.date_range(frame.index.min(), frame.index.max(), freq="D"))
    frame["rows"] = frame["rows"].fillna(0).astype(int)
    frame["expected"] = 24 * 60 // CADENCE_MINUTES
    frame["coverage"] = (frame["rows"] / frame["expected"]).clip(upper=1.0)
    return frame


def read_device(device=DEFAULT_DEVICE, start=None, end=None) -> pd.DataFrame:
    """Every stored reading for one device, as one UTC-indexed frame.

    The read side of the layout above, and the intended entry point for
    anything that wants both sensors: read each, resample, join. A `device`
    column is attached here rather than stored, so the frame is
    self-describing once two of them are concatenated while the partitions
    themselves stay exactly as they were written.

    `start` and `end` are inclusive day bounds (dates or anything
    `pd.Timestamp` accepts) and prune whole partitions before they are read,
    because loading five months to look at a week is the sort of thing that
    quietly makes a notebook unusable.
    """
    device = device_for(device)
    lo = pd.Timestamp(start).date() if start is not None else None
    hi = pd.Timestamp(end).date() if end is not None else None
    frames = []
    for f in partitions(device):
        day = pd.Timestamp(f.parent.name.removeprefix("date=")).date()
        if (lo and day < lo) or (hi and day > hi):
            continue
        frames.append(pd.read_parquet(f))
    if not frames:
        return pd.DataFrame(columns=list(device.channels) + ["device"])
    frame = pd.concat(frames)
    # Partitions written before `battery` existed are missing that column, so
    # concat has already aligned on names; only the index needs normalising.
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize("UTC")
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame["device"] = device.slug
    return frame


def _device_status(device) -> tuple:
    """(lines, state) for one device. state is "ok", "stale" or "absent"."""
    cov = coverage(device)
    if cov.empty:
        return ([f"no readings collected yet for {device.slug}"], "absent")

    last = max(pd.read_parquet(f).index.max() for f in partitions(device))
    # read_sensor stamps in UTC, but a partition written by an older build may
    # be naive, so normalise rather than assuming either.
    last = last.tz_localize("UTC") if last.tzinfo is None else last.tz_convert("UTC")
    age = datetime.now(timezone.utc) - last
    poor = cov[cov["coverage"] < 0.9]

    lines = [
        f"days collected   : {len(cov)}",
        f"median coverage  : {cov['coverage'].median():.1%}",
        f"last reading     : {age.total_seconds() / 60:.0f} min ago"
        f"{'   STALE -- the collector is not running' if age > STALE_AFTER else ''}",
    ]
    if len(poor):
        lines.append(f"days below 90%   : {len(poor)}")
    return (lines, "ok" if age <= STALE_AFTER else "stale")


def status() -> int:
    """Report every device, and exit non-zero if a collector has stopped.

    A device with no partitions at all is "not collecting yet" rather than a
    fault, so installing this build before the second sensor's launchd job
    exists does not start failing a check that was passing. Once a device has
    any data its silence is a real fault, and the exit code says so -- the same
    rule the single-device version applied, extended rather than changed.
    """
    reports = {d.slug: _device_status(d) for d in DEVICES.values()}
    if all(state == "absent" for _, state in reports.values()):
        print("no readings collected yet")
        return 1

    # One device with data prints exactly what it always did; the heading only
    # appears once there is something to disambiguate.
    live = [slug for slug, (_, state) in reports.items() if state != "absent"]
    for device in DEVICES.values():
        lines, state = reports[device.slug]
        if state == "absent" and device is not DEFAULT_DEVICE:
            print(f"[{device.slug}] not collecting yet "
                  f"(no partitions under DATA_DIR/{device.root})")
            continue
        if len(live) > 1:
            print(f"[{device.slug}]  DATA_DIR/{device.root}")
        for line in lines:
            print(f"{'  ' if len(live) > 1 else ''}{line}")

    if reports[DEFAULT_DEVICE.slug][1] == "absent":
        print(f"the {DEFAULT_DEVICE.slug} tree is empty while another device "
              f"has data -- the primary collector is not running", file=sys.stderr)
        return 2
    return 2 if any(state == "stale" for _, state in reports.values()) else 0


def parse_targets(targets) -> list:
    """[(device, deviceId)] from `deviceId` or `slug=deviceId` arguments.

    A bare deviceId means the CO2 meter, which is what the installed launchd
    job passes and what it has always meant. `slug=deviceId` names any other
    device, so both sensors can be polled by one scheduled invocation -- and a
    failure on one does not cost the other its reading (see main).
    """
    out = []
    for target in targets:
        slug, sep, device_id = target.partition("=")
        device = device_for(slug) if sep else DEFAULT_DEVICE
        device_id = device_id if sep else slug
        if not device_id:
            raise SystemExit(f"{target!r} names a device but no deviceId")
        out.append((device, device_id))
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("targets", nargs="*", metavar="[slug=]deviceId",
                        help="a deviceId (the CO2 meter) or slug=deviceId; "
                             f"slugs: {', '.join(sorted(DEVICES))}")
    parser.add_argument("--status", action="store_true",
                        help="coverage and staleness report, no network")
    args = parser.parse_args(argv)

    if args.status:
        return status()
    if not args.targets:
        print(__doc__)
        return 1

    # Each device is polled independently: the rack sensor failing must never
    # cost the CO2 meter its reading, because the CO2 series is the one that
    # cannot be reconstructed from anything else.
    failed = []
    for device, device_id in parse_targets(args.targets):
        try:
            poll(device_id, device)
        except Exception as exc:                          # noqa: BLE001
            print(f"{device.slug}: poll failed -- {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            failed.append(device.slug)
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
