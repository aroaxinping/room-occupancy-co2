# Validation

How this model was checked, including a metric that turned out to be
worthless and had to be withdrawn.

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
