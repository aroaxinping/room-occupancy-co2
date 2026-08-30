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

This is an inverse problem, not a classifier: no occupancy labels are fitted, so
there is no train/test leakage in the usual sense. What takes its place is about
a dozen hand-set constants — the state-transition penalty, the quantile defining
`C_out`, the bounds on `k`, the cap of two occupants — and several were chosen
while watching what the known-empty periods did. That is a weaker form of the
same risk and it deserves naming rather than waving away. Two things bound it:
the constants are physical quantities with defensible ranges rather than free
weights, and the sensitivity analysis below sweeps the largest of them across
its full plausible range. The cap of two occupants is ground truth supplied by
the occupant, used as a prior.

Every assumption still has to be earned, and most of the work below is about
which ones did not hold.

## What the data actually looked like

**The 1-minute resolution is fake.** 88.5% of consecutive readings are byte
-identical; the device reports every ~8 minutes (median) and the export pads the gaps
by repeating the last value. On the raw grid the implied rate of
change reaches 82 ppm/min at the 99th percentile and 1151 ppm/min at worst --
quantisation steps, not air. Everything is
resampled to 5 minutes.

**Three channels, not six.** The export ships `abs_humidity`, `dew_point` and
`vpd` alongside temperature and humidity. They are not measurements: they
reproduce exactly from temperature and RH via the Magnus equation (r = 0.99999,
mean error 0.018 g/m³). Feeding all six to a model would inject the same
information three times.

**Two multi-week outages** split the record into four clean blocks (29d / 25d /
22d / 21d), which is convenient — it gives an honest temporal split instead of a
leaky random one.

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
per episode it ranges from 0.3 (sealed) to over 4 (window open). A constant
value overestimates removal at night — inventing occupants — and underestimates
it by day, hiding them. This single wrong assumption explained both failure
modes, in the right direction each time. Replacing it with a per-timestamp `k`
moved the occupied-room estimate from 0.50 to 0.88 people against a known truth
of 1.

**3. That the fix was free.** Interpolating the episode estimates pointwise
produced peaks of 16.5 people in a room of this size. `k` multiplies `(C − C_out)`,
so its noise enters multiplicatively. Regularising it into a time-of-day profile
times a fortnightly level — two slow components instead of 500 free values —
keeps the calibration and pulls the maximum from 16.5 down to 5.3. That is
still impossible in a room holding at most two people: the regularisation
bought an order of magnitude, not correctness. What imposes the physical bound
is the discrete decoding below, and the fact that a continuous inversion cannot
get there on its own is the argument for it.

**4. That decoding both unknowns jointly would help.** If ventilation and
occupancy are both discrete and both persistent, decoding the pair
`{0,1,2} × {door closed, door open}` in one pass should beat solving them in
sequence. It did not: the weekday estimate fell from 0.88 to 0.33, and
two-person states all but vanished. Given the freedom, the decoder explained
almost everything as a sealed room and concluded nobody was there.

Its reasoning was sound, which is what makes the failure useful. At the sealed
rate of 0.39 ACH, one person would drive the room to ~1300 ppm at equilibrium;
the observed level during working hours is ~840. So one person is *inconsistent*
with a sealed room, and the decoder correctly rejected it.

Inverting that gives the real number. Over 1,565 steady-state samples during
working hours the median excess above `C_out` is 281 ppm, which for one occupant
requires **k = 1.05 ACH**. The room exchanges about its own volume every hour
even with the window shut -- it is not sealed, it is strongly coupled to the
rest of the building. The 0.39 ACH episodes are real but they come from nights
when the room was both empty and closed, and they do not describe occupied
hours. A two-level ventilation model built from the wrong half of the
distribution was never going to work.

## Validation without labels

There is no ground truth for occupancy, so the model is checked against periods
whose occupancy is known independently:

| Period | Known | Estimated |
|---|---|---|
| Reference period A | empty | **0.11** |
| Reference period B | empty | 0.34 |
| Reference period C | 1 person | **0.88** |
| Reference period D | 0–2, varies | 0.69 |
| Reference period E | 1–2 people | **1.07** (mean 1.58) |

