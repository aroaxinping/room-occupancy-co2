# Room Occupancy from CO2

Estimating how many people are in a room from a single CO2 sensor — no camera,
no motion detector, no occupancy labels to train on.

Five months of minute-level readings from a consumer CO2 sensor in a single
room of roughly 60 m³, across 134,659 records and four contiguous blocks.

*The underlying data is not published. An occupancy series is a record of when
a home is empty, so only aggregate statistics and time-of-day patterns appear
here; no dates, and no detail that would locate the room.*

## The idea

A person exhales roughly 18 L of CO2 per hour. In a room of volume `V`
ventilating at `k` air changes per hour, the indoor concentration follows a
mass balance:

```
dC/dt = (G · N · 10⁶) / V  −  k · (C − C_out)
```

Everything but `N` is measured or estimable, so the number of occupants can be
solved for directly:

```
N = (dC/dt + k · (C − C_out)) · V / (G · 10⁶)
```

This is an inverse problem, not a classifier, and that was a choice before it
was a constraint. There were no labels to train on — the project ran for months
without one — but the better reason is what a classifier would learn instead:
fitted to this data it would encode when *this* occupant is usually in *this*
room, and transfer nowhere. The mass balance transfers to any room by measuring
its volume, because its constants are physical quantities rather than weights,
which is also what makes the sensitivity analysis below mean anything — sweeping
a physical constant across its plausible range says something about the world
where sweeping a learned weight would not. (The thermal channel further down is
the exception that marks the boundary: a behavioural proxy, and not portable.)

No labels are fitted, so there is no train/test leakage in the usual sense.
What takes its place is about a dozen hand-set constants — the state-transition
penalty, the quantile defining `C_out`, the bounds on `k`, the cap of two
occupants — and several were chosen while watching what the known-empty periods
did. That is a weaker form of the same risk and it deserves naming rather than
waving away. Two things bound it: those constants have defensible physical
ranges, and the sensitivity analysis sweeps the largest of them across its full
plausible range. The cap of two occupants is ground truth supplied by the
occupant, used as a prior.

Every assumption still has to be earned, and most of the work below is about
which ones did not hold.

## What the data actually looked like

**The 1-minute resolution is fake.** 88.5% of consecutive CO2 readings are
byte-identical; the device reports every ~5 minutes and the export pads the
gaps by repeating the last value. On the raw grid the implied rate of change
reaches 71 ppm/min at the 99th percentile and 1151 ppm/min at worst —
quantisation steps, not air. Everything is resampled to 5 minutes.

**Three channels, not six.** The export ships `abs_hum`, `dew_point` and `vpd`
alongside temperature and humidity. They are not measurements: they reproduce
exactly from temperature and RH via the Magnus equation (r = 0.99999, mean
error 0.018 g/m³). Feeding all six to a model would inject the same information
three times.

**Two multi-week outages** split the record into four clean blocks (29d / 25d /
22d / 21d), which is convenient — it gives an honest temporal split instead of
a leaky random one.

## Four assumptions that broke

**1. Constant outdoor concentration.** Assuming `C_out = 415 ppm` (outdoor air)
put 0.42 phantom people in a demonstrably empty room in the small hours. The room does not
ventilate against the street; it ventilates against the rest of the building.
Fitting `C_out` per decay episode is not identifiable — asymptote and decay rate
trade off over a short window, and the optimiser ran to its bounds on 25% of
episodes. Estimating it instead as a low envelope over a
7-day × time-of-day neighbourhood gives a signal that swings from 620 ppm at
night to 436 ppm mid-afternoon, and cuts the phantom occupancy to 0.28.

**2. A single ventilation rate.** One `k` for five months cannot work: measured
per episode it ranges from 0.3 (sealed) to over 4 (door standing open). A
constant value overestimates removal at night — inventing occupants — and
underestimates it by day, hiding them. This single wrong assumption explained
both failure modes, in the right direction each time. Replacing it with a
per-timestamp `k` moved the occupied-room estimate from 0.50 to 0.88 people
against a known truth of 1.

**3. That the fix was free.** Interpolating the episode estimates pointwise
produced peaks of 16.5 people in a room of this size. `k` multiplies `(C − C_out)`,
so its noise enters multiplicatively. Regularising it into a time-of-day profile
times a fortnightly level — two slow components instead of 500 free values —
keeps the calibration and pulls the maximum from 16.5 down to 5.3. That is
still impossible in a room holding at most two people: the regularisation
bought an order of magnitude, not correctness. A continuous inversion cannot
get to the physical bound on its own, which is the argument for the discrete
decoding below.

