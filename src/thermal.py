"""Room temperature as a second observable: an appliance, not a metabolism.

The CO2 channel cannot tell CO2 that is being generated now from CO2 that is
still clearing from last night. Both look like "elevated and falling slowly",
and that one ambiguity is where the model's errors concentrate: the weekday
small hours score 67.8% against a Saturday at the same hours scoring 98.9%,
both confirmed empty. Temperature responds on a different timescale, so if it
carries occupancy information anywhere it should carry it there.

It does, and the mechanism is worth stating plainly because it decides where
any of this generalises.

**This detects an air conditioner being switched on. It does not detect body
heat.** The occupant turns the air conditioning on when they arrive, and a
person is worth about 100 W against a room whose thermal budget is set by a
server rack that never stops. Body heat would make an occupied room *warmer*;
what the record shows is an occupied room 4-5 C *cooler* than its own uncooled
level, for hours at a stretch. So the quantity being tracked is a decision
somebody makes on the way in. That makes it a behavioural proxy rather than a
physical law, with two consequences: it is close to independent of the CO2
channel, which is exactly why it helps where CO2 alone fails; and in a room
where nobody uses air conditioning it would carry no information at all.

**It is therefore seasonal, and that was a prediction rather than a
discovery.** Nobody switches the cooling on in March. Measured against the
confirmed periods, the deficit below the uncooled level separates occupied from
empty with AUC 0.165 (inverted, i.e. 0.835 the right way up) in June-August and
0.561 -- nothing -- in March-May. Worse than nothing, in fact: across the cold
months the cooling power below reads 0.45 over the windows confirmed *empty*
and 0.16 over the window confirmed occupied, the exact inverse of the summer
pattern, because with no compressor running what the observable picks up is the
outdoor diurnal cycle. A channel that is anti-informative over half the record
is a reason to gate hard rather than to average. The channel is gated on
evidence that
cooling is in use rather than on the calendar: `cooling_scale` measures how far
below its ceiling the room actually goes over the surrounding fortnight, and the
gate opens only where that is large. On this record it opens across June-August
and stays shut across March-May, which is the honest form of "air conditioning
season" -- read off the temperature series rather than assumed from the month.

**Running is not the same as recovering.** A room 3 C below its ceiling might be
held there by the compressor, or might be an empty room warming back up from an
evening that ended hours ago. The level alone cannot tell those apart, and the
difference is decisive precisely in the small hours this is aimed at. So the
observable is not the deficit but the departure from *free relaxation*. With
nothing cooling it, a room below its ceiling warms toward it:

    dT/dt = lambda * (T_ceiling - T)

so whatever is left over after subtracting that is heat being removed by
something -- the compressor:

    cooling = -(dT/dt - lambda * (T_ceiling - T))

which is near zero both at the ceiling and all the way up a free recovery, and
large whenever the room is being held down. That is the same move the occupancy
model makes with CO2, one storey down: a mass balance with the driving term
solved for.

The channel cannot count. The compressor is on or off; one person and two
switch it identically, so `presence_logp` returns the same value for N=1 and
N=2 and the counting stays entirely with the CO2 channel.

The known failure is the converse: **air conditioning left running in an empty
room.** It is not hypothetical -- the occupant's own labels for one recorded day
have the room emptying at 14:02 with the unit still set to 26 C, and the
temperature holds flat for the next hour with nobody in it. Two of the six warm
Saturdays in this record look the same way in the small hours, and they are
where this channel does its only damage to the confirmed numbers.
"""
import numpy as np
import pandas as pd

# The sensor quantises to 0.1 C and repeats its last reading between refreshes,
# so 68% of consecutive 5-minute samples are byte-identical and a 30-minute
# median still returns a difference of exactly zero at the median. An hour of
# smoothing and an hour of lag are what it takes to get a rate at all.
SMOOTH = "1h"
RATE_LAG = 6            # samples either side: a centred 1-hour difference

# Neighbourhood for the uncooled ceiling. Narrower in calendar time than
# `baseline.WINDOW_DAYS`, because the uncooled level tracks the weather, which
# moves faster than C_out does.
HOUR_HALFWIDTH = 1.0
WINDOW_DAYS = 3
QUANTILE = 0.90         # high envelope: cooling pushes temperature down, never up
MIN_NEIGHBOURS = 20

# Free-relaxation rate toward the ceiling, in 1/h. Measured as the slope of the
# 90th-percentile envelope of dT/dt against the deficit, forced through the
# origin: 0.31 /h, a time constant of about 3.2 hours, which is an unremarkable
# figure for a room of this size. The envelope rather than the mean because the
# mean mixes free recovery with samples that are still being cooled. Nothing
# here is sensitive to it -- sweeping 0.20 to 0.50 moves confirmed-period
# accuracy by 0.9 points.
LAMBDA = 0.31

