"""Repair gaps in the collected record from the vendor's own history.

The official API serves present state only, so the collector's five-minute poll
is the sole source of new data -- and any hour it does not run is lost for good.
That was the architecture's weakest point: a laptop asleep, a job that dies
quietly, and the record has a hole indistinguishable from an empty room.

The app's private API does keep history, and `switchbotapi` reaches it. So the
loss is repairable after the fact: find the days the collector under-covered,
ask for them, and merge. The poll stays the primary source because it is
independent of anyone's phone; this is the safety net under it.

Credentials come from the macOS Keychain, never from arguments and never from
the environment -- a password on a command line is visible to every process on
the machine via `ps`, and an exported one lands in shell history permanently.
Both are how the upstream client's own examples do it.

Note what is being handed over: an account email and password, not a scoped
token. A compromise of the client library is a compromise of the whole account,
including device control. It is imported lazily and left out of
requirements.txt for that reason -- nothing else here depends on it.

    python3 src/backfill.py            refresh the last few days (the daily job)
    python3 src/backfill.py --gaps     repair only days below the coverage threshold
    python3 src/backfill.py --all      re-fetch everything the cloud holds

The default is a rolling refresh rather than gap repair, because gap repair
alone would never run. Coverage is measured against the poll's five-minute
cadence, so a day the poll covered fully scores 100% and is skipped -- even
though the cloud holds the same day at one-minute resolution. Refreshing
recent days unconditionally picks that up, and also catches the case where the
phone uploaded late.

The vendor only serves what the phone app has uploaded, and that upload happens
when someone opens the device's history screen with Bluetooth connected. An
empty response usually means that has not happened recently, not that the call
failed.
"""
import argparse
import sys
from datetime import date, datetime, timedelta

import pandas as pd

import paths
from ingest import _credential
from pipeline import CADENCE_MINUTES, coverage, partition_for

COVERAGE_THRESHOLD = 0.90
# How far back the rolling refresh reaches. Long enough that a weekend without
# the phone opening the app still gets collected.
RECENT_DAYS = 4
# The record starts here; nothing earlier exists to ask for.
EARLIEST = "20260329"


def _client():
    from switchbotapi import SwitchBotClient

    client = SwitchBotClient(_credential("email"), _credential("password"), region="eu")
    client.login()
    return client


def _co2_device(client) -> dict:
    for device in client.list_devices():
        if "MeterPro" in str(device.get("device_type", "")) or \
           "W1079001" == device.get("device_type"):
            return device
    matches = [d for d in client.list_devices() if "co2" in str(d).lower()]
    if not matches:
        raise RuntimeError("no Meter Pro CO2 device on this account")
    return matches[0]


def fetch(start: date, end: date) -> pd.DataFrame:
    """History for a date range, in the same shape the collector writes."""
    client = _client()
    device = _co2_device(client)
    rows = client.get_meter_pro_history(
        device["device_mac"],
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )
    if not rows:
        return pd.DataFrame(columns=["temp", "rh", "co2"])

    frame = pd.DataFrame(rows)
    # The client formats each point with .astimezone() before strftime, so the
    # string is LOCAL wall-clock time carrying no offset. Labelling it UTC --
    # which reads naturally and is wrong -- shifts the whole record by the
    # local offset, and the error is invisible until a timestamp lands in the
    # future. Localise to the machine's zone, then convert.
    local = datetime.now().astimezone().tzinfo
    frame["ts"] = (pd.to_datetime(frame["timestamp"])
                   .dt.tz_localize(local, ambiguous="NaT", nonexistent="NaT")
                   .dt.tz_convert("UTC"))
    frame = frame.rename(columns={"temperature_c": "temp", "humidity_pct": "rh",
                                  "co2_ppm": "co2"})
    frame = frame.set_index("ts")[["temp", "rh", "co2"]]
    frame = frame.dropna(subset=["co2"]).sort_index()
    # A reading in the future means the offset handling is wrong again.
    ahead = frame.index.max() - pd.Timestamp.utcnow()
    if ahead > pd.Timedelta(minutes=5):
        raise RuntimeError(
            f"newest fetched reading is {ahead} in the future; the vendor's "
            "timestamps are not being interpreted in the right zone")
    return frame


def merge(history: pd.DataFrame) -> dict:
    """Write history into the day partitions without displacing polled rows.

    Polled readings win on a tie: they were recorded by this pipeline, at a
    known time, whereas a backfilled row has passed through the vendor's cloud
    and the phone's clock.
    """
    written = {}
    for day, chunk in history.groupby(history.index.date):
        path = partition_for(pd.Timestamp(day))
        existing = pd.read_parquet(path) if path.exists() else None
        combined = chunk if existing is None else pd.concat([chunk, existing])
        # keep="last" so the polled rows, concatenated second, survive.
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        combined.to_parquet(path)
        written[str(day)] = len(combined) - (0 if existing is None else len(existing))
    return written


def gaps(threshold: float = COVERAGE_THRESHOLD) -> list:
    cov = coverage()
    if cov.empty:
        return []
    return [d.date() for d in cov.index[cov["coverage"] < threshold]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--all", action="store_true",
                        help="re-fetch the whole record")
    parser.add_argument("--gaps", action="store_true",
                        help="fetch only days below the coverage threshold")
    parser.add_argument("--days", type=int, default=RECENT_DAYS,
                        help="how far back the rolling refresh reaches")
    parser.add_argument("--threshold", type=float, default=COVERAGE_THRESHOLD)
    args = parser.parse_args()

    if args.all:
        start, end = pd.Timestamp(EARLIEST).date(), date.today()
        print(f"fetching everything from {start} to {end}")
    elif args.gaps:
        days = gaps(args.threshold)
        if not days:
            print(f"no day below {args.threshold:.0%} coverage; nothing to repair")
            return 0
        start, end = min(days), max(days)
        print(f"{len(days)} day(s) below {args.threshold:.0%}, spanning {start} to {end}")
    else:
        end = date.today()
        start = end - timedelta(days=args.days)
        print(f"refreshing the last {args.days} days, {start} to {end}")

    history = fetch(start, end + timedelta(days=1))
    if history.empty:
        print("the vendor returned no rows. Open the device's history screen in "
              "the app with Bluetooth connected, then retry.", file=sys.stderr)
        return 1

    print(f"fetched {len(history)} rows, {history.index.min()} to {history.index.max()}")
    for day, added in sorted(merge(history).items()):
        print(f"  {day}  +{added} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
