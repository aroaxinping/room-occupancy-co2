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
put 0.42 phantom people in a demonstrably empty room at 4 a.m. The room does not
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
vendor's client formats each point in local wall-clock time with no offset
attached and the ingestion labelled those strings UTC, which reads naturally,
is wrong, and stays invisible until a timestamp lands in the future.

What still stands, because none of it came from the backfill: the sealed rate
of 0.39 from five months of overnight decays, the steady-state inversion above
that put occupied hours near 1.05 ACH, and the window measurement below, which
predates the backfill entirely.

## Validation without labels, and why it does not work

There are no occupancy labels covering the record, so the model was checked
against windows whose occupancy the occupant stated: certain hours on certain
weekdays, held to be empty or to hold one person.

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

Settling it properly needs labels recorded as occupancy changes, which
`src/label.py` collects and which now exist for a handful of hours. A power
calculation on the window approach is decisive: detecting a three-point
difference would need about 193 confirmed Saturdays, and the record holds
eleven. Roughly twenty to thirty labelled days would answer what fifteen
summers of window labels could not.

## A thermal channel, unvalidated

`src/thermal.py` and `joint.decode_thermal` add a second observable: in warm
weather someone arriving switches on the air conditioning, so the room departs
from free thermal relaxation. It is behavioural rather than metabolic -- a
person contributes about 100 W to a room whose budget is dominated by a
continuously running server rack -- and it therefore does not transfer to a
room where nobody uses air conditioning.

**The observable is real. The improvement is not established.** At a matched
hour, comparing a confirmed-occupied weekday against a confirmed-empty Saturday,
the rate of temperature change differs by 2.56 C/h with a day-level permutation
p below 1e-4, and it survives every artefact test put to it. The mechanism made
a prediction before it was tested -- no compressor runs in winter, so the signal
should vanish -- and in cold months the same comparison gives 0.02 C/h, p=0.19.

But the decoder gain reported for it was measured on the broken metric above.
On metrics a calendar prior cannot win, the gain is 1.4 points with a 95%
interval spanning [-7.3, +10.3], and a weight chosen on one half of the record
scores worse on the held-out half. Added to a calendar prior it subtracts two to
three points. The channel is kept, off the default path, as a documented
attempt -- the same status as `joint.py`.

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

## A second channel, and what it actually detects

The CO2 channel cannot separate CO2 being generated now from CO2 still clearing
from last night, and that is where the model's errors concentrate. Room
temperature responds on a different timescale, so `src/thermal.py` adds it as a
second emission term to the same Viterbi decode.

**It detects an air conditioner being switched on. It does not detect body
heat.** An occupied room here runs 4-5 °C *cooler* than its own uncooled level,
not warmer; what is tracked is a decision somebody makes on the way in. So the
observable is not the temperature deficit but the departure from free
relaxation — the same mass-balance move as the occupancy model, one storey down
— and the channel gates itself off where the record shows no cooling in use,
which leaves it silent for half the year. It cannot count: one person and two
switch the compressor identically.

It takes accuracy against the confirmed periods from 72.5% to 82.0%, and the
gain lands where it was aimed: the weekday small hours go from 67.8% correct to
85.3%. It is not free — the confirmed-empty weekend window falls from 98.9% to
90.9%, which is air conditioning left running in an empty room, a failure mode
the occupant's own labels confirm. And being a behavioural proxy rather than a
physical law, it would carry no information at all in a room where nobody uses
cooling.

## Open problems

**The night residual.** 0.34 people in a room known to be empty, in the small
hours. It survived both the `C_out` and the `k` corrections; the thermal
channel above cuts it to 0.16, but by adding an observable rather than by
fixing the estimator. Simulation supplied the mechanism: `estimate_c_out` is
biased low overnight even with occupancy held at zero, because a low quantile
of a neighbourhood spanning a rising signal lands near that neighbourhood's
floor rather than at its centre. That is a property of the estimator, not of
the room, and it has not been fixed.

**Weekday/weekend discrimination fails.** The model's ranking of which days
hold two people contradicts the occupant's own account, and is not fixable by
tuning without overfitting to a verbal account. That is why the next step is
labels rather than parameters.

**Counting is not validated.** The estimate tracks occupancy well in aggregate,
but claiming accuracy at "1 vs 2 people" needs ground truth. That is now being
collected rather than simply absent: `src/label.py` records occupancy state
changes as they happen and the first entries exist, and `src/simulate.py`
integrates the same mass balance forward from a known schedule to measure what
the pipeline recovers. The simulator bounds the error from estimation only — it
assumes the physics the model assumes — and neither source has yet produced
enough hours to put a number on 1 versus 2.

## Repairing the record

The collector is no longer the only source. The vendor's own history is
reachable through a private-API client, so `src/backfill.py` finds days the
poll under-covered and merges what the cloud holds. The first run recovered
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

Retention is finite: the cloud served roughly three months for this account,
not the full five, so the original export remains the only copy of the earliest
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
src/backfill.py     repairing gaps from the vendor's stored history, on a
                    per-device ledger
src/label.py        recording real occupancy, so the estimate can be scored
src/simulate.py     a synthetic room, to measure what the real one cannot
install-scheduler.sh + scheduler.plist.template   launchd agent, the poll
install-backfill.sh + backfill.plist.template     launchd agent, the daily repair
```

`git log --oneline` is worth a look: the history records the assumptions that
broke and the audits that caught them, in the order it happened.

## Reproducing this

`pip install -r requirements.txt`. `src/backfill.py` additionally needs a
private-API client deliberately *not* listed there: it takes an account email
and password rather than a scoped token, so it is worth installing consciously
and pinned to a commit rather than pulled in by a blanket install. Every other
module works without it.

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
