"""Estimate the room's CO2 asymptote (C_out) as a time-varying signal.

A constant outdoor value (415 ppm) biases the occupancy model: the room does
not relax toward the street, it relaxes toward whatever air surrounds it, and
that changes through the day (window open at noon, sealed at night) and across
the season. Fitting C_out per decay episode is not identifiable -- asymptote
and decay rate trade off against each other over a short window -- so it is
estimated instead from a local neighbourhood in both time-of-day and calendar
time.

Two ways of doing that live here.

`estimate_c_out` is the original: a low quantile of the neighbourhood. It has
no occupancy model, so it is what runs on the first pass, but the simulator
shows it is biased in two separate ways. Where the room is reliably occupied at
a given hour of day, every sample in the neighbourhood is elevated and the
quantile returns an occupied level -- +85 to +160 ppm through the working
afternoon, which the inversion then subtracts back out of the occupancy signal.
And with the room empty all night the estimate is biased the other way, 20-35
ppm *low*, because a low quantile of a neighbourhood spanning a rising signal
lands near the low end of that neighbourhood rather than at its centre.

`estimate_c_out_masked` addresses both. Given a mask of samples believed to be
unoccupied it takes the *median* of those samples -- unbiased for a smooth
signal, where a low quantile is not -- and interpolates across the hours of day
that are never unoccupied. It needs an occupancy estimate to build that mask,
so it runs inside the EM loop in `states.py` rather than on its own.
"""
import numpy as np
import pandas as pd

WINDOW_DAYS = 7      # calendar neighbourhood
HOUR_HALFWIDTH = 1   # +/- 1 hour of day
QUANTILE = 0.10      # low envelope: occupancy pushes CO2 up, never down

# The masked estimator needs this many believed-empty samples in a
# neighbourhood before it will trust one; below it the hour is left blank and
# filled by interpolation across the diurnal curve instead.
MIN_CLEAN_SAMPLES = 12
# Widen the calendar neighbourhood when the mask is sparse rather than giving
# up on an hour: C_out drifts slowly, so a month of empty samples at 03:00 is
# a better estimate than none at all.
MASKED_WINDOW_DAYS = (7, 14, 28)


def estimate_c_out(df: pd.DataFrame) -> pd.Series:
    co2 = df["co2"]
    hours = df.index.hour + df.index.minute / 60
    days = (df.index - df.index[0]).days.values

    out = np.full(len(df), np.nan)
    for h in range(24):
        # Circular distance in hour-of-day so 23h and 00h are neighbours.
        dh = np.abs(hours - h)
        near_hour = np.minimum(dh, 24 - dh) <= HOUR_HALFWIDTH
        target = (np.floor(hours) == h)
        if not target.any():
            continue
        sub = co2[near_hour]
        sub_days = days[near_hour]
        for d in np.unique(days[target]):
            win = np.abs(sub_days - d) <= WINDOW_DAYS
            if win.sum() < 20:
                continue
            out[target & (days == d)] = np.quantile(sub[win], QUANTILE)

    s = pd.Series(out, index=df.index, name="c_out")
    # Smooth the hour-to-hour steps into a continuous signal.
    return s.interpolate(limit_direction="both").rolling("3h", center=True).mean()


def quiet_samples(df: pd.DataFrame, quantile: float = 0.25,
                  steady_ppm_per_h: float = 15.0) -> np.ndarray:
    """A first guess at which samples are empty, using no occupancy model.

    Two conditions, and both matter. The sample sits in the low tail of its own
    24-hour window -- conditioned on the *day* rather than on the hour of day,
    which is the whole point: an estimator conditioned on hour of day cannot
    see past a routine, because at an hour when the room is always occupied
    every sample in the neighbourhood is elevated. And CO2 is flat there, so
    the room is at its asymptote rather than part-way through a decay toward
    it; without this the tail of every departure is read as C_out and drags the
    estimate up.

    Measured on the simulator this mask is 96% pure when ventilation is
    independent of occupancy and 91% pure when it is not, against 83-87% for a
    mask taken from the decoder's own output -- which is why the loop in
    `states.solve` only ever intersects with this one and never replaces it.
    """
    co2 = df["co2"]
    smooth = co2.rolling("30min", center=True).median()
    hours = df.index.to_series().diff().dt.total_seconds() / 3600
    dc_dt = (smooth.diff() / hours).where(hours <= 0.5)
    low = co2 <= co2.rolling("24h", center=True, min_periods=20).quantile(quantile)
    return (low & (dc_dt.abs() < steady_ppm_per_h)).fillna(False).to_numpy()


