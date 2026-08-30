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

    python3 src/backfill.py                  incremental refresh (the daily job)
    python3 src/backfill.py --plan           what that run would fetch, no network
    python3 src/backfill.py --gaps           repair days below the coverage threshold
    python3 src/backfill.py --all            re-fetch everything the cloud holds
    python3 src/backfill.py --device rack    restrict any of the above to one sensor
    python3 src/backfill.py --migrate-state  upgrade the ledger to v2 (see below)
    python3 src/backfill.py --revert-state   undo that, from the backup it kept

Two devices, two endpoints
--------------------------
Both sensors in the room are repaired from the cloud, and the vendor serves
them from different endpoints with different payloads:

    meterpro-co2   get_meter_pro_history(mac, start_date, end_date, ...)
                   -> {"timestamp", "temperature_c", "humidity_pct", "co2_ppm"}
    rack           get_sensor_history(mac, start_date, end_date, ...)
                   -> {"timestamp", "temperature_c", "humidity_pct"}

`get_sensor_history` carries no CO2 for any device -- that is a property of the
endpoint, not of the hardware -- so the rack's frame has two channels and the
CO2 meter's has three. Both go through the same timestamp handling, because
both are formatted by the same code in the client and both are wrong in the
same way if read naively (see "On the vendor's timestamps").

Each device is planned, fetched, merged and recorded separately, in its own
partition tree (src/pipeline.py explains why they are separate) against its own
ledger. A failure on one device is caught and reported, and the next device
still runs: the CO2 series is the only one that cannot be reconstructed from
anything else, so a newly added sensor must never be able to cost it a repair.

Incremental, and why the obvious way to do it is wrong
------------------------------------------------------
This used to re-fetch a fixed rolling window -- the last four days, every run,
unconditionally. On a normal morning that is about 5,700 rows downloaded to add
nothing.

The obvious fix is a watermark: remember the newest timestamp fetched and start
the next run there. Here that is not merely wasteful to get wrong, it is
silently destructive. The vendor's cloud holds only what the phone or hub has
uploaded, and that upload happens when someone opens the device's history
screen with Bluetooth in range -- which can lag by days. So a day is routinely
*sparse when first asked for and complete later*. A watermark advances past
such a day on the strength of the clock alone, never looks at it again, and the
missing hours become permanent and invisible. That is not hypothetical: the
original record carries two silent 26-day holes, and a hole is
indistinguishable from an empty room.

So what is tracked is not a high-water mark but a per-day ledger, and a day
leaves the fetch plan only on evidence about the data itself. It can leave by
either of two doors, and both require the day to be at least
SETTLE_AFTER_AGE_DAYS old, so a day still receiving uploads is never a
candidate:

  complete   -- the day came back with at least COMPLETE_BINS of its 288
                five-minute bins covered. There is no hole left in it, so
                nothing that could arrive later would repair anything: the
                analysis resamples to five minutes and a bin that already holds
                a reading cannot be made more present. This is the door almost
                every day leaves by, and it needs only one observation.

  stably incomplete -- the day still has holes, but the last SETTLE_STREAK
                consecutive fetches returned the same row count, and it is at
                least MIN_SETTLE_COVERAGE dense. Any growth resets the streak to
                zero, so a day that fills in late is by construction fetched
                again after it fills in. This door exists for days the cloud
                genuinely never had -- the sensor was off, the hub was down --
                which would otherwise be retried forever. A day thinner than
                MIN_SETTLE_COVERAGE cannot use it at all, however stable it is,
                because "stably thin" is exactly what an un-uploaded day looks
                like and settling one is how a gap becomes permanent.

Completeness is measured in bins rather than rows deliberately. The device's
real interval drifts either side of a minute, so a complete day is anywhere
from ~1,340 to ~1,400 rows and no row target separates "complete" from "nearly
complete". A bin is either covered or it is a hole, and a hole is the only
thing this module exists to repair.

On top of that, two mechanisms re-examine days that *are* settled:

    * a periodic re-window -- every SWEEP_EVERY_DAYS the run ignores the
      settled flag across the last SWEEP_WINDOW_DAYS entirely, so a day
      wrongly retired stays retired for at most a week;
    * `--gaps` and `--all`, which never consult the ledger at all.

Why this cannot silently skip a late-arriving day
-------------------------------------------------
Take a day D that the cloud serves half-empty on Monday and complete on Friday.

  * Monday's fetch leaves D missing bins, so the complete door is shut. It
    records a row count below MIN_SETTLE_COVERAGE, so the stably-incomplete
    door is shut too, however many times D comes back identical. D stays in the
    plan every single day until it fills in. Friday's fetch finds it complete
    and merges the missing half.
  * Suppose instead D came back merely incomplete rather than thin -- dense
    enough for the second door. It still cannot take it on one observation: the
    first fetch sets its streak to 1, a streak of 1 is not SETTLE_STREAK, so it
    is fetched again, and if anything arrived in between the count differs and
    the streak resets to 0. That door can only be opened by a day observed
    twice, unchanged, which is a statement about the data rather than about the
    calendar. Neither door can be opened by the passage of time, which is
    exactly what a watermark uses and the reason a watermark is unsafe here.
  * Suppose the ledger itself is lost, corrupted, or wrong. A missing or
    unparsable state file degrades to "nothing is settled" rather than to
    "everything is settled", and the periodic re-window re-fetches the last
    month regardless of what the ledger claims.
  * Suppose a day was settled as complete and the cloud later serves *more* of
    it. That cannot mean a repaired hole -- every bin was already covered -- so
    skipping it loses no coverage; and the re-window picks the extra rows up
    within SWEEP_EVERY_DAYS anyway.

