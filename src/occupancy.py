"""Invert the CO2 mass balance to estimate how many people are in the room.

For a well-mixed space the indoor concentration obeys

    dC/dt = (G * N * 1e6) / V  -  k * (C - C_out)

with C in ppm, G the CO2 exhaled per person (m3/h), V the volume (m3) and k the
air changes per hour. Everything but N is measured or estimated, so

    N = (dC/dt + k * (C - C_out)) * V / (G * 1e6)

No occupancy labels are used: the estimate is physical, and is checked
afterwards against periods whose true occupancy is known independently.
"""
import os

import numpy as np
import pandas as pd

import paths

from baseline import estimate_c_out

# Effective volume in m3, measured by hand. The ceiling slopes, so the mean of
# its two extremes gives the height of the wedge. Kept as a single figure
# rather than as dimensions: the floor plan of a private home is not something
# this repository needs to carry. Override for another room.
VOLUME = float(os.environ.get("ROOM_VOLUME_M3", 61.0))

# Seated adult, light work (~18 L/h, ASHRAE/EN 16798). This varies with body
# mass and activity and is the dominant source of error in N, which is why the
# output is read as "about two people" and never as an exact count.
G_M3_PER_H = 0.018

# The sensor refreshes every ~6 min, so a shorter window measures quantisation
# steps rather than air.
SMOOTH = "30min"
# Sustained-episode detection: what counts as "more than one person".
CROWD_THRESHOLD = 1.5
CROWD_MIN_MINUTES = 45


def estimate_occupancy(df: pd.DataFrame, ach, volume: float = VOLUME) -> pd.DataFrame:
    """`ach` may be a scalar or a per-timestamp Series from `ventilation.ach_series`."""
    out = df.copy()
    if "c_out" not in out:
        out["c_out"] = estimate_c_out(out)

    smooth = out["co2"].rolling(SMOOTH, center=True).median()
    hours = out.index.to_series().diff().dt.total_seconds() / 3600
    dc_dt = smooth.diff() / hours
    # Without this the multi-week outages produce a huge fake derivative.
    dc_dt[hours > 0.5] = np.nan

    out["co2_smooth"] = smooth
    # Kept unclipped: clipping at zero and then averaging rectifies symmetric
    # noise into a positive bias, which reads as phantom occupancy.
    out["n"] = (dc_dt + ach * (smooth - out["c_out"])) * volume / (G_M3_PER_H * 1e6)
    out["n_smooth"] = out["n"].rolling("1h", center=True).median()
    return out


def find_crowd_episodes(occ: pd.DataFrame) -> pd.DataFrame:
    """Sustained stretches with more than one person present.

    Averaging hides these: a two-person visit lasting four hours is a handful of
    samples among thousands, so it has to be found as an episode, not a mean.
    """
    high = occ["n_smooth"] >= CROWD_THRESHOLD
    runs = (high != high.shift()).cumsum()

    rows = []
    for _, seg in occ[high].groupby(runs[high]):
        minutes = (seg.index[-1] - seg.index[0]).total_seconds() / 60
        if minutes >= CROWD_MIN_MINUTES:
            rows.append({"start": seg.index[0], "end": seg.index[-1],
                         "minutes": minutes, "n_peak": seg["n_smooth"].max(),
                         "n_median": seg["n_smooth"].median()})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    from load import load_clean
    from ventilation import ach_series

    data = load_clean()
    data["c_out"] = estimate_c_out(data)
    ach = ach_series(data)
    occ = estimate_occupancy(data, ach)
    episodes = find_crowd_episodes(occ)

    occ.to_parquet(paths.processed("occupancy.parquet"))
    episodes.to_parquet(paths.processed("crowd_episodes.parquet"))
    print(f"volume {VOLUME:.1f} m3 | median ACH {ach.median():.2f} | "
          f"one person = {G_M3_PER_H * 1e6 / VOLUME:.0f} ppm/h when sealed")
    print(f"{len(episodes)} crowd episodes | median peak N {episodes.n_peak.median():.2f} "
          f"| median duration {episodes.minutes.median():.0f} min")
