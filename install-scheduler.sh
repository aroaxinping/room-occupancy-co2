#!/bin/sh
# Installs the ingestion poller as a launchd agent.
#
#     ./install-scheduler.sh <deviceId>
#
# The deviceId identifies the owner's hardware, so it is supplied here rather
# than committed. The generated plist is written to ~/Library/LaunchAgents,
# outside the repository.
set -e
[ -n "$1" ] || { echo "usage: $0 <deviceId>   (list them with: python3 src/pipeline.py)"; exit 1; }

REPO=$(cd "$(dirname "$0")" && pwd)
PYTHON=$(command -v python3)
DATA="${ROOM_OCCUPANCY_DATA:-$HOME/Library/Application Support/room-occupancy-co2}"
PLIST="$HOME/Library/LaunchAgents/com.local.room-occupancy-co2.plist"

mkdir -p "$DATA/logs" "$HOME/Library/LaunchAgents"
sed -e "s|__PYTHON__|$PYTHON|" -e "s|__REPO__|$REPO|" \
    -e "s|__DEVICE_ID__|$1|" -e "s|__DATA__|$DATA|" \
    "$REPO/scheduler.plist.template" > "$PLIST"

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "installed: $PLIST"
echo "polling every 300s. check with:  python3 $REPO/src/pipeline.py x --status"
echo "stop with:  launchctl unload $PLIST"