The one case *not* recovered automatically is a day that is both older than
LOOKBACK_DAYS and has never come back dense -- typically a day past the
vendor's ~3-month retention, where no number of retries will help. Those are
not skipped silently either: they stay in the ledger, and every run prints them
under "never came back dense", with `--all` still asking for them. Failing
loudly and cheaply was the whole point of the exercise.

What it saves
-------------
Measured by replaying the policy day by day over the record already on disk,
with the partitions standing in for the vendor (for a backfilled day they are
exactly what the vendor served) and nothing served ahead of the simulated
clock:

    ordinary run   1,652 rows, against 5,726 for the old window   -71%
    week, all in     ~24k rows, against ~40k                      -39%

The gap between those two numbers is the periodic re-window, which is the whole
cost of the safety margin and the one figure worth revisiting: at
SWEEP_EVERY_DAYS=7 / SWEEP_WINDOW_DAYS=10 it is about 13,800 rows once a week.
Turning it off entirely would take the saving to -70%, and is not worth it --
the whole point of this module is that a hole here is invisible and permanent,
and one re-window a week is a cheap price for not depending solely on the
ledger being right. Requests drop from one a day to one a day (the plan is
coalesced into contiguous ranges, so it stays a single call in the normal case)
while the rows on the wire fall by two thirds.

One ledger per device, and why not one shared one
--------------------------------------------------
The ledger is per (device, day), not per day. The two sensors upload
independently -- different BLE sessions, different endpoints, different
retention -- so a day is routinely complete for one and thin for the other, and
a single per-day record could only hold one of those two facts. Whichever
convention it took would be wrong: "settled when both are settled" keeps
re-fetching a CO2 day that has nothing left to repair, and "settled when either
is" retires the rack's holes on the strength of the CO2 meter's completeness,
which is precisely the silent-hole failure this module exists to prevent. The
completeness thresholds are per-device quantities too -- `COMPLETE_BINS` is
counted against a device's own partitions -- so sharing a record would also
mean sharing a denominator that does not apply.

So the state file is versioned to 2 and nests the v1 shape under a device key:

    {"version": 2,
     "devices": {"meterpro-co2": {"last_sweep": ..., "days": {...}},
                 "rack":         {"last_sweep": ..., "days": {...}}}}

`last_sweep` is per device for the same reason: the periodic re-window is a
statement about one device's fetch history.

A v1 file is the CO2 meter's ledger, because that was the only device when it
was written, so the upgrade is a pure move of `days` and `last_sweep` under
`devices["meterpro-co2"]`. `upgrade_state()` does that and is idempotent -- a
v2 file passes through unchanged -- and `load_state()` applies it in memory so
an ordinary run never loses a streak. `--migrate-state` writes the upgraded
file back after copying the original to `backfill_state.v1.bak.json`, and
`--revert-state` restores that copy; neither deletes anything, and running
either twice is a no-op.

Where the state lives, and why JSON
-----------------------------------
`DATA_DIR/backfill_state.json`, never inside the repository -- the ledger says
which dates have readings and how many, which is the same kind of fact about
one address as the readings themselves, so it falls under the same rule (see
src/paths.py).

JSON rather than parquet because this is metadata, not measurement: one small
record per day, a few hundred for the life of the project, read whole and
rewritten whole, with no columnar scan or dtype question to answer. It wants to
be diffable and repairable by hand at 05:30 when something has gone wrong,
which a binary file is not, and it must be readable without pandas so that a
broken environment does not also cost the ledger. It is written atomically
(temp file plus `os.replace`) so a crash mid-write cannot leave a truncated
file -- and, worse, one that happens to parse as "everything is settled".

On the vendor's timestamps
--------------------------
The client formats each point in LOCAL wall-clock time with no offset attached;
`frame_from_rows()` localises before converting to UTC. Labelling those strings
UTC reads naturally and is wrong by the local offset -- that was a real
two-hour bug, and the same function still refuses any response whose newest
reading lands in the future, which is how it was caught.

This is not specific to the Meter Pro endpoint. Every history method in the
client formats its points the same way -- `datetime.fromtimestamp(dd,
tz=utc).astimezone().strftime(...)`, which drops the offset it just applied --
so `get_sensor_history` returns exactly the same trap for the rack sensor. Both
devices therefore share one parsing function rather than each having its own:
the localise-then-convert step and the future guard are written once, and a
third device cannot be added with a fresh copy of the bug.

