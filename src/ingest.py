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
"""
import base64
import hashlib
import hmac
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd

import paths

BASE = "https://api.switch-bot.com"
STORE = paths.RAW / "live.parquet"


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
    """Print the account's devices so the CO2 meter's deviceId can be found."""
    return pd.DataFrame(_get("/v1.1/devices")["deviceList"])


def read_sensor(device_id: str) -> dict:
    s = _get(f"/v1.1/devices/{device_id}/status")
    return {"ts": pd.Timestamp.utcnow().floor("min"),
            "temp": s.get("temperature"), "rh": s.get("humidity"),
            "co2": s.get("CO2"), "battery": s.get("battery")}


def append_reading(device_id: str, store: Path = STORE) -> dict:
    row = read_sensor(device_id)
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
        print("\nRe-run with the deviceId of the CO2 meter to store a reading.")
    else:
        print(append_reading(sys.argv[1]))
