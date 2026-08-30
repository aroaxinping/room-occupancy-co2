"""Load and clean the SwitchBot Meter Pro CO2 sensor export.

The source is a CSV exported from the vendor app. Once API ingestion exists
(SwitchBot API v1.1) only `load_raw` needs to change: the rest of the pipeline
works on the canonical frame returned by `load_clean`.
"""
from pathlib import Path

import pandas as pd
import numpy as np

import paths

RAW = paths.RAW / "meter_pro_co2_raw.csv"
COLS = ["ts", "temp", "rh", "co2", "abs_hum", "dew_point", "vpd"]

# The device reports every ~5-8 min; the app pads the gaps by repeating the last
# reading, so the per-minute grid in the CSV is false resolution.
CADENCE = "5min"
# 400 ppm is outdoor air: anything below is sensor error, not cleaner air.
CO2_FLOOR = 350
# abs_hum, dew_point and vpd are exact functions of temp and rh (verified to
# r=0.99999 against the Magnus equation), so they are dropped rather than fed
# to a model as if they carried independent information.
DERIVED = ["abs_hum", "dew_point", "vpd"]


def load_raw(path: Path = RAW) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = COLS
    df["ts"] = pd.to_datetime(df["ts"], format="%b %d, %Y %H:%M")
    return df.sort_values("ts").reset_index(drop=True)


def load_clean(path: Path = RAW, keep_derived: bool = False) -> pd.DataFrame:
    df = load_raw(path)
    df.loc[df["co2"] < CO2_FLOOR, "co2"] = np.nan
    df = df.set_index("ts").resample(CADENCE).median()
    # Interpolate only within a block; the multi-week outages stay NaN so that
    # whole days of data are never invented.
    df = df.interpolate(limit=6, limit_area="inside")
    df["block"] = (df["co2"].isna() & df["co2"].shift().notna()).cumsum()
    df = df[df["co2"].notna()].copy()
    df["baseline"] = df["co2"].rolling("24h").quantile(0.05)
    df["rate"] = df["co2"].diff() / (df.index.to_series().diff().dt.total_seconds() / 60)
    df["excess"] = df["co2"] - df["baseline"]
    return df if keep_derived else df.drop(columns=DERIVED)


if __name__ == "__main__":
    d = load_clean()
    d.to_parquet(paths.processed("co2_5min.parquet"))
    print(f"{len(d)} rows @ {CADENCE} | {d.index.min()} -> {d.index.max()} | {d.block.nunique()} blocks")