def hours_without_evidence(df: pd.DataFrame, unoccupied) -> int:
    """How many hours of day the mask never covers.

    Those hours are pure interpolation across the diurnal curve, so a large
    number here is the estimator saying it is extrapolating into a routine it
    cannot see around, and the occupancy estimate should be read accordingly.
    """
    h = df.index.hour[np.asarray(unoccupied, dtype=bool)]
    return int(24 - len(np.unique(h)))


def estimate_c_out_masked(df: pd.DataFrame, unoccupied) -> pd.Series:
    """C_out from the samples believed empty, interpolated across the rest.

    `unoccupied` is a boolean mask over `df`. Only its precision matters: hours
    it wrongly excludes are recovered by interpolation across the diurnal
    curve, whereas a single occupied sample admitted to a neighbourhood drags
    the estimate up by the whole excess that sample carries.
    """
    co2 = np.asarray(df["co2"], dtype=float)
    unoccupied = np.asarray(unoccupied, dtype=bool)
    hours = df.index.hour + df.index.minute / 60
    days = (df.index - df.index[0]).days.values
    uday = np.unique(days)
    day_pos = {d: i for i, d in enumerate(uday)}

    # A (day x hour) grid of the median believed-empty level, left NaN wherever
    # the mask is too thin to support an estimate.
    grid = np.full((len(uday), 24), np.nan)
    for h in range(24):
        dh = np.abs(hours - h)
        near_hour = (np.minimum(dh, 24 - dh) <= HOUR_HALFWIDTH) & unoccupied
        if not near_hour.any():
            continue
        sub_days = days[near_hour]
        sub = co2[near_hour]
        order = np.argsort(sub_days, kind="stable")
        sub, sub_days = sub[order], sub_days[order]
        for d in uday:
            for w in MASKED_WINDOW_DAYS:
                lo, hi = np.searchsorted(sub_days, [d - w, d + w + 1])
                if hi - lo >= MIN_CLEAN_SAMPLES:
                    # Median, not a low quantile: across a neighbourhood of
                    # empty samples the centre is the unbiased estimate, while
                    # a low quantile inherits the slope of the diurnal curve.
                    grid[day_pos[d], h] = np.median(sub[lo:hi])
                    break

    grid = _fill_grid(grid)
    if not np.isfinite(grid).any():
        # Nothing was believed empty anywhere; fall back rather than fail.
        return estimate_c_out(df)

    # Read the grid back onto the sample index, interpolating between hour
    # centres so the result is continuous rather than a staircase.
    centres = np.arange(24) + 0.5
    ext = np.concatenate([grid[:, -1:], grid, grid[:, :1]], axis=1)
    ext_x = np.concatenate([[centres[0] - 1], centres, [centres[-1] + 1]])
    rows = np.array([day_pos[d] for d in days])
    out = np.empty(len(df))
    for i in range(len(uday)):
        m = rows == i
        out[m] = np.interp(hours[m], ext_x, ext[i])
    return pd.Series(out, index=df.index, name="c_out")


def _fill_grid(grid: np.ndarray) -> np.ndarray:
    """Fill the (day x hour) holes the mask left.

    Hours that are never empty -- the middle of the working day, in a room with
    a routine -- carry no direct evidence at all. C_out is a slow, smooth
    diurnal curve, so those hours are interpolated circularly across hour of
    day from the hours that do have evidence. That is a far weaker assumption
    than reading an occupied level as the asymptote.
    """
    g = pd.DataFrame(grid)
    # Down columns first: the same hour on a nearby day is the closest evidence.
    g = g.interpolate(axis=0, limit_direction="both")
    # Then circularly across hour of day, for hours empty on every single day.
    tri = pd.concat([g, g, g], axis=1)
    tri.columns = range(72)
    tri = tri.interpolate(axis=1, limit_direction="both")
    filled = tri.iloc[:, 24:48].to_numpy()
    return filled


if __name__ == "__main__":
    from load import load_clean

    d = load_clean()
    c = estimate_c_out(d)
    print(f"C_out: median {c.median():.0f} ppm | p10 {c.quantile(.1):.0f} | p90 {c.quantile(.9):.0f}")
    print("\nby hour of day:")
    for h, v in c.groupby(c.index.hour).median().round(0).items():
        print(f"  {h:02d}h  {v:5.0f} ppm  {'#' * int((v - 380) / 8)}")
    print("\nby month:")
    print(c.groupby(c.index.to_period("M")).median().round(0).to_string())