Every period the model is checked against is listed. Period B is the weakest
result: a window known to be empty that reads a third of a person, and the
reason the night residual is an open problem below rather than a solved one.
Period D is the one with no fixed answer -- occupancy there genuinely varies --
so it constrains the model only loosely.

The ventilation estimate reproduces a daily routine nobody supplied: the median
`k` runs from 0.63 at 04:00 to 1.05 in the early afternoon, recovered purely
from the shape of the decay curves.

The bands were then anchored to a measurement instead of to assumption. The room
has a single window configuration, so an hour of live readings with occupancy
known to be two pins it: **1.62 +/- 0.22 ACH**, against 0.39 sealed. The window
ventilates, by a factor of four -- but it cannot reach the 3+ band, so those
episodes are the door.

That number took two attempts. The first, from six readings, gave 0.8 ACH and
the conclusion that the window barely ventilated at all; the apparent plateau it
rested on was sensor quantisation, and CO2 kept falling for another half hour.
Both the estimate and the conclusion drawn from it were wrong, and thirteen
readings overturned them. It is recorded here because the failure mode is the
interesting part: a six-point fit reported an error bar of +/- 1.19 on a value
of 2.62, which is the fit saying it does not know, and the plateau was read as
signal anyway.

## Sensitivity to the generation rate

`G`, the CO2 exhaled per person, is the largest unquantified input. It depends
on sex, age, body mass and activity level (Persily & de Jonge, 2017). Sweeping
it across the full plausible adult range:

| Occupant profile | G (L/h) | Weekday working (true 1) | Night (true 0) |
|---|---|---|---|
| Adult female, sedentary | 13 | 1.17 | 0.65 |
| Adult female, light activity | 16 | 1.01 | 0.42 |
| Generic (used here) | 18 | 0.88 | 0.34 |
| Adult male, sedentary | 17 | 0.92 | 0.39 |
| Adult male, light activity | 21 | 0.83 | 0.22 |
| Moderate activity | 26 | 0.63 | 0.19 |

Two things follow. The qualitative conclusions hold across the whole range --
an empty room reads 0.07-0.25 and an occupied one 0.63-1.17 -- so the ordering
is robust even though the precision is not.

And the inverse question is settled in the negative. Running this model backwards
to infer an occupant's sex or body mass cannot work: a sedentary woman and a
lightly active man produce near-identical readings, because activity level
swings CO2 output by ~100% while sex and body mass account for 20-35%. The
confounder is larger than the signal. This is the same identifiability problem
as `k` versus `N`, and no amount of extra data from this one sensor resolves it.

## Open problems

**The night residual.** 0.26 people at 4 a.m. in a room known to be empty. It
survived both the `C_out` and the `k` corrections, so it is not ventilation.
Current hypothesis: CO2-laden air from the rest of the home, which the mass
balance cannot distinguish from a person.

**Weekday/weekend discrimination fails.** The model's ranking of which days hold two
people contradicts the occupant's own account. This is not fixable by tuning without overfitting to a verbal account,
and is the reason the next step is labels rather than parameters.

**Counting is not validated.** The estimate is a continuous quantity that
tracks occupancy well in aggregate. Claiming accuracy at "1 vs 2 people" needs
ground truth that does not yet exist.

## The collector

The vendor API returns present state only: there is no historical endpoint, so
the record exists at all only because something keeps polling. `src/pipeline.py`
runs every five minutes under launchd -- chosen over cron because it survives
reboots and runs a missed job once the machine wakes -- writing day-partitioned
Parquet that a re-run deduplicates rather than corrupts. Five minutes because
that is roughly the sensor's own refresh rate, and it spends 288 of the 10,000
API calls allowed per day.

It also answers for itself:

```
$ python3 src/pipeline.py <deviceId> --status
days collected   : 1
median coverage  : 0.3%
last reading     : 1 min ago
```