# How deep the cooling goes, locally: the 95th percentile of the deficit over a
# fortnight. Taken per day it is bimodal, and the two modes do not touch -- no
# day in this record falls between 4.5 and 5.1 C -- so the gate is placed in
# that gap rather than at a threshold the data straddles. The consequence is
# worth stating as a property rather than as a number: across March-May the
# gate is shut at every single sample, so `joint.decode_thermal` returns
# `states.decode`'s sequence there exactly, and the cold-season figures are
# unchanged by construction rather than by luck.
#
# A wider gate scores marginally better overall (82.6% against 82.0% on the
# confirmed periods) by letting the warmest May days through. It is not taken,
# because the same measurement that says this channel works in summer says it
# is *anti*-correlated with occupancy in spring -- see the module docstring --
# and buying half a point by letting a channel speak where it has been measured
# to point the wrong way is how a model acquires its next broken assumption.
SCALE_WINDOW = "14D"
SCALE_QUANTILE = 0.95
GATE_OFF = 4.5          # below this the channel is silent
GATE_FULL = 5.5         # above this it carries full weight

# Where the evidence changes sign, in units of the local cooling scale. Over the
# gated samples the normalised cooling power is sharply bimodal -- a spike at 0
# (nothing running) and a spike at 1 (compressor holding the room down) -- and
# this is the trough between them, so it is read off the observable's own
# distribution rather than fitted to the validation windows.
CROSSOVER = 0.45


def _smooth(df: pd.DataFrame) -> pd.Series:
    return df["temp"].rolling(SMOOTH, center=True).median()


def ceiling(df: pd.DataFrame) -> pd.Series:
    """The temperature the room reaches with nothing cooling it.

    A high quantile over a neighbourhood of +/- 1 hour of day and +/- 3 days.
    This is the mirror of `baseline.estimate_c_out`: occupancy pushes CO2 *up*,
    so C_out is a low envelope; occupancy pushes temperature *down*, so the
    reference is a high one. It has to be a local envelope rather than a
    constant because the uncooled level itself swings with hour and season.

    The failure mode to avoid is `estimate_c_out`'s in mirror image: at an hour
    of day when the room is *always* cooled, every sample in the neighbourhood
    is depressed and the envelope returns a cooled level. The calendar half of
    the neighbourhood is what saves it -- weekends fall inside the window, and
    the room is not cooled on the same schedule then.
    """
    t = _smooth(df).to_numpy()
    hours = df.index.hour + df.index.minute / 60
    days = (df.index - df.index[0]).days.values

    out = np.full(len(df), np.nan)
    for h in range(24):
        dh = np.abs(hours - h)
        near_hour = np.minimum(dh, 24 - dh) <= HOUR_HALFWIDTH
        target = np.floor(hours) == h
        if not target.any():
            continue
        sub, sub_days = t[near_hour], days[near_hour]
        finite = np.isfinite(sub)
        sub, sub_days = sub[finite], sub_days[finite]
        for d in np.unique(days[target]):
            win = np.abs(sub_days - d) <= WINDOW_DAYS
            if win.sum() < MIN_NEIGHBOURS:
                continue
            out[target & (days == d)] = np.quantile(sub[win], QUANTILE)

    s = pd.Series(out, index=df.index, name="t_ceiling")
    return s.interpolate(limit_direction="both").rolling("3h", center=True).mean()


def deficit(df: pd.DataFrame, t_ceiling: pd.Series = None) -> pd.Series:
    """How far below its own uncooled level the room sits, in C (<= 0)."""
    if t_ceiling is None:
        t_ceiling = ceiling(df)
    return (_smooth(df) - t_ceiling).rename("t_deficit")


def warming_rate(df: pd.DataFrame) -> pd.Series:
    """dT/dt in C/h, as a centred one-hour difference.

    Anything shorter measures the sensor's 0.1 C quantisation rather than the
    room, and the difference is masked wherever the window does not actually
    span an hour, so the multi-week outages produce no rate at all.
    """
    sm = _smooth(df)
    span = (df.index.to_series().shift(-RATE_LAG)
            - df.index.to_series().shift(RATE_LAG)).dt.total_seconds() / 3600
    rate = sm.shift(-RATE_LAG) - sm.shift(RATE_LAG)
    return rate.where((span - 1.0).abs() < 0.05).rename("dT_dt")


