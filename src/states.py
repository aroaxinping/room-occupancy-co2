"""Decode occupancy as a sequence of discrete states instead of a free number.

The continuous inversion is ill-posed. Ventilation k is only observable during
decay, but occupancy matters during accumulation, where a slow rise is equally
well explained by few people with little ventilation or many people with a lot.
Solving for N pointwise therefore produces physically impossible values -- 5 to
8 people in a room that never holds more than 2.

Constraining N to {0, 1, 2} and requiring it to persist in time makes the
problem well-posed again. The mass balance becomes the emission model (given N,
what should dC/dt be?) and Viterbi finds the most likely state sequence.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

import paths

MAX_OCCUPANTS = 2          # hard physical constraint from the occupant
STATES = np.arange(MAX_OCCUPANTS + 1)
# Cost of changing state, in log-likelihood units. Occupancy is persistent:
# people do not blink in and out every five minutes.
SWITCH_PENALTY = 4.0


def decode(df: pd.DataFrame, ach, volume: float, g_m3_per_h: float = 0.018) -> pd.Series:
    smooth = df["co2"].rolling("30min", center=True).median()
    hours = df.index.to_series().diff().dt.total_seconds() / 3600
    observed = smooth.diff() / hours
    k = ach if np.isscalar(ach) else np.asarray(ach)

    # What the mass balance predicts for each candidate occupancy.
    gain = g_m3_per_h * 1e6 / volume
    loss = k * (smooth - df["c_out"])
    predicted = np.stack([gain * n - loss for n in STATES])

    residual = np.asarray(observed) - predicted
    valid = np.isfinite(residual).all(axis=0)
    sigma = np.nanstd(residual[:, valid])
    # Gaussian log-likelihood, up to a constant.
    logp = -0.5 * (residual / sigma) ** 2
    logp[:, ~valid] = 0.0

    # Viterbi over the three states.
    n_t = logp.shape[1]
    score = np.full((len(STATES), n_t), -np.inf)
    back = np.zeros((len(STATES), n_t), dtype=int)
    score[:, 0] = logp[:, 0]
    for t in range(1, n_t):
        # Penalty grows with the size of the jump: 0 -> 2 is less likely than 0 -> 1.
        trans = score[:, t - 1][:, None] - SWITCH_PENALTY * np.abs(STATES[:, None] - STATES[None, :])
        back[:, t] = trans.argmax(axis=0)
        score[:, t] = trans.max(axis=0) + logp[:, t]

    path = np.empty(n_t, dtype=int)
    path[-1] = score[:, -1].argmax()
    for t in range(n_t - 1, 0, -1):
        path[t - 1] = back[path[t], t]
    return pd.Series(path, index=df.index, name="occupants")


if __name__ == "__main__":
    from load import load_clean
    from baseline import estimate_c_out
    from ventilation import ach_series
    from occupancy import VOLUME

    data = load_clean()
    data["c_out"] = estimate_c_out(data)
    occ = decode(data, ach_series(data), VOLUME)
    occ.to_frame().to_parquet(paths.processed("states.parquet"))

    # Validation windows are defined in a local, gitignored file rather than
    # here: naming the hours at which this particular home is empty would
    # publish the routine that the rest of the repo is careful not to.
    dow, hour = occ.index.dayofweek, occ.index.hour
    print("occupancy share:", {int(s): f"{(occ == s).mean():.1%}" for s in STATES})

    periods = paths.DATA_DIR / "validation_periods.json"
    if not periods.exists():
        print(f"\nno validation windows at {periods}; skipping the report")
        raise SystemExit

    print("\nmean occupants by period:")
    for name, spec in json.loads(periods.read_text()).items():
        mask = pd.Series(hour, occ.index).between(*spec["hours"])
        mask &= pd.Series(dow, occ.index).isin(spec["weekdays"])
        print(f"  {name:12s} {occ[np.asarray(mask)].mean():.2f}   expected {spec['expected']}")
