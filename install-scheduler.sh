#!/bin/sh
# Installs the ingestion poller as a launchd agent.
#
#     ./install-scheduler.sh <co2DeviceId> [rackDeviceId]
#
# The deviceIds identify the owner's hardware, so they are supplied here rather
# than committed. The generated plist is written to ~/Library/LaunchAgents,
# outside the repository.
#
# The second id is the rack sensor (src/ingest.py describes it) and is
# optional: with it, the one agent polls both sensors in a single run.
set -e
[ -n "$1" ] || { echo "usage: $0 <co2DeviceId> [rackDeviceId]   (list them with: python3 src/ingest.py)"; exit 1; }
# The ids are substituted into a sed script and then into XML, so anything but
# hex would either corrupt the plist silently (sed treats & as the match) or
# inject a key into it. SwitchBot ids are hex, so require that.
for id in "$1" ${2:+"$2"}; do
    case "$id" in *[!0-9A-Fa-f]*) echo "deviceId must be hexadecimal: $id"; exit 1;; esac
done

REPO=$(cd "$(dirname "$0")" && pwd)
PYTHON=$(command -v python3)
DATA="${ROOM_OCCUPANCY_DATA:-$HOME/Library/Application Support/room-occupancy-co2}"
PLIST="$HOME/Library/LaunchAgents/com.local.room-occupancy-co2.plist"

# With no second id the placeholder line is deleted rather than blanked, so a
# one-sensor install produces exactly the plist it always did.
if [ -n "$2" ]; then
    EXTRA_RULE="s|^__EXTRA_TARGETS__\$|        <string>rack=$2</string>|"
else
    EXTRA_RULE="/^__EXTRA_TARGETS__\$/d"
fi

mkdir -p "$DATA/logs" "$HOME/Library/LaunchAgents"
sed -e "s|__PYTHON__|$PYTHON|" -e "s|__REPO__|$REPO|" \
    -e "s|__DEVICE_ID__|$1|" -e "s|__DATA__|$DATA|" -e "$EXTRA_RULE" \
    "$REPO/scheduler.plist.template" > "$PLIST"

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "installed: $PLIST"
echo "polling every 300s${2:+ (both sensors)}. check with:  python3 $REPO/src/pipeline.py --status"
echo "stop with:  launchctl unload $PLIST"
