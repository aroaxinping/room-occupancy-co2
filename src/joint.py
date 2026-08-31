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
import sys

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


# ---------------------------------------------------------------------------
# What the failure above was missing, and what the temperature channel is
# actually good for.
#
# The joint decode failed because it had two unknowns and one observable. Given
# the freedom to explain any rise as a sealed room rather than as a person, it
# took it, and collapsed to "closed" in 99-100% of samples. Re-running it with a
# corrected asymptote and better-chosen levels did not change that: the problem
# is identifiability, not tuning, and it stays until something outside the CO2
# series constrains ventilation.
#
# Temperature was the candidate for that something. It is not. Measured against
# the confirmed periods (`src/thermal.py` records the numbers) the thermal
# signal tracks the air conditioner, and a recirculating split unit changes the
# room's temperature without changing its air exchange rate -- so the channel
# constrains *occupancy*, not k, and the ventilation state above stays as
# unidentifiable as it was. What follows therefore keeps the occupancy states
# and drops the ventilation ones, taking `ventilation.ach_series` (or the
# solver's k) exactly as `states.decode` does.
#
# So this is `states.decode` with a second emission channel, and the only claim
# it makes beyond that one is the thermal term.


THERMAL_WEIGHT = 1.0    # see `decode_thermal`; swept in `--sweep` below


def decode_thermal(df: pd.DataFrame, ach, volume: float,
                   g_m3_per_h: float = 0.018, weight: float = THERMAL_WEIGHT,
                   switch_penalty: float = 4.0,
                   t_deficit: pd.Series = None) -> pd.Series:
    """Occupancy over {0, 1, 2} from the CO2 mass balance *and* the temperature.

    Identical to `states.decode` -- same emission model, same switch penalty,
    same Viterbi -- with `thermal.presence_logp` added to the per-sample
    log-likelihood. The thermal term gates itself off wherever the room shows no
    cooling range to speak of, so across March-May this returns the same
    sequence `states.decode` does, sample for sample, and the change is confined
    to the months where there is something to see.

    `weight` is the one number this adds to the model. At 0 it is
    `states.decode`, and everything from 0.5 to 4.0 lands confirmed-period
    accuracy in 80.8-82.0% against a baseline of 72.5%, which is the argument
    that 1.0 is a choice inside a plateau rather than a value fitted to the
    validation windows. `--sweep` prints the plateau.

    `df` needs a `temp` column alongside `co2` and `c_out`.
    """
    from thermal import presence_logp

    smooth = df["co2"].rolling("30min", center=True).median()
    hours = df.index.to_series().diff().dt.total_seconds() / 3600
    observed = smooth.diff() / hours
    observed = observed.where(hours <= 0.5)
    k = ach if np.isscalar(ach) else np.asarray(ach)

    gain = g_m3_per_h * 1e6 / volume
    loss = k * (smooth - df["c_out"])
    predicted = np.stack([gain * n - loss for n in OCCUPANTS])

    residual = np.asarray(observed) - predicted
    valid = np.isfinite(residual).all(axis=0)
    sigma = np.nanstd(residual[:, valid])
    logp = -0.5 * (residual / sigma) ** 2
    logp[:, ~valid] = 0.0

    # The second channel. Added on the same footing as the first, and zero
    # wherever the gate is shut -- including across the outages, where `valid`
    # is False and the CO2 channel is silenced for the same reason.
    thermal = presence_logp(df, OCCUPANTS, weight=weight, t_deficit=t_deficit)
    thermal[:, ~valid] = 0.0
    logp = logp + thermal

    n_t = logp.shape[1]
    score = np.full((len(OCCUPANTS), n_t), -np.inf)
    back = np.zeros((len(OCCUPANTS), n_t), dtype=int)
    score[:, 0] = logp[:, 0]
    for t in range(1, n_t):
        trans = (score[:, t - 1][:, None]
                 - switch_penalty * np.abs(OCCUPANTS[:, None] - OCCUPANTS[None, :]))
        back[:, t] = trans.argmax(axis=0)
        score[:, t] = trans.max(axis=0) + logp[:, t]

    path = np.empty(n_t, dtype=int)
    path[-1] = score[:, -1].argmax()
    for t in range(n_t - 1, 0, -1):
        path[t - 1] = back[path[t], t]
    return pd.Series(path, index=df.index, name="occupants")