**4. That decoding both unknowns jointly would help.** If ventilation and
occupancy are both discrete and both persistent, decoding the pair
`{0,1,2} × {door closed, door open}` in one pass should beat solving them in
sequence. It did not: the weekday estimate fell from 0.88 to 0.25, and
two-person states all but vanished. Given the freedom, the decoder explained
almost everything as a sealed room and concluded nobody was there.

Its reasoning was sound, which is what makes the failure useful. At the sealed
rate of 0.39 ACH, one person would drive the room to ~1300 ppm at equilibrium;
the observed level during working hours is ~855. So one person is *inconsistent*
with a sealed room, and the decoder correctly rejected it.

Inverting that gives the real number. Over 1,565 steady-state samples during
working hours the median excess above `C_out` is 281 ppm, which for one occupant
requires **k = 1.05 ACH**. The room exchanges about its own volume every hour
even with the window shut: it is not sealed, it is strongly coupled to the rest
of the building. The 0.39 ACH episodes are real, but they come from nights when
the room was both empty and closed — a two-level model built from the wrong
half of the distribution was never going to work.

## Ventilation, measured rather than assumed

**This section is currently withdrawn.** The live session it reported was
computed against backfilled timestamps carrying a two-hour offset, so each
window analysed was not the window it was labelled with: an interval recorded
as the occupied morning was in fact the two hours before anyone arrived. The
conclusions may survive re-measurement, but they are not evidence until they
have been recomputed. The cause and the fix are in `src/backfill.py`: the
history it was repaired from formats each point in local wall-clock time with
no offset attached and the ingestion labelled those strings UTC, which reads
naturally, is wrong, and stays invisible until a timestamp lands in the future.
A source now has to declare which convention its timestamps use, and the guard
that caught this one is applied to all of them.

What still stands, because none of it came from the backfill: the sealed rate
of 0.39 from five months of overnight decays, the steady-state inversion above
that put occupied hours near 1.05 ACH, and the window measurement below, which
predates the backfill entirely.

## Validation: the metric that broke, and the first real one

There are no occupancy labels covering most of the record, so the model was
checked against windows whose occupancy the occupant stated: certain hours on
certain days of the week, held to be empty or to hold one person.

**That check is worthless, and every accuracy figure derived from it has been
withdrawn.** The windows are defined as hour-of-day crossed with day-of-week,
so a rule that reproduces them scores perfectly while reading no sensor at all.
Measured on this repository's own definitions:

| | accuracy |
|---|---|
| the model, from CO2 | 72.5% |
| hour of day alone, no sensor | 98.1% |
| the same calendar rule the windows encode | **100.0%** |

A metric a calendar wins outright cannot distinguish a sensor from a timetable.
It was not measuring whether the room's air reveals people; it was measuring
whether the model agrees with the assumptions used to write the windows. Every
specificity, sensitivity and accuracy this README previously reported --
including the improvement claimed for the thermal channel -- rests on it and
none of them survive.

What the windows can still do is what they were originally used for: show that
the estimate is not nonsense. An empty period reading near zero and an occupied
one reading near one is worth knowing. Turning that into a percentage was the
error.

### Scored against real labels, once

`src/label.py` records occupancy changes as they happen, and one day of them now
exists. Aligned to the readings it gives 250 scoreable samples -- 131 with the
room empty, 64 with one person, 55 with two. It is the first time anything here
has been measured against occupancy that was written down rather than argued
for.

| | |
|---|---|
| specificity, empty called empty | 87.0% (114/131) |
| sensitivity, occupied called occupied | 79.0% (94/119) |
| accuracy, present vs absent | 83.2% |
| accuracy, exact count over {0, 1, 2} | 78.4% |

Broken out by true occupancy, which the windows could not test at all:

| truth | called correctly | where the rest go |
|---|---|---|
| 0 | 114 / 131 (87.0%) | 4 read as 1, 13 as 2 |
| 1 | 59 / 64 (92.2%) | 5 read as 0 |
| 2 | 23 / 55 (41.8%) | 20 read as 0, 12 as 1 |

