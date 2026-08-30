"""Decode occupancy and ventilation together as one discrete state sequence.

Two unknowns drive the CO2 curve and neither is observed: how many people are
in the room, and whether the door is open. Solving them separately fails --
estimating k from decay episodes and then inverting for N produced impossible
occupancies, because a slow rise is equally well explained by few people in a
sealed room or many with the door open.

Both are discrete and both persist: people do not blink in and out, and doors
stay as they are for hours. So the pair is decoded jointly over

    (occupants, ventilation) in {0, 1, 2} x {closed, door open}

with the mass balance as the emission model and Viterbi finding the most likely
path. The window is reported as always shut at night and rarely opened
otherwise, so a two-level ventilation state covers the real behaviour.
"""
import numpy as np
import pandas as pd

MAX_OCCUPANTS = 2
OCCUPANTS = np.arange(MAX_OCCUPANTS + 1)
VENT_LABELS = ("closed", "door open")

# Switching cost in log-likelihood units. Occupancy changes are rarer than door
# movements, so they carry the heavier penalty.
OCCUPANT_PENALTY = 5.0
VENT_PENALTY = 3.0


def ventilation_levels(episodes: pd.Series) -> tuple:
    """The two ventilation regimes, taken from the decay-episode distribution.

    The low quantile is the room sealed (infiltration through gaps only); the
    upper one is the door standing open onto the rest of the home.
    """
    return float(episodes.quantile(0.15)), float(episodes.quantile(0.80))


def decode(df: pd.DataFrame, k_closed: float, k_open: float, volume: float,
           g_m3_per_h: float = 0.018) -> pd.DataFrame:
    smooth = df["co2"].rolling("30min", center=True).median()
    hours = df.index.to_series().diff().dt.total_seconds() / 3600
    observed = np.asarray(smooth.diff() / hours)
    # Across a multi-week outage the difference is finite and near zero, so it
    # survives the validity mask and is scored as evidence for whichever state
    # predicts no net change -- and Viterbi charges the same switch penalty
    # across 26 days as across 5 minutes, so the decoder carries the
    # pre-outage state over the gap. Three of the four blocks begin with a
    # transition corrupted this way unless the gap is excluded outright.
    observed[np.asarray(hours) > 0.5] = np.nan
    excess = np.asarray(smooth - df["c_out"])
    gain = g_m3_per_h * 1e6 / volume

    states = [(n, v) for v in range(2) for n in OCCUPANTS]
    ks = (k_closed, k_open)
    predicted = np.stack([gain * n - ks[v] * excess for n, v in states])

    residual = observed - predicted
    valid = np.isfinite(residual).all(axis=0)
    sigma = np.nanstd(residual[:, valid])
    logp = -0.5 * (residual / sigma) ** 2
    logp[:, ~valid] = 0.0

    occ = np.array([s[0] for s in states])
    vent = np.array([s[1] for s in states])
    cost = (OCCUPANT_PENALTY * np.abs(occ[:, None] - occ[None, :])
            + VENT_PENALTY * np.abs(vent[:, None] - vent[None, :]))

    n_t = logp.shape[1]
    score = np.full((len(states), n_t), -np.inf)
    back = np.zeros((len(states), n_t), dtype=int)
    score[:, 0] = logp[:, 0]
    for t in range(1, n_t):
        trans = score[:, t - 1][:, None] - cost
        back[:, t] = trans.argmax(axis=0)
        score[:, t] = trans.max(axis=0) + logp[:, t]

    path = np.empty(n_t, dtype=int)
    path[-1] = score[:, -1].argmax()
    for t in range(n_t - 1, 0, -1):
        path[t - 1] = back[path[t], t]

    return pd.DataFrame({"occupants": occ[path], "vent": vent[path]}, index=df.index)
