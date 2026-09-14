# Collecting the data, and not publishing it

## The collector

The vendor API returns present state only: there is no historical endpoint, so
the record exists at all only because something keeps polling. `src/pipeline.py`
runs every five minutes under launchd — chosen over cron because it survives
reboots and runs a missed job once the machine wakes — writing day-partitioned
Parquet that a re-run deduplicates rather than corrupts. Five minutes because
that is roughly the sensor's own refresh rate, and it spends 288 of the 10,000
API calls allowed per day. There are two sensors in the room now, and each gets
its own partition tree rather than a shared one with a device column, for
reasons `src/pipeline.py` sets out.

It also answers for itself:

```
$ python3 src/pipeline.py --status
days collected   : 92
median coverage  : 100.0%
last reading     : 1 min ago
[rack] not collecting yet (no partitions under DATA_DIR/live-rack)
```

`--status` needs no network and no deviceId, and exits non-zero when a
collector's newest reading is over an hour old. That check exists because a
hole is indistinguishable from an empty room unless something writes down that
no reading arrived — which is how the two 26-day holes above went unnoticed.

## Repairing the record

The collector is no longer the only source. Stored history exists elsewhere --
an export, a database, an account that keeps readings the poll never saw -- so
`src/backfill.py` finds days the poll under-covered and merges what that store
holds. Which store is configuration rather than an import: `src/history_source.py`
states the contract -- rows of timestamp, temperature, humidity and CO2 for one
device over a date range, plus the timezone convention those timestamps use --
and resolves the source at run time from a file beside the data. The first run
recovered
124,363 rows at one-minute resolution with no gap longer than six hours across
three months — including both multi-week holes the original export had, which
had been treated as permanent since the start of the project.

That repair now runs daily, and the interesting part is what it declines to
fetch. The obvious way to make it incremental is a watermark over the newest
timestamp seen, and here that is not merely wasteful but silently destructive.
The cloud holds only what the phone has uploaded, and that upload happens when
someone opens the device's history screen with Bluetooth in range, so a day is
routinely sparse when first asked for and complete later. A watermark advances
past such a day on the strength of the clock alone and never looks at it again
— which is exactly the shape of the two 26-day holes this project has been
repairing since the start.

So what is tracked is a per-day, per-device ledger, and a day leaves the fetch
plan only on evidence about the data itself: it came back with 99% of its 288
five-minute bins covered, or it came back the same size twice running while
already at least half dense. Any growth resets that streak, and neither door can
be opened by time passing. A weekly re-window ignores the ledger across the
preceding ten days, so a day wrongly retired stays retired for at most a week.
An ordinary run asks for about **2,200 rows against about 6,300** for the old
fixed window; the re-window is what that safety margin costs, and every run
prints both figures. `src/backfill.py` argues the whole policy through, case by
case.

Retention is finite: the source used here held roughly three months, not the
full five, so the original export remains the only copy of the earliest
period. The poll also stays primary, because it depends on nothing but this
machine. But an hour the collector misses is now repairable rather than lost,
which was the architecture's weakest assumption.

## What is published, and what is not

The finding is published; the record is not. Those are different things, and
keeping them apart is a deliberate decision rather than a gap.

An aggregate — "the reference period reads 0.01 occupants" — describes a
tendency over five months. A dated series states verifiable facts about one
address: not "this room tends to be empty on some mornings" but that it stood
empty across a specific stretch of a specific week. Only the dated form carries
the irregular multi-day absences, which averaging removes; only it can be
refitted to predict future ones; and only it is checkable against a calendar.
The distinction is one of degree rather than kind — a time-of-day profile is not
zero-risk either — but the degree is large, and honouring it costs the analysis
nothing. Every result here is reproducible by anyone with their own sensor, and
the one artefact that cannot be shown — a list of dated occupancy events — was a
validation aid rather than a finding. It constrains publication, not processing:
the pipeline works on full-resolution timestamps because the physics requires
them, and those files simply never leave the machine.

So the rule throughout this repository, notebooks included:

> **Aggregates and distributions are published. Anything indexed by a real date
> is not.** Time series appear on a relative axis — "a representative 7-day
> window" — because what persuades in such a plot is the shape of the curve,
> not which week it came from.

## How that is enforced

The repository is arranged so that publishing the record cannot happen by
accident:

- **Data lives outside the repository tree** (`src/paths.py`). A `.gitignore`
  entry is a rule someone can edit or override with `git add -f`; keeping the
  files out of the tree makes committing them impossible rather than merely
  discouraged. `.gitignore` covers `data/` and every tabular extension as a
  second layer.
- **Credentials live in the macOS Keychain**, read at call time by
  `src/ingest.py`. There is deliberately no environment-variable fallback: an
  `export` writes the token into shell history in plaintext, permanently. A
  vendor API token is account-wide rather than scoped to one sensor.
- **A pre-commit hook** (`.githooks/pre-commit`, enabled with
  `git config core.hooksPath .githooks`) refuses commits containing credential
  -shaped filenames, tabular data, long opaque strings, or a literal calendar
  date in source.
- **Validation windows are not in the source.** Naming the hours behind each
  reference period would publish by another route what the aggregates are
  careful not to, so `src/states.py` reads them from an uncommitted file and
  reports them as `period_a`...`period_e`.
- **Figures go through `src/figures.py`**, which discards the index rather than
  drawing a date axis, refuses any caption naming a date or weekday, and will
  only plot an allowlisted quantity against time. The guarantee stops at the
  module boundary — a caller holding its own axes can draw what it likes — so
  the same hook scans rendered notebook outputs for dates as a backstop, and a
  stray `df.head()` cannot slip through. The rule lives in the plotting code
  rather than in the memory of whoever writes the next notebook.