The vendor only serves what the phone app has uploaded, and that upload happens
when someone opens the device's history screen with Bluetooth connected. An
empty response usually means that has not happened recently, not that the call
failed.
"""
import argparse
import json
import os
import shutil
import sys
from datetime import date, datetime, timedelta, timezone

import pandas as pd

import paths
from ingest import (DEFAULT_DEVICE, DEVICES, METER_PRO_CO2, _credential,
                    device_for)
from pipeline import (CADENCE_MINUTES, bins_covered, coverage, device_root,
                      partition_for, partition_stats)

COVERAGE_THRESHOLD = 0.90
# How far back the open tail reaches: these days are fetched every run whatever
# the ledger says. It used to be 4, which was the whole policy -- a blunt
# window wide enough to cover a weekend with the phone away. The ledger now
# provides that guarantee properly (an incomplete day stays in the plan until
# it fills in, for as long as that takes, rather than for four days), so the
# unconditional part shrinks to the days that cannot be judged yet: today,
# which is still being written, and yesterday, which may still be uploading.
# --days still widens it for anyone who wants the old behaviour back.
RECENT_DAYS = 1
# What the old unconditional window was, kept only so every run can report what
# it saved against it.
OLD_WINDOW_DAYS = 4
# How far back the planner reaches when there are no partitions to infer it
# from. Deliberately a duration rather than a literal date: the first day of the
# record is a fact about the occupant, and this repository does not carry those
# (see the README). With partitions present the bound is read from them.
FALLBACK_LOOKBACK_DAYS = 550

# --- the ledger -----------------------------------------------------------
STATE_PATH = paths.DATA_DIR / "backfill_state.json"
STATE_VERSION = 2
# Where --migrate-state parks the v1 file, and where --revert-state looks for
# it. A copy, never a move: nothing under DATA_DIR is deleted by either.
STATE_BACKUP_PATH = paths.DATA_DIR / "backfill_state.v1.bak.json"
# The vendor's history is one-minute, so a full day is this many rows.
VENDOR_CADENCE_MINUTES = 1
EXPECTED_VENDOR_ROWS = 24 * 60 // VENDOR_CADENCE_MINUTES
# A day thinner than this never settles: "stably thin" is what an un-uploaded
# day looks like, and settling one is how a gap becomes permanent.
MIN_SETTLE_COVERAGE = 0.50
# Completeness is judged in five-minute bins, not rows -- see
# pipeline.bins_covered for why. 288 bins is a whole day.
EXPECTED_BINS = 24 * 60 // CADENCE_MINUTES
# A day with this fraction of its bins covered has no hole left to repair.
COMPLETE_BINS = 0.99
# A day younger than this is still receiving uploads; never settle it.
SETTLE_AFTER_AGE_DAYS = 2
# Consecutive identical fetches required before a day is considered done.
SETTLE_STREAK = 2
# How far back unsettled days are retried automatically.
LOOKBACK_DAYS = 45
# The periodic re-window: every N days, re-examine the last M days whatever the
# ledger says about them. This is the belt to the ledger's braces, and it is
# not free -- it is the one part of the policy that costs bandwidth in the
# normal case, roughly M/N days' worth of rows amortised per run. Ten days
# weekly is chosen against the failure it guards: an upload that lagged and was
# somehow retired anyway. Observed lag is days, so ten is generously past it,
# and a full-record re-examination is what --all is for. Widening the window or
# shortening the interval is a straight trade of bandwidth for paranoia, and
# every run prints what the current setting costs.
SWEEP_EVERY_DAYS = 7
SWEEP_WINDOW_DAYS = 10
# Planned days this far apart are still asked for as one range -- one request
# for [d, d+1] costs less than two, and the extra day merges idempotently.
BRIDGE_DAYS = 1


# --------------------------------------------------------------------------
# The ledger: pure functions over a plain dict, so every decision below is
# testable against stored parquet without the network or the Keychain.
# --------------------------------------------------------------------------

def _iso(day) -> str:
    return day.isoformat() if hasattr(day, "isoformat") else str(day)


def _dense(rows) -> bool:
    return (rows or 0) >= MIN_SETTLE_COVERAGE * EXPECTED_VENDOR_ROWS


def _complete(bins) -> bool:
    return (bins or 0) >= COMPLETE_BINS * EXPECTED_BINS


def empty_device_state() -> dict:
    """One device's ledger: the whole of what v1 used to hold at top level."""
    return {"last_sweep": None, "days": {}}


def empty_state() -> dict:
    return {"version": STATE_VERSION,
            "devices": {slug: empty_device_state() for slug in DEVICES}}


def state_for(state: dict, device=DEFAULT_DEVICE) -> dict:
    """The per-device sub-ledger, created on first use.

    Every pure function below still takes something shaped like the v1 state --
    a dict with "days" and "last_sweep" -- so the per-device split cost them no
    signature change and no new branch.
    """
    slug = device_for(device).slug
    return state.setdefault("devices", {}).setdefault(slug, empty_device_state())


def upgrade_state(state: dict) -> dict:
    """v1 -> v2, idempotently. Pure: no disk, no clock.

    A v1 file predates the second sensor, so its days are the CO2 meter's by
    definition and move under that key unchanged. A v2 file is returned as it
    came in, which is what makes running the migration twice a no-op. Anything
    unrecognisable returns None so the caller can fall back to re-seeding
    rather than build a ledger on a guess.
    """
    if not isinstance(state, dict):
        return None
    version = state.get("version")
    if version == 2 and isinstance(state.get("devices"), dict):
        for slug in DEVICES:
            state["devices"].setdefault(slug, empty_device_state())
        return state
    if version == 1 and isinstance(state.get("days"), dict):
        upgraded = empty_state()
        upgraded["devices"][DEFAULT_DEVICE.slug] = {
            "last_sweep": state.get("last_sweep"),
            "days": state["days"],
        }
        return upgraded
    return None


