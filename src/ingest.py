"""Poll the SwitchBot cloud API for the current sensor reading and append it.

The API exposes present state only -- there is no historical endpoint -- so the
history has to be accumulated by polling. That is also why the original CSV
export is kept: it is the only record of everything before ingestion started.

Credentials are read from the macOS Keychain only. They are never written to
disk in plaintext and never enter the repository. To store them once:

    security add-generic-password -a "$USER" -s switchbot-token  -w
    security add-generic-password -a "$USER" -s switchbot-secret -w

(the -w with no value prompts for the secret without echoing it to the shell
history). A SwitchBot token grants control of every device on the account --
locks and cameras included -- so it is treated as a high-value credential
rather than as project configuration.

Run it from cron or a systemd timer every 5 minutes, matching the sensor's own
refresh rate. At that cadence this uses 288 of the 10000 daily calls allowed.

Two sensors, and what tells them apart
--------------------------------------
The room holds two devices, and `DEVICES` below is the single place that says
what each one is and where its readings go:

  meterpro-co2  the Meter Pro (CO2), device_type W1079001. Temperature,
                humidity and CO2. The original sensor, and what every analysis
                module means by "the sensor".
  rack          a WoIOSensor, named "Outdoor Meter" in the vendor's app but
                physically inside a server rack in the same room. Temperature
                and humidity only -- neither the hardware nor the history
                endpoint carries CO2. The rack dissipates a roughly constant
                load, so the difference between its interior temperature and
                the room's responds to airflow: a handle on ventilation that
                does not run through CO2 at all.

A device carries its own channel list, so a reading is built from what that
hardware actually measures rather than from a fixed set of columns with a
permanent hole in one of them.
"""
import base64
import hashlib
import hmac
import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd

import paths

BASE = "https://api.switch-bot.com"


@dataclass(frozen=True)
class Device:
    """One physical sensor: what it measures, and where its readings live.

    `root` is a directory immediately under DATA_DIR, and it carries the whole
    of the storage-layout decision: each device owns a partition tree, and the
    tree a row sits in *is* its device label. src/pipeline.py records why that
    was chosen over a device column in one shared tree.

    `vendor_types` and `name_hints` are used only by the backfill, which has to
    find the device on the account by itself. The five-minute poll is handed a
    deviceId on the command line and needs neither.
    """
    slug: str
    label: str
    root: str
    channels: tuple
    vendor_types: tuple = ()
    name_hints: tuple = ()

    @property
    def has_co2(self) -> bool:
        return "co2" in self.channels

    @property
    def store(self) -> Path:
        """The legacy single-file store, kept per device for symmetry."""
        return paths.RAW / ("live.parquet" if self.root == "live"
                            else f"{self.root}.parquet")


METER_PRO_CO2 = Device(
    slug="meterpro-co2",
    label="Meter Pro (CO2)",
    root="live",
    # This order is the order the existing partitions already hold, so nothing
    # written from here reorders a schema that is 124k rows deep.
    channels=("temp", "rh", "co2"),
    vendor_types=("W1079001", "W1079000"),
    name_hints=("meter pro", "meterpro", "co2"),
)

RACK = Device(
    slug="rack",
    label="Rack sensor (WoIOSensor)",
    root="live-rack",
    channels=("temp", "rh"),
    # A history source may report device types as vendor names and others as
    # numeric codes, and this one has never been seen from here -- so the match
    # is deliberately loose, falls back to the device name, and can be
    # overridden with an explicit MAC (see src/backfill.py --mac).
    vendor_types=("WoIOSensor",),
    name_hints=("outdoor", "iosensor"),
)

DEVICES = {d.slug: d for d in (METER_PRO_CO2, RACK)}
# What an unqualified "the sensor" means, everywhere: the CO2 meter. Every
# device-aware function defaults to it, so callers written before there was a
# second device keep working unchanged.
DEFAULT_DEVICE = METER_PRO_CO2
STORE = DEFAULT_DEVICE.store


def device_for(slug) -> Device:
    """Resolve a slug (or a Device, which passes through) to a Device."""
    if isinstance(slug, Device):
        return slug
    if slug is None:
        return DEFAULT_DEVICE
    try:
        return DEVICES[slug]
    except KeyError:
        raise SystemExit(f"unknown device {slug!r}; known: "
                         f"{', '.join(sorted(DEVICES))}") from None