Two people is where it breaks, and the errors do not land next door at one
person: twenty of the fifty-five read the room as empty.

**This is one day.** One day, one household, one season, and the truth is the
occupant's own account of their own movements written down as it happened.
Nothing generalises from it. What it does is replace a metric that could not be
lost with one that can be.

It does not yet replace it with a metric a calendar clearly loses. A rule reading
only hour and day-of-week scores 76.4% on these same samples against the model's
83.2% -- but the day falls inside the one reference period the occupant recorded
as genuinely variable, and reading that period as occupied rather than empty
takes the same calendar rule to 86.0%, ahead of the model. Which reading is the
fair one is not something a single day can settle. The claim here is that the
model is finally being scored against something real, not that it has beaten
anything.

### What would settle it

More labels, and not many. The window approach cannot be rescued by patience.
On the reference period confirmed empty the model scores 1.00 on most days and
collapses on a few, so per-day accuracy across the 21 such days in the record has
a standard deviation of 0.22; comparing two model variants day-paired gives a
difference with a standard deviation of 0.33. Detecting a three-point difference
at 80% power therefore needs on the order of a thousand such days. The record
holds 21, and accumulates them at roughly fifty a year.

One labelled day produced 250 scoreable samples spanning all three states,
including 55 with two people -- of which five months of window labels contain
exactly none. Twenty to thirty labelled days would answer in a month what the
window approach could not answer in twenty years.

## Sensitivity to the generation rate

`G`, the CO2 exhaled per person, is the largest unquantified input. It depends
on sex, age, body mass and activity level (Persily & de Jonge, 2017). Sweeping
it across the full plausible adult range:

| Occupant profile | G (L/h) | Weekday working (true 1) | Night (true 0) |
|---|---|---|---|
| Adult female, sedentary | 13 | 1.17 | 0.66 |
| Adult female, light activity | 16 | 1.02 | 0.42 |
| Adult male, sedentary | 17 | 0.93 | 0.39 |
| Generic (used here) | 18 | 0.88 | 0.34 |
| Adult male, light activity | 21 | 0.83 | 0.22 |
| Moderate activity | 26 | 0.63 | 0.19 |

The qualitative conclusion holds across the whole range — an empty room reads
0.19-0.66 and an occupied one 0.63-1.17, and the two never cross — so the
ordering is robust even though the precision is not.

`G` is no longer only an assumption. With ventilation measured independently —
a decay in a room known to be empty, where the equation does not contain `G` at
all — an occupancy step of one person raises the equilibrium by a predictable
amount, and inverting that gives **17 +/- 3 L/h** for this room's occupant
against the 18 the model assumes.

For that comparison to mean anything the predicted side has to be right too, so
`src/generation.py` was checked against its source — and the check found two
errors in this repository's own derivation. It used 20.9 kJ per litre of O2, the
energy equivalent at a respiratory quotient near 0.96, while applying RQ = 0.85
two lines later: a uniform **2.7% underestimate**. Separately, `steady_state_ppm`
compared rates published at 273 K against a room volume at room temperature,
understating concentrations by a further **7.4%**. The module is now the paper's
Equation 8, `tests/test_generation.py` holds 43 tests asserting published values
including every adult cell of its Table 4, and `docs/generation-rate.md` records
the comparison. At room temperature it gives 14-21 L/h for an adult at desk work
across 55-95 kg, which brackets both the assumed 18 and the measured 17 +/- 3.

None of that moves the numbers above: `src/generation.py` is not in the
inversion path — `src/occupancy.py` and `src/states.py` carry `G` as their own
constant. What changed is that the figure the measurement is checked against is
now traceable to a published source rather than to a derivation this repository
did itself and got wrong.

The inverse question is settled in the negative, and now quantitatively.
Running this model backwards to infer an occupant's sex or body mass cannot
work: holding the person fixed and varying activity spans a factor of **2.00**,
while holding activity fixed and varying sex and body mass across 55-95 kg spans
**1.52**. The confounder is larger than the signal, and the measured 17 +/- 3
L/h is consistent with twenty-one different combinations of sex, mass and
activity. This is the same identifiability problem as `k` versus `N`, and no
amount of extra data from this one sensor resolves it.

## A second channel: four attempts, and why each failed