def downgrade_state(state: dict) -> dict:
    """v2 -> v1, keeping only the CO2 meter -- the reverse of the above.

    Lossy by construction: v1 has nowhere to put a second device. That is why
    --migrate-state keeps a copy of the original file rather than relying on
    this, and why --revert-state prefers the copy; this exists so the shape
    change is reversible in principle as well as in practice.
    """
    device = state_for(state, DEFAULT_DEVICE)
    return {"version": 1, "last_sweep": device.get("last_sweep"),
            "days": device.get("days", {})}


def seed_device_state(stats: dict, today: date) -> dict:
    """Bootstrap one device's ledger from the partitions already on disk.

    Without this, the first incremental run would treat every day as unknown
    and re-fetch the whole lookback window. A partition that is already dense
    is evidence that the day was fetched and came back complete, so it is
    seeded as settled -- and the sweep re-tests it within the week, which is
    what keeps the shortcut honest. A thin partition is seeded *unsettled*: 288
    rows is a day the five-minute poll covered on its own while the cloud still
    holds it at one-minute resolution, which is worth asking for.
    """
    state = empty_device_state()
    for day, stat in stats.items():
        d = day if isinstance(day, date) else pd.Timestamp(day).date()
        rows, bins = stat["rows"], stat["bins"]
        aged = (today - d).days >= SETTLE_AFTER_AGE_DAYS
        state["days"][_iso(d)] = {
            "fetched_rows": int(rows),
            "fetched_bins": int(bins),
            # A dense seeded day is credited with a streak so that the
            # stably-incomplete door is reachable without two more fetches;
            # the complete door does not need it. Either way the re-window
            # re-tests it within the week.
            "unchanged_streak": SETTLE_STREAK if (_dense(rows) and aged) else 0,
            "fetches": 0,
            "last_fetch": None,
            "seeded": True,
        }
    return state


def seed_state(today: date, devices=None) -> dict:
    """A whole v2 ledger seeded from each device's own partitions."""
    state = empty_state()
    for device in (devices or DEVICES.values()):
        device = device_for(device)
        state["devices"][device.slug] = seed_device_state(
            partition_stats(device), today)
    return state


def read_state_file(path=STATE_PATH) -> dict:
    """The parsed file, or None if it is missing, truncated or unreadable."""
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as exc:
        print(f"backfill ledger unreadable ({exc}); rebuilding it, so this run "
              f"fetches more than usual", file=sys.stderr)
        return None


def load_state(path=STATE_PATH, today: date = None) -> dict:
    """Read the ledger, degrading to "nothing is settled" on any doubt.

    Every failure mode here -- missing file, truncated file, a version this
    build does not understand -- resolves towards fetching more rather than
    less. The cost of being wrong in that direction is bandwidth; the cost of
    being wrong in the other is a permanent hole.

    A v1 file is the one case that is *not* doubt: it is a well-formed ledger
    for the CO2 meter written before the rack sensor existed, so it is upgraded
    in memory rather than thrown away. A copy of the original is kept first, so
    that the ordinary run which then writes v2 back cannot be the thing that
    loses it, and --revert-state has something to restore.
    """
    today = today or datetime.now(timezone.utc).date()
    raw = read_state_file(path)
    if raw is None:
        return seed_state(today)
    if raw.get("version") == 1:
        _backup_v1(path)
        print("backfill ledger is v1 (one device); upgrading it in memory. "
              "Run --migrate-state to write it back in v2 form.", file=sys.stderr)
    state = upgrade_state(raw)
    if state is None:
        print(f"backfill ledger is version {raw.get('version')!r}, not "
              f"{STATE_VERSION}; rebuilding it", file=sys.stderr)
        return seed_state(today)
    return state


