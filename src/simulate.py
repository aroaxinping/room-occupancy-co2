"""A simulated room, so that "does it actually count?" has a number.

The real room has no ground truth. The estimate is checked against periods whose
occupancy is known by argument, which shows it is not nonsense but cannot give a
precision, a recall, or an answer to whether one person is told from two.

A simulation can. Integrating the same mass balance forward from a known
schedule produces a series whose true occupancy is known at every sample, and
running the real pipeline over it measures the recovery error directly.

The point is only made if the simulated sensor is as awkward as the real one, so
the quirks that shaped every design decision in this project are reproduced:
readings that refresh roughly every eight minutes and are padded by repetition
in between, integer quantisation, a C_out that drifts between household air at
night and outdoor air by day, and multi-week outages.

    python3 src/simulate.py            simulate, recover, report the error
    python3 src/simulate.py --ablate   which of C_out and k carries the error
    python3 src/simulate.py --sweep    the same, across coupling strengths

What this bounds is the error from estimation. It cannot bound the error from
the physics being wrong, because the simulator assumes the same physics the
model does -- a well-mixed single zone. A model recovering its own generative
assumptions is the floor of what it must achieve, not proof that it works.

One choice in here dominates every number it prints, and it is a property of
the simulator rather than of the room. `COUPLING` sets how often ventilation
follows occupancy. At 1.0 -- the original setting, kept as the default so the
figures stay comparable -- k is *perfectly* determined by N, and no method can
separate two quantities that never once vary independently. `--sweep` measures
how much of the error that assumption is responsible for, and the answer is
most of it.
"""
import sys

import numpy as np
import pandas as pd

C_INITIAL = 500.0
STEP_MINUTES = 1        # the physics is integrated finely, then sampled coarsely
REPORT_MINUTES = 8      # the device's real median gap between distinct values
SENSOR_NOISE_PPM = 12.0

# Matches the real room: 7 x 4 m, sloped ceiling averaging 2.175 m.
VOLUME = 60.9
G_M3_PER_H = 0.018

# Ventilation regimes, anchored to the rates measured in the real room.
K_SEALED = 0.39
K_WINDOW = 1.62
K_DOOR = 3.20

# How strongly ventilation follows occupancy, from 0 (independent) to 1 (the
# window is open exactly when, and only when, someone is in the room).
#
# This is the single most consequential number in the file and it was never
# measured. At 1.0 k and N are perfectly confounded: every occupied sample is
# also a ventilated one, so the CO2 record contains no case of one varying
# without the other and the two are not separable even in principle. The real
# room is nowhere near that -- the window is opened on some occupied
# afternoons and not others, and the door moves independently of both -- so
# recovery measured at 1.0 is a lower bound on a pathological case rather than
# an estimate of how the model performs.
COUPLING = 1.0


