"""Where the backfill's history comes from, resolved at run time.

An ingestion module should not hardcode one supplier's client. The poll in
src/ingest.py has to know its API because it *is* that API's client; the
backfill does not -- it needs stored readings for a device over a date range,
and any store that can answer that question will do: a directory of exported
CSV, a second sensor network, a local database, a cloud account. So the
question is asked through this module, and which store answers it is
configuration rather than an import.

The contract
------------
A history source is a callable:

    source(device, start, end, device_id=None) -> iterable of mappings

    device      an ingest.Device -- what it measures (`channels`, `has_co2`),
                what it is called (`slug`, `label`) and, for a source that has
                to identify the device in some remote account, `vendor_types`
                and `name_hints`. A source that can serve only one kind of
                sensor should raise rather than return another's readings: a
                wrong device here writes one sensor's history into the other's
                partition tree, which nothing downstream can detect.
    start, end  datetime.date. `start` is inclusive, `end` exclusive.
    device_id   an optional handle from `--device-id`, meaning "this record,
                whatever the matching rules would have said". Its form belongs
                to the source -- a MAC, a file stem, a primary key. Sources
                that identify the device themselves may ignore it.

Each mapping is one reading. Only `timestamp` is required:

    "timestamp"       when the reading was taken (see below)
    "temperature_c"   degrees Celsius, or None
    "humidity_pct"    relative humidity in percent, or None
    "co2_ppm"         ppm, or None. Absent for a device with no CO2 channel,
                      and absent is not the same as None: a source that cannot
                      serve CO2 for a CO2 device should leave the key out
                      rather than fill a column with nulls.

Unrecognised keys are ignored. Returning rows outside the requested range is
allowed and costs only bandwidth -- the merge is keyed on the timestamp and is
idempotent -- but the ledger only credits the days it asked for. Returning
nothing is allowed too, and means "this source holds nothing for that range";
it is not treated as an error, because for a store fed by an intermittent
uploader an empty answer is ordinary.

Timezone: the one thing a source must declare
---------------------------------------------
This is the contract's sharp edge, and it is stated rather than guessed
because guessing it wrong once cost a day of analysis. A source that formats
its points in local wall-clock time and attaches no offset produces strings
that read exactly like UTC and are wrong by the local offset. Labelling them
UTC shifted a whole backfilled record by two hours; every window analysed was
then not the window it was labelled with, and the error was invisible until a
timestamp landed in the future. src/backfill.py still refuses any response
whose newest reading is ahead of the clock, which is how it was caught, and
that guard applies to every source regardless of what it declares.

So a source declares one of:

    "local-naive"   wall-clock in *this machine's* zone, no offset attached.
                    Localised, then converted to UTC.
    "utc-naive"     already UTC, no offset attached. Labelled UTC.
    "aware"         every timestamp carries an offset or a tzinfo. Converted.

There is no default. A source that declares nothing is refused at load time,
because the failure mode of a wrong assumption here is silent and the failure
mode of an explicit refusal is a message.

Configuring one
---------------
The configuration lives outside the repository, beside the data and for the
same reason (src/paths.py): which account or which export directory holds this
room's history is a fact about the room. By default it is read from

    DATA_DIR/history_source.json

and ROOM_OCCUPANCY_HISTORY_SOURCE overrides that path. The file names either a
built-in source:

    {"builtin": "csv-directory",
     "options": {"directory": "~/exports"},
     "timestamps": "utc-naive"}

or a factory to import -- a module of your own, anywhere on the machine:

    {"factory": "room_history_adapter:open_source",
     "path": "~/adapters",
     "options": {"region": "eu"},
     "timestamps": "local-naive"}

`path` (a string or a list) is prepended to sys.path, so the adapter can live
outside this repository -- which is where an adapter holding account details,
or wrapping a client this project does not depend on, belongs. `options` are
passed to the factory as keyword arguments. The factory returns either a
callable matching the contract above or an object with a `.rows` method; if it
sets a `timestamps` attribute the config need not repeat it, and if both are
present the config wins.

Nothing here reads a credential. A source that needs one gets it however it
likes -- a keyring, a file only it can read -- and this module never sees it.
"""
import csv
import importlib
import json
import os
import sys
from datetime import date
from pathlib import Path

import paths

CONFIG_PATH = Path(os.environ.get(
    "ROOM_OCCUPANCY_HISTORY_SOURCE", paths.DATA_DIR / "history_source.json"))

# The three answers to "what does a naked timestamp from this source mean".
CONVENTIONS = ("local-naive", "utc-naive", "aware")


class NotConfigured(RuntimeError):
    """No history source is configured, or the configuration is unusable."""


def _config_help(path=None) -> str:
    path = CONFIG_PATH if path is None else path
    return (f"Write one to {path} (or point ROOM_OCCUPANCY_HISTORY_SOURCE at "
            f"another file): a JSON object naming either a built-in source -- "
            f"{', '.join(sorted(BUILTINS))} -- or a factory to import, plus "
            f"the timestamp convention it uses ({', '.join(CONVENTIONS)}). "
            f"src/history_source.py documents the contract in full.")


# --------------------------------------------------------------------------
# A built-in source, so the contract has a second implementation in the tree
# and can be exercised without a network or an account of any kind.
# --------------------------------------------------------------------------

