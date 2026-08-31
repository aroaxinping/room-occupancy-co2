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
    # Across a multi-week outage this difference is finite and near zero, so it
    # survives the validity mask below and is scored as evidence for whichever
    # state predicts no net change. Viterbi also charges the same switch
    # penalty across 26 days as across 5 minutes, so the decoder would carry
    # the pre-outage state over the gap and corrupt the transition at the start
    # of three of the four blocks.
    observed = observed.where(hours <= 0.5)
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


# How many rounds of (C_out, k, N) to alternate over. The C_out step converges
# in one or two -- it is constrained to shrink, so it cannot run away -- and
# the k step in three or four.
ROUNDS = 5


def solve(df: pd.DataFrame, volume: float, g_m3_per_h: float = 0.018,
          rounds: int = ROUNDS, report: bool = False) -> pd.DataFrame:
    """Estimate C_out, k and occupancy together instead of in sequence.

    Solving them in sequence is what produced the systematic off-by-one the
    simulator found. `baseline.estimate_c_out` conditions on hour of day, so
    where occupancy follows a routine it absorbs the occupancy signal into the
    asymptote -- +85 to +160 ppm through the working afternoon, which the
    inversion then subtracts back out of the answer. `ventilation.ach_series`
    then fits its decay episodes against that inflated C_out, which makes the
    excess look like it is collapsing faster than it is and inflates k on top
    (a true 0.39 read as 1.59). Both errors push N down, and together they cost
    almost exactly one occupant whenever anyone is present.

    So: start from an occupancy-model-free guess at which samples are empty
    (`baseline.quiet_samples`), estimate C_out from those alone, solve k from
    the mass balance given the current occupancy, decode, repeat. The
    empty-sample mask is only ever *intersected* with the decoder's output,
    never replaced by it. That constraint matters. An unconstrained EM here has
    a degenerate attractor -- call the room empty everywhere and C_out rises to
    meet C, which makes it emptier still -- and left free it walks straight
    into it: on the simulator the C_out bias at one occupant climbs from +50 to
    +145 ppm over four iterations while accuracy falls from 0.84 to 0.78.

    Returns a frame of `occupants`, `c_out` and `ach`.
    """
    import baseline
    from ventilation import ach_from_occupancy, ach_series

    seed = baseline.quiet_samples(df)
    mask = seed.copy()
    frame = df.copy()
    occ = None

    for _ in range(rounds):
        frame["c_out"] = baseline.estimate_c_out_masked(frame, mask)
        if occ is None:
            # Nothing to solve k against on the first pass, so bootstrap from
            # the decay episodes: biased, but only used to seed one decode.
            ach = ach_series(frame)
        else:
            ach = ach_from_occupancy(frame, occ, volume, g_m3_per_h)
        occ = decode(frame, ach, volume, g_m3_per_h)

        new = seed & (occ.to_numpy() == 0)
        # Refuse a mask too thin to estimate anything from: the seed is the
        # floor of what this scheme is allowed to believe.
        mask = seed if new.sum() < 200 else new

    if report:
        blind = baseline.hours_without_evidence(frame, mask)
        print(f"  C_out from {int(mask.sum())} believed-empty samples "
              f"({mask.mean():.1%}); {blind} of 24 hours of day have no direct "
              f"evidence and are interpolated")

    return pd.DataFrame({"occupants": occ, "c_out": frame["c_out"], "ach": ach})


if __name__ == "__main__":
    from baseline import estimate_c_out
    from load import load_clean
    from occupancy import VOLUME
    from ventilation import ach_series

    data = load_clean()
    data["c_out"] = estimate_c_out(data)
    # decode(), not solve(). The alternating solver recovers occupancy better on
    # synthetic data where ventilation is independent of occupancy, and worse as
    # the two become coupled -- and on the real room it moved all five
    # validation periods away from their known values, most sharply the two that
    # were closest (0.88 -> 0.80 against a true 1, and 1.07 -> 0.84 against 1-2).
    # The real room plausibly is tightly coupled, since the occupant opens the
    # window when present. Until labels settle that, the evidence favours this.
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