The CO2 channel cannot separate CO2 being generated now from CO2 still clearing
from hours ago, and that is where its errors concentrate. Temperature responds
on a different timescale, so it is the obvious place to look for a second
observable. Four ways of using it have been tried. None of them is load-bearing
now, and the reasons are physical rather than a matter of tuning.

**1. Temperature as body heat. Dead on the room's thermal budget.** A person
adds about 100 W. The room also contains a server rack that never stops, and a
second sensor placed inside it holds a steady 1.5-2.1 C above the room, month
after month, with a standard deviation under half a degree. A continuous load
that stable sets the budget; one occupant is a perturbation on it. The labelled
day settles the direction outright: the room reads 26.0 C while occupied and
29.3 C while empty. Occupancy here makes the room *colder*, not warmer.

**2. Temperature as air conditioning. The observable is real; the gain is not
established.** It is colder because somebody switches the cooling on when they
arrive, so what temperature tracks is a decision rather than a metabolism. The
observable itself holds up: at a matched hour, comparing a reference period
confirmed occupied against one confirmed empty, the rate of temperature change
differs by 2.56 C/h with a day-level permutation p below 1e-4, and it survives
every artefact test put to it. The mechanism predicted its own limit before it
was tested -- no compressor runs in the cold months, so the signal should vanish
-- and there the same comparison gives 0.02 C/h, p=0.19. So `src/thermal.py`
tracks the departure from free thermal relaxation rather than the temperature
itself, the same mass-balance move as the occupancy model one storey down, and
gates itself off wherever the record shows no cooling in use.

What is not established is that any of it helps the decoder. The improvement
once claimed for this channel was measured on the broken metric above and is
withdrawn with the rest. On metrics a calendar prior cannot win, the gain is 1.4
points with a 95% interval spanning [-7.3, +10.3]; a weight chosen on one half
of the record scores worse on the held-out half; and added to a calendar prior
it subtracts two to three points. The channel cannot count in any case -- one
person and two switch the compressor identically -- and being behavioural rather
than physical it would carry nothing at all in a room where nobody uses cooling.
Its known failure mode is cooling left running in an empty room, which the
occupant's own labels confirm happens. It is kept off the default path as a
documented attempt, the same status as `joint.py`.

**3. The rack-minus-room difference as a ventilation measure. Dead on the rack's
own fans.** If the rack is a constant heat source, the difference between it and
the room should widen and narrow as the room's air exchange does, which would
measure `k` directly and break the identifiability problem the whole model runs
on. It does not. The difference that makes attempt 1 fail is exactly what makes
this fail too: it is near-constant. Across hour of day its median spans 1.70 to
1.90 C in total, while the ventilation rate over those same hours varies by
severalfold. It correlates -0.15 with CO2, -0.10 with CO2 excess, and +0.22 with
the estimated ventilation rate -- the largest of the three and still far too weak
to invert. The physical reason is that the rack's fans dominate its heat exchange
with the room: it is cooled by a forced draught that does not care what the
window is doing. The rack sensor is worth keeping as a reference for room
temperature; it is not a ventilation instrument.

*The earliest rows of that sensor's record are excluded from all of the above.*
They sit roughly 8 C *below* the room rather than above it, which is not how a
sensor inside a rack behaves.

**4. Solving `C_out`, `k` and `N` together by alternation. Dead on every period
that has an expected value.** Not a thermal attempt, but it failed the same way:
plausible in principle, worse in measurement. Estimating `C_out` from
believed-empty samples, solving `k` from the mass balance given the current
occupancy, decoding, and repeating is `states.solve`, and it moves every
reference period away from its known value -- the two confirmed empty rise from
0.07 and 0.31 toward 0.15 and 0.40, and the one confirmed to hold a person falls
from 0.89 to 0.79. `states.decode` is what runs by default for that reason.

## Open problems

**Counting two people. 41.8% correct**, on the one labelled day above, and the
errors do not go to one person -- twenty of the fifty-five two-person samples
read as an empty room. The decoder does not know it is guessing, either: the
margin between its chosen state and its runner-up is *higher* where the truth is
two (median 0.27) than where it is nought or one (0.15, 0.12), and the
lowest-margin third of samples is 19.3% two-person against a 22.0% base rate. So
there is no doubt signal to threshold on and no cheap abstention to build. The
model is confidently wrong, which is the expensive kind.

