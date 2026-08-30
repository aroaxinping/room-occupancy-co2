"""Estimate the room's CO2 asymptote (C_out) as a time-varying signal.

A constant outdoor value (415 ppm) biases the occupancy model: the room does
not relax toward the street, it relaxes toward whatever air surrounds it, and
that changes through the day (window open at noon, sealed at night) and across
the season. Fitting C_out per decay episode is not identifiable -- asymptote
and decay rate trade off against each other over a short window -- so it is
estimated instead as a low envelope over a local neighbourhood in both
time-of-day and calendar time.
"""
import numpy as np
import pandas as pd

WINDOW_DAYS = 7      # calendar neighbourhood
HOUR_HALFWIDTH = 1   # +/- 1 hour of day
QUANTILE = 0.10      # low envelope: occupancy pushes CO2 up, never down


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
