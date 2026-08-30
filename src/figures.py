"""Plots for the notebooks, built so that a real date cannot reach a figure.

The repository publishes aggregates and withholds the dated record (see the
README). Rather than trusting whoever writes the next notebook to remember
that, the rule lives here.

Two things are enforced, not merely intended:

* `relative_window` discards the index and plots against elapsed hours, and it
  chooses *which* window at random. A committed window selector would be an
  exact pointer into the calendar: anyone who learned the series start date
  could invert it and date every feature on the plot. The choice is therefore
  deliberately not recorded, and the window never begins on a day boundary, so
  time of day cannot be read off the axis either.
* `relative_window` will only draw an allowlisted quantity. For anything that
  tracks outdoor conditions the randomised start is not enough -- an attacker
  matching an unknown window simply slides it against every candidate week --
  and a denylist of such channels kept missing aliases, so the list names what
  is permitted instead.
* Every string these functions are given -- titles, legend entries, tick
  labels, vline annotations -- is checked against a date pattern before it is
  drawn. The axis was never the only way to put a date on a figure.

The second guarantee stops at this module's boundary. Every function accepts
`ax`, so a caller holding its own axes can still call `ax.text` or
`fig.suptitle` itself, and nothing here sees that. The pre-commit hook, which
scans the rendered notebook, is the backstop for that path.
"""
import re
import secrets

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# Channels that MAY be drawn as a time window. Everything else is refused.
#
# This was first written as a denylist of weather-tracking channels and that was
# the wrong shape: it missed `abs_hum` and `dpt`, both real columns in this
# project's own data, and dew point is the worst of them -- an air-mass property,
# and so the most spatially coherent channel there is to cross-match against
# public weather records. A denylist also cannot survive a renamed column.
#
# An allowlist fails closed instead. The set of quantities this project has any
# reason to plot against time is small and driven by occupancy rather than by
# the weather, so nothing is lost by naming it exhaustively.
WINDOW_PLOTTABLE = ("co2", "n", "n_smooth", "occupants", "k", "ach")

_DATE = re.compile(
    r"20\d{2}-\d{2}-\d{2}"
    r"|\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b"
    r"|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{1,2}\b"
    r"|\b\d{1,2}\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b"
    r"|\b(mon|tues|wednes|thurs|fri|satur|sun)day\b"
    r"|\b(mon|tue|wed|thu|fri|sat|sun)\b"
    r"|\b20\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\b",
    re.I)


def _safe(*strings):
    """Refuse any caption that names a date or a weekday."""
    for s in strings:
        for value in np.atleast_1d(s):
            if isinstance(value, str) and _DATE.search(value):
                raise ValueError(
                    f"refusing to draw {value!r}: figures in this repository "
                    "carry no dates or weekdays (see the README)")

PALETTE = {"line": "#2b6cb0", "accent": "#c05621", "muted": "#a0aec0"}


def _style(ax, xlabel, ylabel, title):
    _safe(xlabel, ylabel, title)
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
    _safe(*[label for _, label in vlines])
    ax.hist(np.asarray(values), bins=bins, color=PALETTE["line"], alpha=0.75)
    for x, label in vlines:
        ax.axvline(x, color=PALETTE["accent"], ls="--", lw=1.4)
        ax.text(x, ax.get_ylim()[1] * 0.92, f" {label}", color=PALETTE["accent"], fontsize=9)
    return _style(ax, xlabel, "episodes", title)


def relative_window(frame: pd.DataFrame, columns, days: int = 7,
                    title: str = "", ylabel: str = "", ax=None):
    """A stretch of the series against elapsed hours, never against dates.

    The index is discarded and replaced by hours-from-start, so the shape of
    the curve survives and the calendar does not.

    Which window is drawn is chosen at random on every call and is not an
    argument. A committed selector would be an exact index into the series:
    given the start date it inverts, and every peak on the plot acquires a
    date. The start is also never a whole number of days, so the axis does not
    silently encode hour of day.
    """
    _safe(title, ylabel, *np.atleast_1d(columns))
    for col in np.atleast_1d(columns):
        if str(col).lower() not in WINDOW_PLOTTABLE:
            raise ValueError(
                f"refusing to plot {col!r} as a time window. Only "
                f"{', '.join(WINDOW_PLOTTABLE)} may be drawn against time; "
                "anything tracking outdoor conditions can be aligned against "
                "public weather records. Use hour_profile or distribution.")
    step = frame.index.to_series().diff().median()
    n = int(pd.Timedelta(days=days) / step)
    if len(frame) < n + 2:
        raise ValueError("series is shorter than the requested window")

    # secrets rather than numpy: no seed exists to be recorded or recovered.
    start = secrets.randbelow(len(frame) - n - 1)
    window = frame.iloc[start:start + n]

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
    _safe(*labels)
    x = np.arange(len(labels))
    ax.bar(x - 0.2, expected, 0.4, label="known", color=PALETTE["muted"])
    ax.bar(x + 0.2, values, 0.4, label="estimated", color=PALETTE["line"])
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=20, ha="right")
    ax.legend(frameon=False, fontsize=9)
    return _style(ax, "", "occupants", title)