def _schedule(index: pd.DatetimeIndex, rng: np.random.Generator,
              coupling: float = COUPLING):
    """A plausible weekly routine: occupancy, ventilation and outdoor level.

    Deliberately not the real routine. The simulation exists to measure
    recovery error, and reproducing the occupant's actual timetable would put a
    dated behavioural record back into the repository by another route.
    """
    hour = index.hour + index.minute / 60
    weekday = index.dayofweek < 5

    occupants = np.zeros(len(index), dtype=int)
    occupants[weekday & (hour >= 9) & (hour < 13.5)] = 1
    occupants[weekday & (hour >= 15) & (hour < 19)] = 1
    occupants[~weekday & (hour >= 11) & (hour < 20)] = 1

    # Two people on some afternoons, chosen per-day so the episodes are blocks
    # rather than scattered samples.
    days = index.normalize()
    busy = pd.Series(rng.random(len(np.unique(days))) < 0.25, index=np.unique(days))
    pair = days.map(busy).to_numpy() & (hour >= 16) & (hour < 20) & occupants.astype(bool)
    occupants[pair] = 2

    # Ventilation. On a fraction `coupling` of days the window is open exactly
    # while the room is occupied; on the rest that same pattern is rolled to a
    # random offset within the day, which keeps the amount of ventilation and
    # the length of its blocks identical while destroying the alignment with
    # occupancy. Sweeping `coupling` therefore changes only the confounding,
    # not the marginal statistics of either series.
    uday = np.unique(days)
    daycode = pd.Series(days).factorize()[0]
    follows = rng.random(len(uday)) < coupling
    offsets = rng.integers(1, 24 * 60 // STEP_MINUTES, len(uday))
    present = occupants > 0
    ventilated = np.zeros(len(index), dtype=bool)
    for i in range(len(uday)):
        m = daycode == i
        block = present[m]
        ventilated[m] = block if follows[i] else np.roll(block, int(offsets[i]))

    k = np.full(len(index), K_SEALED)
    k[ventilated] = K_WINDOW
    wide = pd.Series(rng.random(len(uday)) < 0.15, index=uday)
    k[days.map(wide).to_numpy() & (hour >= 12) & (hour < 14)] = K_DOOR

    # C_out drifts: household air at night, closer to outdoor by day.
    c_out = 520 + 90 * np.cos(2 * np.pi * (hour - 2) / 24)
    return occupants, k, c_out


def simulate(days: int = 120, seed: int = 7, outages: bool = True,
             coupling: float = COUPLING) -> pd.DataFrame:
    """Integrate the mass balance forward and sample it like the real device."""
    rng = np.random.default_rng(seed)
    fine = pd.date_range("2020-01-06", periods=days * 24 * 60 // STEP_MINUTES,
                         freq=f"{STEP_MINUTES}min")
    occupants, k, c_out = _schedule(fine, rng, coupling)

    gain = G_M3_PER_H * 1e6 / VOLUME
    dt = STEP_MINUTES / 60
    co2 = np.empty(len(fine))
    c = C_INITIAL
    for i in range(len(fine)):
        c += (gain * occupants[i] - k[i] * (c - c_out[i])) * dt
        co2[i] = c

    truth = pd.DataFrame({"co2": co2, "occupants": occupants, "k": k, "c_out": c_out},
                         index=fine)

    # The device refreshes every ~8 minutes; the export repeats the last value
    # in between and rounds to whole ppm.
    reported = truth["co2"] + rng.normal(0, SENSOR_NOISE_PPM, len(truth))
    held = reported.iloc[::REPORT_MINUTES].reindex(truth.index).ffill()
    truth["reading"] = held.round()

    if outages:
        # Two multi-week holes, as in the real record.
        for start, length in ((0.30, 26), (0.68, 25)):
            begin = truth.index[int(len(truth) * start)]
            truth.loc[begin:begin + pd.Timedelta(days=length), "reading"] = np.nan

    return truth.dropna(subset=["reading"])


def _frame(truth: pd.DataFrame) -> pd.DataFrame:
    frame = pd.DataFrame({"co2": truth["reading"]}).resample("5min").median()
    return frame.interpolate(limit=6, limit_area="inside").dropna()


def recover(truth: pd.DataFrame, legacy: bool = False, true_c_out: bool = False,
            true_k: bool = False, report_solver: bool = False) -> pd.DataFrame:
    """Run the real pipeline over the simulated readings.

    `legacy` restores the sequential version -- C_out from the hour-of-day
    quantile, then k from the decay episodes, then one decode -- so the fix can
    be measured against what it replaced. `true_c_out` and `true_k` substitute
    the simulator's own values for one component at a time, which is what
    separates the two error sources: neither substitution alone recovers
    anything, and both together recover almost everything.
    """
    from baseline import estimate_c_out
    from occupancy import VOLUME as MODEL_VOLUME
    from states import decode, solve
    from ventilation import ach_series

    frame = _frame(truth)
    c_true = truth["c_out"].reindex(frame.index, method="nearest")
    k_true = truth["k"].reindex(frame.index, method="nearest")

    if legacy or true_c_out or true_k:
        frame["c_out"] = c_true if true_c_out else estimate_c_out(frame)
        ach = k_true if true_k else ach_series(frame)
        estimate = decode(frame, ach, MODEL_VOLUME)
    else:
        solved = solve(frame, MODEL_VOLUME, report=report_solver)
        estimate, ach = solved["occupants"], solved["ach"]
        frame["c_out"] = solved["c_out"]

    out = pd.DataFrame({"estimated": estimate})
    out["true"] = truth["occupants"].reindex(out.index, method="nearest")
    out["true_k"] = k_true.reindex(out.index)
    out["true_c_out"] = c_true.reindex(out.index)
    out["est_c_out"] = frame["c_out"].reindex(out.index)
    out["est_k"] = pd.Series(np.asarray(ach), index=frame.index).reindex(out.index)
    return out.dropna()


def report(out: pd.DataFrame) -> None:
    true, est = out["true"].astype(int), out["estimated"].astype(int)
    print(f"samples            {len(out)}")
    print(f"exact match        {(true == est).mean():.1%}")
    print(f"mean abs. error    {(true - est).abs().mean():.3f} occupants")
    print(f"empty vs occupied  {((true > 0) == (est > 0)).mean():.1%}")

    print("\nconfusion (rows true, columns estimated)")
    print(pd.crosstab(true, est, normalize="index").round(3).to_string())

    print("\nby true occupancy")
    for n in sorted(true.unique()):
        m = true == n
        print(f"  {n} -> estimated {est[m].mean():.2f} on average, "
              f"correct {(est[m] == n).mean():.1%} of {m.sum()} samples")

    print("\nby true ventilation")
    for lo, hi, name in ((0, 0.8, "sealed"), (0.8, 2.5, "window"), (2.5, 9, "door")):
        m = out["true_k"].between(lo, hi)
        if m.any():
            print(f"  {name:7s} k={out.loc[m, 'true_k'].median():.2f}  "
                  f"exact {(true[m] == est[m]).mean():.1%} of {m.sum()} samples")

    print("\nnuisance parameters")
    bias = (out["est_c_out"] - out["true_c_out"]).groupby(true).mean()
    print("  C_out bias by true N (ppm):  "
          + "  ".join(f"N={n}: {v:+.0f}" for n, v in bias.round(0).items()))
    k = out.groupby(out["true_k"].round(2))["est_k"].median()
    print("  k estimated, by true k:      "
          + "  ".join(f"{t:.2f} -> {v:.2f}" for t, v in k.items()))


def _line(out: pd.DataFrame, label: str) -> None:
    true, est = out["true"].astype(int), out["estimated"].astype(int)
    per = "  ".join(f"N={n}: {(est[true == n] == n).mean():5.1%}" for n in (0, 1, 2)
                    if (true == n).any())
    print(f"  {label:26s} exact {(true == est).mean():5.1%}   "
          f"mae {(true - est).abs().mean():.3f}   {per}")


def ablate(coupling: float = COUPLING, seed: int = 7) -> None:
    """Which of the two nuisance parameters carries the error.

    Substituting one true value at a time is the informative experiment: if the
    errors were independent, fixing either would recover roughly its own share.
    They are not. Fixing only C_out, or only k, leaves the estimate exactly
    where it was; fixing both recovers nearly everything. The two failures are
    not additive, they are the same failure entering twice, because
    `ach_series` fits its decay episodes against the C_out that `estimate_c_out`
    produced -- an inflated asymptote makes the excess look like it is
    collapsing faster than it is, which inflates k on top.
    """
    data = simulate(coupling=coupling, seed=seed)
    print(f"coupling {coupling:.2f} (corr(k, N) = "
          f"{np.corrcoef(data['k'], data['occupants'])[0, 1]:+.2f})")
    _line(recover(data, legacy=True), "sequential (before)")
    _line(recover(data, legacy=True, true_c_out=True), "  + true C_out")
    _line(recover(data, legacy=True, true_k=True), "  + true k")
    _line(recover(data, legacy=True, true_c_out=True, true_k=True), "  + both true")
    _line(recover(data), "joint solve (after)")


if __name__ == "__main__":
    if "--sweep" in sys.argv:
        print("Recovery against how tightly ventilation is tied to occupancy.\n"
              "At coupling 1.0 they never vary independently, so nothing can\n"
              "separate them; the room's real coupling is unmeasured but lower.\n")
        for c in (0.0, 0.25, 0.5, 0.75, 1.0):
            ablate(coupling=c)
            print()
    elif "--ablate" in sys.argv:
        ablate()
    else:
        data = simulate()
        print(f"simulated {len(data)} minutes, "
              f"CO2 {data.reading.min():.0f}-{data.reading.max():.0f} ppm, "
              f"true occupancy {data.occupants.mean():.2f} on average\n")
        report(recover(data, report_solver=True))
