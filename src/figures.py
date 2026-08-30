"""Plots for the notebooks, built so that a real date cannot reach a figure.

The repository publishes aggregates and withholds the dated record (see the
README). Rather than trusting whoever writes the next notebook to remember
that, the rule lives here: every function below either aggregates over the
whole series or plots a window against elapsed hours. None of them accepts or
renders a calendar date, and `relative_window` strips the index before the axis
is ever drawn.
"""
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

PALETTE = {"line": "#2b6cb0", "accent": "#c05621", "muted": "#a0aec0"}


def _style(ax, xlabel, ylabel, title):
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(alpha=0.25, linewidth=0.6)
    return ax


def hour_profile(series: pd.Series, ylabel: str, title: str, ax=None):
    """Median by hour of day, with the interquartile band."""
    by_hour = series.groupby(series.index.hour)
    med, lo, hi = by_hour.median(), by_hour.quantile(0.25), by_hour.quantile(0.75)
    ax = ax or plt.subplots(figsize=(8, 3.4))[1]
    ax.fill_between(med.index, lo, hi, color=PALETTE["line"], alpha=0.18, lw=0)
    ax.plot(med.index, med, color=PALETTE["line"], lw=2)
    ax.set_xticks(range(0, 24, 3))
    return _style(ax, "hour of day", ylabel, title)


def distribution(values, xlabel: str, title: str, bins=60, vlines=(), ax=None):
    ax = ax or plt.subplots(figsize=(8, 3.4))[1]
    ax.hist(np.asarray(values), bins=bins, color=PALETTE["line"], alpha=0.75)
    for x, label in vlines:
        ax.axvline(x, color=PALETTE["accent"], ls="--", lw=1.4)
        ax.text(x, ax.get_ylim()[1] * 0.92, f" {label}", color=PALETTE["accent"], fontsize=9)
    return _style(ax, xlabel, "episodes", title)


def relative_window(frame: pd.DataFrame, columns, days: int = 7, offset: int = 0,
                    title: str = "", ylabel: str = "", ax=None):
    """A stretch of the series against elapsed hours, never against dates.

    The index is discarded and replaced by hours-from-start, so the shape of
    the curve survives and the calendar does not. `offset` selects which window
    without naming it.
    """
    step = frame.index.to_series().diff().median()
    n = int(pd.Timedelta(days=days) / step)
    start = int(offset * n)
    window = frame.iloc[start:start + n]
    if len(window) < n // 2:
        raise ValueError("window falls outside the data; lower `offset`")

    hours = np.arange(len(window)) * step.total_seconds() / 3600
    ax = ax or plt.subplots(figsize=(11, 3.4))[1]
    for col, colour in zip(np.atleast_1d(columns), (PALETTE["line"], PALETTE["accent"])):
        ax.plot(hours, window[col].to_numpy(), color=colour, lw=1.3, label=col)
    ax.set_xticks(np.arange(0, days * 24 + 1, 24))
    ax.legend(frameon=False, fontsize=9)
    return _style(ax, f"hours elapsed within a representative {days}-day window",
                  ylabel, title)


def comparison_bars(labels, values, expected, title: str, ax=None):
    """Estimated occupancy against independently known values."""
    ax = ax or plt.subplots(figsize=(8, 3.4))[1]
    x = np.arange(len(labels))
    ax.bar(x - 0.2, expected, 0.4, label="known", color=PALETTE["muted"])
    ax.bar(x + 0.2, values, 0.4, label="estimated", color=PALETTE["line"])
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=20, ha="right")
    ax.legend(frameon=False, fontsize=9)
    return _style(ax, "", "occupants", title)