`--status` exits non-zero when the newest reading is over an hour old. That
check exists because the original five months contain two silent 26-day holes,
which is what a scheduled job failing unnoticed looks like after the fact -- and
a hole is indistinguishable from an empty room unless something writes down
that no reading arrived.

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
src/joint.py        joint occupancy x ventilation decoding (documented failure)
```

And the collection side:

```
src/ingest.py       SwitchBot API polling, credentials from the Keychain
src/pipeline.py     scheduled poll, partitioned writes, staleness report
src/label.py        recording real occupancy, so the estimate can be scored
install-scheduler.sh + scheduler.plist.template   launchd agent
```

`git log --oneline` is worth a look: the history records the assumptions that
broke and the audits that caught them, in the order it happened.

## Reproducing this

`pip install -r requirements.txt`. The sensor readings are not in the
repository and cannot be, so the notebooks need a directory of their own data;
`src/paths.py` says where it is looked for, and `ROOM_OCCUPANCY_DATA` overrides
it. Everything here runs against any CO2 series with the same columns.

## What is published, and what is not

The finding is published; the record is not. Those are different things, and
keeping them apart is a deliberate decision rather than a gap.

An aggregate -- "the reference period reads 0.11 occupants" -- describes a
tendency over five months. A dated series states verifiable facts about one
address: not "this room tends to be empty on some mornings" but that it stood
empty across a specific stretch of a specific week. Only the dated form carries
the irregular multi-day absences, which averaging removes; only it can be
refitted to predict future ones; and only it is checkable against a calendar.
The distinction is one of degree rather than kind -- a time-of-day profile is
not zero-risk either -- but the degree is large, and honouring it costs the
analysis nothing.

So the rule throughout this repository, notebooks included:

> **Aggregates and distributions are published. Anything indexed by a real date
> is not.** Time series appear on a relative axis -- "a representative 7-day
> window" -- because what persuades in such a plot is the shape of the curve,
> not which week it came from.

Nothing of analytical value is lost to this. Every result here is reproducible
from the code by anyone with their own sensor, and the one artefact that cannot
be shown -- a list of dated occupancy events -- was a validation aid rather
than a finding.

Note that this constrains publication, not processing. The pipeline works on
full-resolution timestamps because the physics requires them; those files
simply never leave the machine.

## How that is enforced

The repository is arranged so that publishing the record cannot happen by
accident:

- **Data lives outside the repository tree** (`src/paths.py`). A `.gitignore`
  entry is a rule someone can edit or override with `git add -f`; keeping the
  files out of the tree makes committing them impossible rather than merely
  discouraged. `.gitignore` still covers `data/` and every tabular extension as
  a second layer.
- **Credentials live in the macOS Keychain**, read at call time by
  `src/ingest.py`. There is deliberately no environment-variable fallback: an
  `export` writes the token into shell history in plaintext, permanently. A
  vendor API token is account-wide rather than scoped to one sensor, so it is
  treated as a high-value credential rather than as project configuration.
- **A pre-commit hook** (`.githooks/pre-commit`, enabled with
  `git config core.hooksPath .githooks`) refuses commits containing credential
  -shaped filenames, tabular data, or long opaque strings.
- **Validation windows are not in the source.** Naming the hours behind each
  reference period would publish by another route what the aggregates are
  careful not to, so `src/states.py` reads them from an uncommitted file and
  reports them as `period_a`...`period_e`.
- **Figures go through `src/figures.py`**, which discards the index rather than
  drawing a date axis, refuses any caption naming a date or weekday, and will
  only plot an allowlisted quantity against time. The guarantee stops at the
  module boundary -- a caller holding its own axes can draw what it likes -- so
  the notebook hook backs it up. The rule lives in the plotting code rather
  than in the memory of whoever writes the next notebook.
- **The pre-commit hook scans notebook outputs for dates** and refuses the
  commit if it finds any, so a stray `df.head()` cannot slip through.

## References

Persily, A., & de Jonge, L. (2017). Carbon dioxide generation rates for
building occupants. *Indoor Air*, 27(5), 868-879.
https://doi.org/10.1111/ina.12383 -- CO2 output as a function of occupant sex,
age, body mass and activity, superseding the decades-old fixed rates. Source of
the generation-rate range used in the sensitivity analysis above.
