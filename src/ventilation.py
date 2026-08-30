"""Estimate the room's air exchange rate from CO2 decay episodes.

With the room empty (or its occupancy constant), indoor CO2 relaxes toward the
outdoor concentration:

    C(t) = C_out + (C_0 - C_out) * exp(-k * t)

so ln(C - C_out) is linear in t with slope -k, where k is the air changes per
hour (ACH). Fitting k on every sustained decay gives the ventilation rate
without any hardware beyond the CO2 sensor itself -- and that rate is what
later separates "CO2 fell because people left" from "CO2 fell because a window
opened", the confound that breaks naive occupancy models.
"""
import numpy as np
import pandas as pd

import paths

# Outdoor background CO2. A fixed value is a deliberate simplification; the
# seasonal and diurnal swing outdoors is small next to the indoor range.
C_OUT = 415.0
MIN_POINTS = 6      # >= 30 min at 5-min cadence
MIN_DROP = 30       # ppm; smaller falls are sensor noise, not a real decay
ACH_MAX = 15.0      # above this the fit is degenerate, not a real room

# Interpretation bands for the fitted ACH.
#
# These were originally labelled by window state, which was wrong. The room's
# window is fitted with a fly screen and a venetian blind whose tilt wand is
# broken, so its slats have not moved once across the record: the window has
# exactly one configuration, and a measurement with it open gives about 0.8 ACH
# -- barely above sealed. The high-exchange episodes therefore cannot be the
# window. They are the door standing open to the rest of the home, which is the
# only real ventilation control this room has.
BANDS = [(0.0, 0.5, "sealed"), (0.5, 1.5, "infiltration"),
         (1.5, 3.0, "door ajar"), (3.0, ACH_MAX, "door open")]


def find_decay_episodes(df: pd.DataFrame) -> pd.DataFrame:
    """Fit an exponential decay to every sustained fall in CO2."""
    falling = df["co2"].diff().rolling(3, center=True).mean() < 0
    runs = (falling != falling.shift()).cumsum()

    rows = []
    for _, seg in df[falling].groupby(runs[falling]):
        if len(seg) < MIN_POINTS:
            continue
        excess = seg["co2"] - C_OUT
        if (excess <= 0).any() or (seg["co2"].iloc[0] - seg["co2"].iloc[-1]) < MIN_DROP:
            continue
        hours = (seg.index - seg.index[0]).total_seconds() / 3600
        slope, _ = np.polyfit(hours, np.log(excess), 1)
        rows.append({
            "start": seg.index[0],
            "duration_h": hours[-1],
            "ach": -slope,
            # r2 of the log-linear fit: how well the mass-balance model holds.
            "r2": np.corrcoef(hours, np.log(excess))[0, 1] ** 2,
            "co2_start": seg["co2"].iloc[0],
            "co2_end": seg["co2"].iloc[-1],
        })

    ep = pd.DataFrame(rows)
    ep = ep[ep["ach"].between(0, ACH_MAX, inclusive="neither")].reset_index(drop=True)
    ep["regime"] = pd.cut(ep["ach"], [b[0] for b in BANDS] + [ACH_MAX],
                          labels=[b[2] for b in BANDS])
    return ep


def baseline_ach(episodes: pd.DataFrame, quantile: float = 0.25) -> float:
    """The room's background ventilation, i.e. ACH with nothing opened.

    Uses a low quantile rather than the minimum so a single bad fit cannot set
    the reference for the whole occupancy model downstream.
    """
    return float(episodes["ach"].quantile(quantile))


if __name__ == "__main__":
    from load import load_clean

    data = load_clean()
    ep = find_decay_episodes(data)
    ep.to_parquet(paths.processed("decay_episodes.parquet"))
    print(f"{len(ep)} decay episodes | median r2 {ep.r2.median():.2f} "
          f"| median ACH {ep.ach.median():.2f} | background ACH {baseline_ach(ep):.2f}")
    print(ep.groupby("regime", observed=True).agg(
        n=("ach", "size"), median_ach=("ach", "median"),
        mean_drop=("co2_start", lambda s: (s - ep.loc[s.index, "co2_end"]).mean()),
    ).round(2).to_string())


def fit_background_ach(df: pd.DataFrame, quantile: float = 0.25) -> float:
    """Background ACH using the time-varying C_out rather than a fixed 415 ppm.

    A low quantile rather than the minimum, so one bad fit cannot set the
    reference that the whole occupancy model depends on.
    """
    falling = df["co2"].diff().rolling(3, center=True).mean() < 0
    runs = (falling != falling.shift()).cumsum()

    rates = []
    for _, seg in df[falling].groupby(runs[falling]):
        if len(seg) < MIN_POINTS:
            continue
        excess = seg["co2"] - seg["c_out"]
        if (excess <= 5).any() or (seg["co2"].iloc[0] - seg["co2"].iloc[-1]) < MIN_DROP:
            continue
        hours = (seg.index - seg.index[0]).total_seconds() / 3600
        slope, _ = np.polyfit(hours, np.log(excess), 1)
        rates.append(-slope)

    rates = np.array([r for r in rates if 0 < r < ACH_MAX])
    return float(np.quantile(rates, quantile))


def ach_series(df: pd.DataFrame) -> pd.Series:
    """Air changes per hour as a signal, not a constant.

    A single k cannot describe five months: with a window open it is ~4, sealed
    ~0.3. Forcing one average value makes the model invent occupants at night
    (removal overestimated) and miss them by day (removal underestimated). Each
    decay episode gives a local measurement of k; those are placed at their
    midpoints and interpolated, falling back to the median for that hour of day
    where no episode is near.
    """
    falling = df["co2"].diff().rolling(3, center=True).mean() < 0
    runs = (falling != falling.shift()).cumsum()

    rows = []
    for _, seg in df[falling].groupby(runs[falling]):
        if len(seg) < MIN_POINTS:
            continue
        excess = seg["co2"] - seg["c_out"]
        if (excess <= 5).any() or (seg["co2"].iloc[0] - seg["co2"].iloc[-1]) < MIN_DROP:
            continue
        hours = (seg.index - seg.index[0]).total_seconds() / 3600
        slope, _ = np.polyfit(hours, np.log(excess), 1)
        if 0 < -slope < ACH_MAX:
            rows.append((seg.index[0] + (seg.index[-1] - seg.index[0]) / 2, -slope))

    ep = pd.Series(dict(rows)).sort_index()

    # Interpolating the episode estimates pointwise fails: k multiplies
    # (C - C_out), so a single overestimated k during a CO2 peak produces
    # absurd occupancy (16 people in a 61 m3 room). Regularise it into two slow
    # components instead -- a time-of-day profile and a fortnightly level --
    # which keeps the day/night contrast without the multiplicative noise.
    profile = ep.groupby(ep.index.hour).median().reindex(range(24))
    profile = profile.interpolate(limit_direction="both")
    # Smooth circularly so 23h and 00h stay continuous.
    profile = pd.concat([profile] * 3).rolling(5, center=True).median().iloc[24:48]
    profile.index = range(24)

    hour_of = pd.Series(df.index.hour, index=df.index).map(profile)
    residual = ep / pd.Series(ep.index.hour, index=ep.index).map(profile)
    level = (residual.rolling("14D", center=True).median()
             .reindex(df.index).interpolate(limit_direction="both").clip(0.5, 2))
    return (hour_of * level).clip(0.2, 3.0).rename("ach")