def cooling_scale(defi: pd.Series) -> pd.Series:
    """How deep the cooling goes locally -- the channel's own gate.

    This decides whether temperature is allowed to speak, and it is measured
    from the temperature series rather than read off the calendar: a cool
    fortnight in August silences the channel exactly as March does, and a room
    where nobody ever runs the cooling never turns it on at all.
    """
    return ((-defi).rolling(SCALE_WINDOW, center=True)
            .quantile(SCALE_QUANTILE)
            .interpolate(limit_direction="both")
            .rename("cooling_scale"))


def gate(scale: pd.Series) -> np.ndarray:
    """0 where the channel is uninformative, 1 where it is, ramped between."""
    g = (np.asarray(scale, dtype=float) - GATE_OFF) / (GATE_FULL - GATE_OFF)
    return np.clip(np.nan_to_num(g, nan=0.0), 0.0, 1.0)


def cooling_power(df: pd.DataFrame, t_deficit: pd.Series = None,
                  lam: float = LAMBDA) -> pd.Series:
    """Heat being removed beyond free relaxation, as a fraction of full cooling.

    0 both at the ceiling and anywhere along a free recovery -- an empty room
    warming back up from an evening that ended hours ago reads the same as a
    room that was never cooled. 1 where the compressor is holding the room at
    the bottom of its local range. This is the quantity that separates "cooling
    is running" from "cooling ran earlier", which the deficit alone cannot.
    """
    if t_deficit is None:
        t_deficit = deficit(df)
    scale = cooling_scale(t_deficit)
    residual = np.asarray(warming_rate(df)) - lam * (-np.asarray(t_deficit))
    p = -residual / np.maximum(lam * np.asarray(scale), 1e-6)
    # No rate means no evidence, which is 0 rather than a guess either way.
    return pd.Series(np.clip(np.nan_to_num(p, nan=0.0), 0.0, 1.0),
                     index=df.index, name="cooling_power")


def presence_logp(df: pd.DataFrame, states, weight: float = 1.0,
                  t_deficit: pd.Series = None, lam: float = LAMBDA) -> np.ndarray:
    """Log-likelihood of the thermal observation under each occupancy state.

    Shape (len(states), len(df)), to be added to the CO2 channel's own.

    Linear in the cooling power and crossing zero at `CROSSOVER`, bounded by
    `weight` either way. Linear and bounded rather than a likelihood ratio,
    because "cooled" implies "occupied" only as a behavioural regularity -- the
    occupant can be present without touching the switch, and can leave the unit
    running on the way out -- so the channel has to be able to lose an argument
    with the CO2 channel rather than overrule it.

    N=1 and N=2 receive identical values: see the module docstring.
    """
    if t_deficit is None:
        t_deficit = deficit(df)
    g = gate(cooling_scale(t_deficit))
    p = np.asarray(cooling_power(df, t_deficit, lam))

    # >0 favours "someone is here", <0 favours "nobody is".
    evidence = weight * g * (p - CROSSOVER)
    occupied = (np.asarray(states) > 0).astype(float)[:, None]
    return occupied * evidence[None, :] - (1 - occupied) * evidence[None, :]


def channel(df: pd.DataFrame, lam: float = LAMBDA) -> pd.DataFrame:
    """Every intermediate quantity at once, for inspection."""
    c = ceiling(df)
    defi = deficit(df, c)
    scale = cooling_scale(defi)
    return pd.DataFrame({"t_ceiling": c, "t_deficit": defi,
                         "dT_dt": warming_rate(df), "cooling_scale": scale,
                         "gate": gate(scale),
                         "cooling_power": cooling_power(df, defi, lam)},
                        index=df.index)


if __name__ == "__main__":
    from load import load_clean

    data = load_clean()
    ch = channel(data)
    print(f"deficit: median {ch.t_deficit.median():+.2f} C | "
          f"p10 {ch.t_deficit.quantile(.1):+.2f} | p90 {ch.t_deficit.quantile(.9):+.2f}")
    print(f"channel open on {ch.gate.mean():.1%} of samples\n")
    print("by month -- cooling scale, how open the gate is, and the observable:")
    print(ch.groupby(ch.index.to_period("M")).agg(
        scale=("cooling_scale", "median"), gate=("gate", "mean"),
        deficit=("t_deficit", "median"),
        cooling=("cooling_power", "median")).round(2).to_string())

    gated = ch[ch.gate > 0.5]
    counts, edges = np.histogram(gated.cooling_power, bins=np.arange(0, 1.05, 0.05))
    print("\ncooling power where the gate is open -- the bimodality the "
          f"crossover of {CROSSOVER} sits in the trough of:")
    for c, e in zip(counts, edges):
        print(f"  {e:4.2f}  {'#' * int(c / 40):<62s} {c}")