def _credential(name: str) -> str:
    """Read one secret from the macOS Keychain.

    Deliberately without an environment-variable fallback: exporting a token in
    a shell writes it to ~/.zsh_history in plaintext, permanently, which is the
    exposure this is meant to avoid.
    """
    try:
        return subprocess.run(
            ["security", "find-generic-password", "-a", os.environ["USER"],
             "-s", f"switchbot-{name}", "-w"],
            capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise RuntimeError(
            f"No switchbot-{name} in the Keychain. See this module's docstring."
        ) from exc


def _headers() -> dict:
    token = _credential("token")
    secret = _credential("secret")
    nonce = str(uuid.uuid4())
    t = str(int(time.time() * 1000))
    # The signature is base64 of the HMAC, then uppercased -- skipping the
    # uppercase step is the usual cause of a 401 here.
    sign = base64.b64encode(
        hmac.new(secret.encode(), f"{token}{t}{nonce}".encode(), hashlib.sha256).digest()
    ).decode().upper()
    return {"Authorization": token, "sign": sign, "t": t, "nonce": nonce,
            "Content-Type": "application/json; charset=utf8"}


def _get(path: str) -> dict:
    with urlopen(Request(BASE + path, headers=_headers()), timeout=20) as r:
        body = json.loads(r.read())
    if body.get("statusCode") != 100:
        raise RuntimeError(f"SwitchBot API error: {body.get('statusCode')} {body.get('message')}")
    return body["body"]


def list_devices() -> pd.DataFrame:
    """Print the account's devices so each sensor's deviceId can be found."""
    return pd.DataFrame(_get("/v1.1/devices")["deviceList"])


def reading_from_status(status: dict, device=DEFAULT_DEVICE) -> dict:
    """Shape one status payload into a row for `device`. No network.

    Split out of `read_sensor` so the field mapping -- including the guard
    below -- is exercisable without credentials.

    The guard is against the one mistake that would actually corrupt a tree:
    handing the rack's slug the CO2 meter's deviceId, or the reverse, writes
    one sensor's readings into the other's partitions where nothing downstream
    could tell. A device with no CO2 channel that reports CO2 is that mistake,
    and it fails loudly rather than accumulating. The converse -- a CO2 device
    whose payload happens to omit CO2 -- is left alone: it is what a transient
    null looks like, and refusing it would stop the primary collector over
    something the existing code has always tolerated.
    """
    if not device.has_co2 and status.get("CO2") is not None:
        raise RuntimeError(
            f"device {device.slug!r} has no CO2 channel but the API reported "
            f"CO2={status.get('CO2')}: this deviceId is a CO2 meter, and "
            f"storing it under {device.slug!r} would mix two sensors in one "
            f"partition tree")
    row = {"ts": pd.Timestamp.utcnow().floor("min"),
           "temp": status.get("temperature"), "rh": status.get("humidity")}
    if device.has_co2:
        row["co2"] = status.get("CO2")
    row["battery"] = status.get("battery")
    return row


def read_sensor(device_id: str, device=DEFAULT_DEVICE) -> dict:
    return reading_from_status(
        _get(f"/v1.1/devices/{device_id}/status"), device_for(device))


def append_reading(device_id: str, store: Path = None, device=DEFAULT_DEVICE) -> dict:
    device = device_for(device)
    store = device.store if store is None else store
    row = read_sensor(device_id, device)
    df = pd.DataFrame([row]).set_index("ts")
    if store.exists():
        df = pd.concat([pd.read_parquet(store), df])
        # The device does not always refresh between polls, so identical
        # timestamps are expected and only the last one is kept.
        df = df[~df.index.duplicated(keep="last")].sort_index()
    store.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(store)
    return row


if __name__ == "__main__":
    import sys

    # The deviceId identifies the owner's hardware, so it is passed in rather
    # than committed.
    if len(sys.argv) < 2:
        print(list_devices()[["deviceId", "deviceName", "deviceType"]].to_string(index=False))
        print("\nRe-run with a deviceId to store a reading:")
        for slug, dev in sorted(DEVICES.items()):
            print(f"    python3 src/ingest.py <deviceId> {slug}"
                  f"    {dev.label} -> DATA_DIR/{dev.root}")
    else:
        print(append_reading(sys.argv[1],
                             device=sys.argv[2] if len(sys.argv) > 2 else None))
