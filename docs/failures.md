# What broke, and what each failure cost

The main README summarises these. This is the full account.

## Four assumptions that broke, and what each cost

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