def _confirmed(occ: pd.Series, spec: dict) -> dict:
    """Specificity, sensitivity and accuracy against the occupant's own windows.

    Specificity is over the two windows confirmed empty, sensitivity over the
    one confirmed to hold one person. The other two are reported as means only:
    one genuinely varies and the other is "one or two, and one comes and goes",
    so neither can score a binary.
    """
    hour = pd.Series(occ.index.hour, occ.index)
    dow = pd.Series(occ.index.dayofweek, occ.index)
    m = {n: np.asarray(hour.between(*s["hours"]) & dow.isin(s["weekdays"]))
         for n, s in spec.items()}
    empty = np.zeros(len(occ), bool)
    occupied = np.zeros(len(occ), bool)
    for n, s in spec.items():
        if s["expected"] == "0":
            empty |= m[n]
        elif s["expected"] == "1":
            occupied |= m[n]
    o = occ.to_numpy()
    return {
        "specificity": (o[empty] == 0).mean(),
        "sensitivity": (o[occupied] > 0).mean(),
        "accuracy": ((o[empty] == 0).sum() + (o[occupied] > 0).sum())
                    / (empty.sum() + occupied.sum()),
        "n": int(empty.sum() + occupied.sum()),
        "periods": {n: float(o[m[n]].mean()) for n in spec},
        "per_window": {n: float((o[m[n]] == 0).mean()) for n, s in spec.items()
                       if s["expected"] == "0"},
    }


if __name__ == "__main__":
    import json

    import paths
    from baseline import estimate_c_out
    from load import load_clean
    from occupancy import VOLUME
    from states import decode
    from ventilation import ach_series
    import thermal

    data = load_clean()
    data["c_out"] = estimate_c_out(data)
    ach = ach_series(data)
    defi = thermal.deficit(data)

    before = decode(data, ach, VOLUME)
    after = decode_thermal(data, ach, VOLUME, t_deficit=defi)
    print(f"the thermal channel is open on "
          f"{thermal.gate(thermal.cooling_scale(defi)).mean():.1%} of samples; "
          f"it changes {(after != before).mean():.1%} of them")

    periods = paths.DATA_DIR / "validation_periods.json"
    if not periods.exists():
        print(f"\nno validation windows at {periods}; skipping the report")
        raise SystemExit
    spec = json.loads(periods.read_text())

    print("\nagainst the confirmed periods:")
    print(f"  {'':10s} {'spec':>7s} {'sens':>7s} {'acc':>7s}")
    for name, occ in (("before", before), ("after", after)):
        r = _confirmed(occ, spec)
        print(f"  {name:10s} {r['specificity']:7.1%} {r['sensitivity']:7.1%} "
              f"{r['accuracy']:7.1%}")

    print("\nthe windows confirmed empty, separately -- the weekday small hours "
          "are\nwhat this channel is aimed at, and the reference window is what it costs:")
    b, a = _confirmed(before, spec), _confirmed(after, spec)
    for n in b["per_window"]:
        print(f"  {n:10s} correct {b['per_window'][n]:6.1%} -> "
              f"{a['per_window'][n]:6.1%}")

    print("\nmean occupants by period:")
    for n, s in spec.items():
        print(f"  {n:10s} {b['periods'][n]:.2f} -> {a['periods'][n]:.2f}"
              f"   expected {s['expected']}")

    if "--sweep" in sys.argv:
        print("\nthe weight is one number, and the result does not turn on it:")
        print(f"  {'weight':>7s} {'spec':>7s} {'sens':>7s} {'acc':>7s}")
        for w in (0.0, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0):
            r = _confirmed(decode_thermal(data, ach, VOLUME, weight=w,
                                          t_deficit=defi), spec)
            print(f"  {w:7.2f} {r['specificity']:7.1%} {r['sensitivity']:7.1%} "
                  f"{r['accuracy']:7.1%}")