def save_state(state: dict, path=STATE_PATH) -> None:
    """Atomic rewrite: a crash mid-write must not leave a file that parses."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    os.replace(tmp, path)


def _backup_v1(path=STATE_PATH, backup=STATE_BACKUP_PATH) -> bool:
    """Copy a v1 ledger aside, once. Never overwrites an existing backup."""
    if backup.exists() or not path.exists():
        return False
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup)
    return True


def migrate_state(path=STATE_PATH, backup=STATE_BACKUP_PATH) -> int:
    """Write the ledger back in v2 form, keeping the original beside it.

    Idempotent: on an already-v2 file nothing is written and nothing is backed
    up. Reversible: --revert-state puts the copy back. Non-destructive: the
    original is copied, never moved, and no partition is touched -- the
    partition layout needs no migration at all, which is the point of giving
    each device its own tree (see src/pipeline.py).
    """
    raw = read_state_file(path)
    if raw is None:
        print(f"no ledger at {path}; nothing to migrate. The next run will "
              f"seed one from the partitions on disk.")
        return 0
    if raw.get("version") == STATE_VERSION:
        print(f"ledger is already v{STATE_VERSION}; nothing to do")
        return 0
    upgraded = upgrade_state(raw)
    if upgraded is None:
        print(f"ledger is version {raw.get('version')!r} and this build cannot "
              f"upgrade it; leave it alone and let a run re-seed from the "
              f"partitions instead", file=sys.stderr)
        return 1
    _backup_v1(path, backup)
    save_state(upgraded, path)
    print(f"ledger upgraded v{raw.get('version')} -> v{STATE_VERSION}\n"
          f"  original copied to {backup}\n"
          f"  devices: {', '.join(sorted(upgraded['devices']))}\n"
          f"  undo with: python3 src/backfill.py --revert-state")
    return 0


def revert_state(path=STATE_PATH, backup=STATE_BACKUP_PATH) -> int:
    """Restore the pre-migration ledger from the copy migrate_state kept.

    The current file is copied aside first, so reverting is itself reversible
    and neither direction deletes anything under DATA_DIR.
    """
    if not backup.exists():
        print(f"no backup at {backup}; nothing to revert to. A ledger can "
              f"always be rebuilt with --reset-state", file=sys.stderr)
        return 1
    if path.exists():
        aside = path.with_name(f"backfill_state.v{STATE_VERSION}.bak.json")
        shutil.copy2(path, aside)
        print(f"  current ledger copied to {aside}")
    shutil.copy2(backup, path)
    print(f"ledger restored from {backup}")
    return 0


def is_settled(record: dict, day: date, today: date) -> bool:
    """Old enough, and through one of the two doors. See the docstring."""
    if not record:
        return False
    if (today - day).days < SETTLE_AFTER_AGE_DAYS:
        return False
    if _complete(record.get("fetched_bins")):
        return True
    return (record.get("unchanged_streak", 0) >= SETTLE_STREAK
            and _dense(record.get("fetched_rows")))


def record_fetch(state: dict, day: date, rows: int, bins: int = 0,
                 now: datetime = None) -> dict:
    """Note what the vendor served for one day, and update its streak.

    `rows` is the count the *vendor* returned, not the size of the partition.
    The partition also grows from the five-minute poll, which says nothing
    about whether the cloud has finished uploading; conflating the two would
    let local poll activity settle a day the cloud never delivered.
    """
    now = now or datetime.now(timezone.utc)
    key = _iso(day)
    record = state["days"].get(key) or {"fetched_rows": None,
                                        "unchanged_streak": 0, "fetches": 0}
    record["unchanged_streak"] = (record.get("unchanged_streak", 0) + 1
                                  if record.get("fetched_rows") == rows else 0)
    record["fetched_rows"] = int(rows)
    record["fetched_bins"] = int(bins)
    record["fetches"] = record.get("fetches", 0) + 1
    record["last_fetch"] = now.isoformat()
    record.pop("seeded", None)
    state["days"][key] = record
    return record


def stale_days(state: dict, today: date, device=DEFAULT_DEVICE) -> list:
    """Days that never came back dense and are now past automatic retry.

    Reported every run rather than dropped. Most are simply older than the
    vendor's retention, where retrying cannot help -- but the failure has to
    stay visible, because an invisible gap is the thing this module exists to
    prevent.

    `state` is one device's sub-ledger, and `device` only bounds the report at
    the start of that device's own record.
    """
    out = []
    earliest = earliest_date(device)
    for key, record in state["days"].items():
        try:
            day = date.fromisoformat(key)
        except ValueError:
            continue
        if (today - day).days <= LOOKBACK_DAYS or day < earliest:
            continue
        if not _dense(record.get("fetched_rows")):
            out.append((day, record.get("fetched_rows") or 0))
    return sorted(out)


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------

def due_sweep(state: dict, today: date, every: int = None) -> bool:
    # Resolved in the body rather than captured as a default, so that a test
    # can retune the constants at module level and have them take effect.
    every = SWEEP_EVERY_DAYS if every is None else every
    last = state.get("last_sweep")
    if not last:
        return True
    try:
        return (today - date.fromisoformat(last)).days >= every
    except ValueError:
        return True


def _tree_earliest(device) -> date:
    days = sorted(p.parent.name.removeprefix("date=")
                  for p in device_root(device).glob("date=*/readings.parquet"))
    return date.fromisoformat(days[0]) if days else None


def earliest_date(device=DEFAULT_DEVICE) -> date:
    """The earliest day worth asking for, for one device.

    Taken from the partitions on disk so the bound comes from the data rather
    than from a constant in the source. Asking earlier than the record starts
    costs an empty response, not a wrong answer, so the fallback is generous.

    A device with no partitions yet -- which is exactly the rack sensor on the
    day it is added -- falls back to the earliest day any device has, rather
    than straight to the duration below. The room's record began when it began;
    a second sensor cannot have readings older than the first partition on this
    machine, and asking 550 days back for a device whose tree is empty would
    make its first --all a request for a year and a half of nothing.
    """
    own = _tree_earliest(device)
    if own:
        return own
    others = [d for d in (_tree_earliest(dev) for dev in DEVICES.values()) if d]
    if others:
        return min(others)
    return date.today() - timedelta(days=FALLBACK_LOOKBACK_DAYS)


def plan_days(state: dict, today: date, recent_days: int = None,
              sweep: bool = False, gap_days=None, earliest: date = None,
              device=DEFAULT_DEVICE) -> dict:
    """Which days to ask for, and why. Pure: no network, no clock, no disk.

    Returns {date: reason}. The reasons are printed by --plan, because a
    scheduled job that cannot explain its own decisions is how the last set of
    holes went unnoticed for 26 days.

    `state` is one device's sub-ledger (`state_for(state, device)`), so the
    policy below is unchanged from the single-device version: it simply runs
    once per device over that device's own history.
    """
    earliest = earliest or earliest_date(device)
    recent_days = RECENT_DAYS if recent_days is None else recent_days
    plan = {}

    # The open tail. Always fetched, never settled (rule 1), because today and
    # yesterday are still receiving uploads by definition.
    for i in range(recent_days + 1):
        plan[today - timedelta(days=i)] = "recent"

    # Anything inside the lookback that has not proved it stopped growing.
    for i in range(LOOKBACK_DAYS + 1):
        day = today - timedelta(days=i)
        if day in plan:
            continue
        record = state["days"].get(_iso(day))
        if record is None:
            plan[day] = "never fetched"
        elif not is_settled(record, day, today):
            plan[day] = ("sparse" if not _dense(record.get("fetched_rows"))
                         else "incomplete")

    # The periodic re-window: settled days are re-examined on a fixed cadence,
    # so a day wrongly retired stays retired for at most SWEEP_EVERY_DAYS.
    if sweep:
        for i in range(SWEEP_WINDOW_DAYS + 1):
            plan.setdefault(today - timedelta(days=i), "sweep")

    # Days the poll under-covered, on the same criterion --gaps uses.
    for day in gap_days or []:
        if 0 <= (today - day).days <= LOOKBACK_DAYS:
            plan.setdefault(day, "gap")

    # Nothing before the record began: those days would come back empty every
    # run, and an empty day is never allowed to settle, so without this clamp
    # they would be retried forever.
    return {d: r for d, r in sorted(plan.items()) if d >= earliest}


def coalesce(days, bridge: int = None) -> list:
    """Contiguous (start, end) ranges, merging runs separated by <= bridge.

    The vendor takes a date range per call, so a plan of scattered days would
    otherwise cost one login and one request each. Bridging one-day holes
    trades a few unwanted rows for a whole request, and the merge is
    idempotent, so the extra day costs nothing but bytes.
    """
    bridge = BRIDGE_DAYS if bridge is None else bridge
    days = sorted(days)
    if not days:
        return []
    ranges = [[days[0], days[0]]]
    for day in days[1:]:
        if (day - ranges[-1][1]).days <= bridge + 1:
            ranges[-1][1] = day
        else:
            ranges.append([day, day])
    return [(a, b) for a, b in ranges]


def rows_for(days, stats: dict) -> int:
    """What a set of days is worth in rows, from what is already on disk.

    Used only to report the saving. The vendor's holdings for a past day are
    approximated by the partition, which for a backfilled day is precisely what
    the vendor served.
    """
    return sum(stats.get(_iso(d), {}).get("rows", 0) for d in days)


def expand(ranges) -> list:
    """Every day a set of ranges actually asks for, bridged days included."""
    out = []
    for start, end in ranges:
        out.extend(start + timedelta(days=i) for i in range((end - start).days + 1))
    return out


# --------------------------------------------------------------------------
# The network edge. Everything above this line runs without it.
# --------------------------------------------------------------------------

def _client():
    from switchbotapi import SwitchBotClient

    client = SwitchBotClient(_credential("email"), _credential("password"), region="eu")
    client.login()
    return client


def find_vendor_device(client, device=DEFAULT_DEVICE) -> dict:
    """The account entry for one of our devices, matched by type then by name.

    The private API reports `device_type` as a numeric code for some families
    and a Wo-name for others, and the entry is sometimes nested under
    `device_detail`, so the type match reads both places and the device's own
    `name_hints` are the fallback. If neither identifies exactly one device the
    account's devices are listed in the error: a wrong MAC here would write one
    sensor's history into the other's tree, which nothing downstream could
    detect, so guessing is worse than failing.
    """
    device = device_for(device)
    entries = client.list_devices()

    def type_of(entry):
        return str(entry.get("device_type")
                   or (entry.get("device_detail") or {}).get("device_type") or "")

    wanted = {t.lower() for t in device.vendor_types}
    matches = [e for e in entries if type_of(e).lower() in wanted]
    if not matches:
        matches = [e for e in entries
                   if any(t.lower() in type_of(e).lower() for t in device.vendor_types)]
    if not matches:
        matches = [e for e in entries
                   if any(h in str(e.get("device_name", "")).lower()
                          for h in device.name_hints)]
    if len(matches) == 1:
        return matches[0]
    listing = ", ".join(f"{e.get('device_name')!r} ({type_of(e)})" for e in entries)
    raise RuntimeError(
        f"{'no' if not matches else len(matches)} device(s) on this account "
        f"match {device.slug!r} (types {device.vendor_types}, name hints "
        f"{device.name_hints}); account holds: {listing}. Pass --mac to pin it.")


# Kept because it named the one device this module used to know about.
def _co2_device(client) -> dict:
    return find_vendor_device(client, METER_PRO_CO2)


def frame_from_rows(rows, device=DEFAULT_DEVICE) -> pd.DataFrame:
    """Vendor rows -> the frame the collector writes. Pure: no network.

    Shared by both endpoints, and the reason it is a separate function is the
    timestamp handling below: every history method in the client formats its
    points the same way, so a per-endpoint copy of this would be a per-endpoint
    copy of the two-hour bug.
    """
    device = device_for(device)
    columns = list(device.channels)
    if not rows:
        return pd.DataFrame(columns=columns)

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
    # get_sensor_history never returns a CO2 field for any device, so a rack
    # frame has two channels and asking for a third would be a KeyError rather
    # than a column of nulls.
    for column in columns:
        if column not in frame:
            frame[column] = pd.NA
    frame = frame.set_index("ts")[columns]
    # For the CO2 meter, a row without CO2 is not worth keeping -- that is the
    # rule this module has always applied. For a device with no CO2 channel the
    # equivalent is a row with no measurement in it at all.
    frame = frame.dropna(subset=(["co2"] if device.has_co2 else columns),
                         how="all").sort_index()
    if frame.empty:
        return frame
    # A reading in the future means the offset handling is wrong again.
    ahead = frame.index.max() - pd.Timestamp.utcnow()
    if ahead > pd.Timedelta(minutes=5):
        raise RuntimeError(
            f"newest fetched reading is {ahead} in the future; the vendor's "
            "timestamps are not being interpreted in the right zone")
    return frame


def fetch(start: date, end: date, device=DEFAULT_DEVICE, mac: str = None) -> pd.DataFrame:
    """History for a date range, in the same shape the collector writes.

    The only function here that touches the network. Everything deciding *what*
    to ask for is a pure function of the ledger above, and everything shaping
    what comes back is `frame_from_rows`, so both are testable against stored
    parquet with no credentials at all.

    The endpoint is chosen by the device: `get_meter_pro_history` for the Meter
    Pro family, which is the only one carrying CO2, and `get_sensor_history`
    for everything else. They differ in the response as well as the path -- the
    latter has no `co2_ppm` key at all, for any device.
    """
    device = device_for(device)
    client = _client()
    if mac is None:
        mac = find_vendor_device(client, device)["device_mac"]
    call = (client.get_meter_pro_history if device.has_co2
            else client.get_sensor_history)
    rows = call(mac,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"))
    return frame_from_rows(rows, device)


def fetch_ranges(ranges, device=DEFAULT_DEVICE, fetcher=None) -> pd.DataFrame:
    """Fetch each planned range and concatenate.

    `fetcher` is injected so a caller -- or a test -- can drive the whole
    plan/merge/ledger path from stored parquet instead of the private API. It
    is resolved here rather than captured as a default argument, so replacing
    `backfill.fetch` at module level is enough to take the network out of the
    picture entirely.
    """
    device = device_for(device)
    fetcher = fetch if fetcher is None else fetcher
    frames = []
    for start, end in ranges:
        # end + 1 because the vendor's range excludes the final day.
        frame = fetcher(start, end + timedelta(days=1), device)
        if len(frame):
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=list(device.channels))
    combined = pd.concat(frames)
    return combined[~combined.index.duplicated(keep="last")].sort_index()


def merge(history: pd.DataFrame, device=DEFAULT_DEVICE) -> dict:
    """Write history into the day partitions without displacing polled rows.

    Polled readings win on a tie: they were recorded by this pipeline, at a
    known time, whereas a backfilled row has passed through the vendor's cloud
    and the phone's clock.

    `device` selects the tree. Dedup on the timestamp alone stays correct here
    only because one tree holds one device -- with both sensors in a shared
    tree this line would silently drop whichever row lost a collision.
    """
    device = device_for(device)
    written = {}
    for day, chunk in history.groupby(history.index.date):
        path = partition_for(pd.Timestamp(day), device)
        existing = pd.read_parquet(path) if path.exists() else None
        combined = chunk if existing is None else pd.concat([chunk, existing])
        # keep="last" so the polled rows, concatenated second, survive.
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        combined.to_parquet(path)
        written[str(day)] = len(combined) - (0 if existing is None else len(existing))
    return written


def gaps(threshold: float = COVERAGE_THRESHOLD, device=DEFAULT_DEVICE) -> list:
    cov = coverage(device)
    if cov.empty:
        return []
    return [d.date() for d in cov.index[cov["coverage"] < threshold]]


def report_plan(plan: dict, ranges: list, stats: dict, today: date,
                baseline_days: int) -> None:
    """Print the plan, and what it costs against the old fixed window."""
    by_reason = {}
    for day, reason in plan.items():
        by_reason.setdefault(reason, []).append(day)
    for reason, days in sorted(by_reason.items()):
        print(f"  {reason:<14} {len(days):>3} day(s)  {min(days)}..{max(days)}")
    print(f"  {len(ranges)} request(s): "
          + ", ".join(f"{a}..{b}" for a, b in ranges))

    # The comparison the change is judged on: what the old unconditional
    # rolling window would have pulled, against what this plan pulls. Row
    # counts come from the partitions, which for a backfilled day are exactly
    # what the vendor served.
    old_window = [today - timedelta(days=i) for i in range(OLD_WINDOW_DAYS + 1)]
    before = rows_for(old_window, stats)
    after = rows_for(expand(ranges), stats)
    print(f"  ~{after} rows to download, against ~{before} for the old fixed "
          f"{OLD_WINDOW_DAYS}-day window ({after - before:+d})")


def run_device(device, state: dict, args, today: date, fetcher=None) -> int:
    """Plan, fetch, merge and record one device. Mutates its sub-ledger.

    Returns 0 on success and 1 if the vendor served nothing, which is what the
    single-device version returned in the same situations. Exceptions are left
    to the caller, which catches them per device so that one sensor cannot cost
    another its repair.

    `state` is the whole ledger; the sub-ledger is taken from it here so that
    the caller can save the file once for all devices.
    """
    device = device_for(device)
    ledger = state_for(state, device)
    stats = partition_stats(device)
    print(f"== {device.slug}  ({device.label}) -> DATA_DIR/{device.root}")
    swept = False

    if args.all:
        # Deliberately blind to the ledger: --all means "ask for everything",
        # and it is the escape hatch for when the ledger is not to be trusted.
        start, end = earliest_date(device), today
        plan = {start + timedelta(days=i): "all"
                for i in range((end - start).days + 1)}
        print(f"  fetching everything from {start} to {end}")
    elif args.gaps:
        days = gaps(args.threshold, device)
        if not days:
            print(f"  no day below {args.threshold:.0%} coverage; "
                  f"nothing to repair")
            return 0
        plan = {d: "gap" for d in days}
        print(f"  {len(days)} day(s) below {args.threshold:.0%}, "
              f"spanning {min(days)} to {max(days)}")
    else:
        swept = args.sweep or due_sweep(ledger, today)
        plan = plan_days(ledger, today, recent_days=args.days, sweep=swept,
                         gap_days=gaps(args.threshold, device), device=device)
        print(f"  incremental: {len(plan)} day(s) planned"
              f"{'  (periodic re-window due)' if swept else ''}")

    ranges = coalesce(plan)
    report_plan(plan, ranges, stats, today, args.days)

    for day, rows in stale_days(ledger, today, device):
        print(f"  {device.slug}: never came back dense: {day} ({rows} rows). "
              f"Past retention and past the lookback; only --all will retry it",
              file=sys.stderr)

    if args.plan:
        return 0

    requested = expand(ranges)
    history = fetch_ranges(ranges, device, fetcher=fetcher)
    if history.empty:
        print(f"  {device.slug}: the vendor returned no rows. Open the device's "
              f"history screen in the app with Bluetooth connected, then retry.",
              file=sys.stderr)
        # Record the zero rather than leaving a stale dense count in place: a
        # day that comes back empty must not keep looking settled.
        for day in requested:
            record_fetch(ledger, day, 0, 0)
        return 1

    print(f"  fetched {len(history)} rows, "
          f"{history.index.min()} to {history.index.max()}")
    for day, added in sorted(merge(history, device).items()):
        print(f"    {day}  +{added} rows")

    served = {day: (len(chunk), bins_covered(chunk.index))
              for day, chunk in history.groupby(history.index.date)}
    for day in requested:
        rows, bins = served.get(day, (0, 0))
        record_fetch(ledger, day, int(rows), int(bins))
    if swept:
        ledger["last_sweep"] = today.isoformat()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--all", action="store_true",
                        help="re-fetch the whole record, ignoring the ledger")
    parser.add_argument("--gaps", action="store_true",
                        help="fetch only days below the coverage threshold")
    parser.add_argument("--days", type=int, default=RECENT_DAYS,
                        help="days of open tail fetched unconditionally")
    parser.add_argument("--threshold", type=float, default=COVERAGE_THRESHOLD)
    parser.add_argument("--plan", action="store_true",
                        help="show what an incremental run would fetch, no network")
    parser.add_argument("--sweep", action="store_true",
                        help="force the periodic re-window this run")
    parser.add_argument("--device", action="append", metavar="SLUG",
                        help=f"restrict to one sensor (repeatable); "
                             f"default: all of {', '.join(sorted(DEVICES))}")
    parser.add_argument("--mac", metavar="MAC",
                        help="pin the vendor MAC instead of matching by device "
                             "type or name; only valid with a single --device")
    parser.add_argument("--reset-state", action="store_true",
                        help="rebuild the ledger from the partitions on disk")
    parser.add_argument("--migrate-state", action="store_true",
                        help="upgrade the ledger file to v2 (per device), "
                             "keeping a copy of the original")
    parser.add_argument("--revert-state", action="store_true",
                        help="restore the ledger copy --migrate-state kept")
    args = parser.parse_args()

    today = datetime.now(timezone.utc).date()
    devices = [device_for(slug) for slug in (args.device or sorted(DEVICES))]
    if args.mac and len(devices) != 1:
        parser.error("--mac pins one device's MAC, so it needs exactly one "
                     "--device")

    if args.migrate_state:
        return migrate_state()
    if args.revert_state:
        return revert_state()
    if args.reset_state:
        state = seed_state(today, devices=DEVICES.values())
        save_state(state)
        counts = ", ".join(f"{slug}: {len(d['days'])}"
                           for slug, d in sorted(state["devices"].items()))
        print(f"ledger rebuilt from the partitions on disk ({counts}) "
              f"-> {STATE_PATH}")
        return 0

    state = load_state(today=today)
    # A pinned MAC skips device discovery entirely, for the case where the
    # rack sensor's device_type is not one this build recognises.
    fetcher = (None if not args.mac else
               lambda start, end, device: fetch(start, end, device, mac=args.mac))

    failed, empty = [], []
    try:
        for device in devices:
            try:
                if run_device(device, state, args, today, fetcher=fetcher):
                    empty.append(device.slug)
            except Exception as exc:                      # noqa: BLE001
                # One device's failure must not cost another its repair: the
                # CO2 series is the only one that cannot be reconstructed from
                # anything else, and it is no longer the only device here.
                print(f"  {device.slug}: backfill failed -- "
                      f"{type(exc).__name__}: {exc}", file=sys.stderr)
                failed.append(device.slug)
    finally:
        # Whatever was learned before a failure is still worth keeping: the
        # ledger is how a day stays in the plan, and losing it means re-fetching
        # rather than losing data.
        if not args.plan:
            save_state(state)

    if failed:
        return 2
    return 1 if empty else 0


if __name__ == "__main__":
    sys.exit(main())