def csv_directory(directory, suffix: str = ".csv",
                  timestamps: str = None):
    """Readings from `<directory>/<device>.csv`, one file per device.

    The file needs a header row; `timestamp` is the only column it must have,
    and `temperature_c`, `humidity_pct` and `co2_ppm` are read when present.
    The device's slug names the file unless `--device-id` overrides it, which
    is how one export directory can hold several rooms.

    Rows are filtered to the requested range on the date prefix of the
    timestamp, so a file spanning the whole record costs a scan rather than a
    day of memory. A timestamp this cannot read is passed through and left for
    the caller's parser to reject.
    """
    directory = Path(directory).expanduser()

    def rows(device, start: date, end: date, device_id: str = None):
        path = directory / f"{device_id or device.slug}{suffix}"
        if not path.exists():
            raise FileNotFoundError(
                f"no history file for {device.slug!r} at {path}; a "
                f"csv-directory source expects one file per device, named "
                f"after its slug or after --device-id")
        out = []
        with path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                stamp = (row.get("timestamp") or "").strip()
                day = _leading_date(stamp)
                if day is not None and not (start <= day < end):
                    continue
                reading = {"timestamp": stamp}
                for key in ("temperature_c", "humidity_pct", "co2_ppm"):
                    if key in row:
                        reading[key] = _number(row[key])
                out.append(reading)
        return out

    if timestamps:
        rows.timestamps = timestamps
    return rows


def _leading_date(stamp: str):
    try:
        return date.fromisoformat(stamp[:10])
    except (ValueError, TypeError):
        return None


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


BUILTINS = {"csv-directory": csv_directory}


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------

class Source:
    """A resolved history source: how to ask it, and what its clocks mean."""

    def __init__(self, rows, timestamps: str, name: str):
        if timestamps not in CONVENTIONS:
            raise NotConfigured(
                f"history source {name!r} declares timestamp convention "
                f"{timestamps!r}; it must be one of {', '.join(CONVENTIONS)}. "
                f"This is not defaulted on purpose: reading local wall-clock "
                f"timestamps as UTC shifts the whole record by the local "
                f"offset and stays invisible until one lands in the future.")
        self.rows = rows
        self.timestamps = timestamps
        self.name = name

    def __call__(self, device, start: date, end: date, device_id: str = None):
        return self.rows(device, start, end, device_id=device_id)

    def __repr__(self):
        return f"<history source {self.name!r} timestamps={self.timestamps!r}>"


def read_config(path=None) -> dict:
    """The parsed configuration, or None if there is no file at all."""
    path = CONFIG_PATH if path is None else Path(path)
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as exc:
        raise NotConfigured(
            f"history source configuration at {path} is unreadable ({exc}). "
            + _config_help(path)) from exc
    if not isinstance(raw, dict):
        raise NotConfigured(f"history source configuration at {path} is not a "
                            f"JSON object. " + _config_help(path))
    return raw


def build(config: dict, name: str = "configured") -> Source:
    """A Source from a configuration dict. No disk read, no clock.

    Split from `resolve` so a caller -- or a test -- can build a source from a
    literal dict without a file anywhere.
    """
    options = config.get("options") or {}
    if not isinstance(options, dict):
        raise NotConfigured("history source 'options' must be a JSON object")

    builtin, factory = config.get("builtin"), config.get("factory")
    if builtin and factory:
        raise NotConfigured("history source names both 'builtin' and "
                            "'factory'; it can have only one")
    if builtin:
        try:
            maker = BUILTINS[builtin]
        except KeyError:
            raise NotConfigured(
                f"unknown built-in history source {builtin!r}; this build has "
                f"{', '.join(sorted(BUILTINS))}") from None
        name = builtin
    elif factory:
        maker = _import_factory(factory, config.get("path"))
        name = factory
    else:
        raise NotConfigured("history source names neither 'builtin' nor "
                            "'factory'. " + _config_help())

    made = maker(**options)
    rows = made.rows if hasattr(made, "rows") else made
    if not callable(rows):
        raise NotConfigured(
            f"history source {name!r} produced {made!r}, which is neither "
            f"callable nor an object with a .rows method")
    convention = config.get("timestamps") or getattr(made, "timestamps", None)
    if convention is None:
        raise NotConfigured(
            f"history source {name!r} does not say which timezone convention "
            f"its timestamps use. Add \"timestamps\" to the configuration, or "
            f"set it on the source: one of {', '.join(CONVENTIONS)}.")
    return Source(rows, convention, name)


def _import_factory(spec: str, extra_path=None):
    """Import `module:attribute`, optionally after widening sys.path.

    The adapter is expected to live outside this repository -- that is the
    point of it being configuration -- so the config may name directories to
    import it from.
    """
    for entry in ([extra_path] if isinstance(extra_path, str) else extra_path or []):
        entry = str(Path(entry).expanduser())
        if entry not in sys.path:
            sys.path.insert(0, entry)
    module_name, _, attribute = spec.partition(":")
    if not module_name or not attribute:
        raise NotConfigured(
            f"history source factory {spec!r} is not of the form "
            f"'module:attribute'")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise NotConfigured(
            f"cannot import history source module {module_name!r} ({exc}). "
            f"If it lives outside this repository, add its directory as "
            f"\"path\" in the configuration.") from exc
    try:
        return getattr(module, attribute)
    except AttributeError:
        raise NotConfigured(f"module {module_name!r} has no {attribute!r}"
                            ) from None


def resolve(path=None) -> Source:
    """The configured source, or a NotConfigured explaining how to configure one."""
    path = CONFIG_PATH if path is None else Path(path)
    config = read_config(path)
    if config is None:
        raise NotConfigured("no history source is configured, so there is "
                            "nowhere to fetch missing readings from. "
                            + _config_help(path))
    return build(config)


if __name__ == "__main__":
    # Say what is configured, without asking it for anything: enough to tell a
    # missing configuration from a broken one before a scheduled job does.
    try:
        source = resolve()
    except NotConfigured as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
    print(f"{source}\n  configured in {CONFIG_PATH}")