**The night plateau.** The room does not come back down to its own estimated
asymptote overnight: across the small hours the median reading is about 740 ppm
against an estimated `C_out` near 615, which the inversion reads as roughly a
third of a person in a room known to be empty. It survived both the `C_out` and
the `k` corrections. Simulation supplies one mechanism -- `estimate_c_out` is
biased low overnight even with occupancy held at zero, because a low quantile of
a neighbourhood spanning a rising signal lands near that neighbourhood's floor
rather than at its centre -- but that is a property of the estimator, and whether
it accounts for the whole plateau has not been established. Nothing here rules
out a real, unattributed source in the room.

**Both are the same problem.** A slow rise is equally well explained by more
people or less ventilation, and the CO2 series alone cannot say which: `k` and
`N` are not separately identifiable from it. That is what makes two people look
like an empty room with the door shut, and an empty room look like a person who
never left. Every attempt above -- the joint decode, the alternating solver, the
thermal channel, the rack delta -- was an attempt to bring in something outside
the CO2 series to break it, and none of them did. It is the same shape as the
generation-rate result further up, where activity and body mass could not be
separated either.

**Day-type discrimination fails.** The model's ranking of which days hold two
people contradicts the occupant's own account, and is not fixable by tuning
without overfitting to a verbal account. That is why the next step is labels
rather than parameters.

`src/simulate.py` integrates the same mass balance forward from a known schedule
to measure what the pipeline recovers, and bounds the error from estimation
alone -- it assumes the physics the model assumes, so it cannot see any of this.

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

## Reading path

The notebooks are the short version, in order, with figures:

1. [`notebooks/01_signal.ipynb`](notebooks/01_signal.ipynb) — what the sensor
   actually gives you, and why the file's 1-minute resolution is fiction
2. [`notebooks/02_ventilation.ipynb`](notebooks/02_ventilation.ipynb) —
   recovering the air exchange rate from the decay curves alone
3. [`notebooks/03_occupancy.ipynb`](notebooks/03_occupancy.ipynb) — the
   inversion, why it is ill-posed, and the discrete decoding that fixes it

```
src/load.py         parsing, cleaning, 5-min resampling, block detection
src/baseline.py     time-varying C_out as a low envelope
src/ventilation.py  per-episode decay fits, regularised k(t)
src/occupancy.py    mass-balance inversion and episode detection
src/states.py       Viterbi decoding over {0, 1, 2} occupants  <- the model that works
src/thermal.py      temperature as a presence channel: an appliance, not a body
src/joint.py        the joint occupancy x ventilation decode (documented failure),
                    and the CO2-plus-thermal decode that replaced it
src/generation.py   CO2 output per person from body size and activity
```

`docs/generation-rate.md` records how `src/generation.py` was checked against
the paper it implements, and what that check found.

And the collection side:

```
src/ingest.py       vendor API polling, credentials from the Keychain
src/pipeline.py     scheduled poll, one partition tree per device, staleness
                    and bin coverage
src/backfill.py     repairing gaps from stored history, on a per-device ledger
src/history_source.py   what a history source has to answer, and how one is
                    configured
src/label.py        recording real occupancy, so the estimate can be scored
src/simulate.py     a synthetic room, to measure what the real one cannot
install-scheduler.sh + scheduler.plist.template   launchd agent, the poll
install-backfill.sh + backfill.plist.template     launchd agent, the daily repair
```

`git log --oneline` is worth a look: the history records the assumptions that
broke and the audits that caught them, in the order it happened.

## Reproducing this

`pip install -r requirements.txt`, and that is all of it: nothing here depends
on a client for any particular sensor service. `src/backfill.py` repairs gaps
from whatever history source is configured for it; `src/history_source.py` says
what such a source has to answer and where the configuration lives, and a
directory of exported CSV is one of them, built in. Everything except the
repair runs with no source configured at all.

The sensor readings are not in the repository and cannot be, so the notebooks
need a directory of their own data; `src/paths.py` says where it is looked for,
and `ROOM_OCCUPANCY_DATA` overrides it. Everything here runs against any CO2
series with the same columns.

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

## References

Persily, A., & de Jonge, L. (2017). Carbon dioxide generation rates for
building occupants. *Indoor Air*, 27(5), 868-879.
https://doi.org/10.1111/ina.12383 — CO2 output as a function of occupant sex,
age, body mass and activity, superseding the decades-old fixed rates. Source of
the generation-rate range swept in the sensitivity analysis above.
