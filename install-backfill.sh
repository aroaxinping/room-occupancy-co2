#!/bin/sh
# Installs the daily gap-repair job as a launchd agent.
#
# Takes no arguments: backfill.py resolves the device itself and reads its
# credentials from the Keychain.
set -e
REPO=$(cd "$(dirname "$0")" && pwd)
PYTHON=$(command -v python3)
DATA="${ROOM_OCCUPANCY_DATA:-$HOME/Library/Application Support/room-occupancy-co2}"
PLIST="$HOME/Library/LaunchAgents/com.local.room-occupancy-backfill.plist"

mkdir -p "$DATA/logs" "$HOME/Library/LaunchAgents"

# sed treats & as the whole match and | as the delimiter, so a path containing
# either corrupts the plist silently rather than failing. Plausible for a
# directory like "R&D".
for path in "$PYTHON" "$REPO" "$DATA"; do
    case "$path" in *[\&\|]*) echo "path contains & or |, which sed would mangle: $path"; exit 1;; esac
done

sed -e "s|__PYTHON__|$PYTHON|" -e "s|__REPO__|$REPO|" -e "s|__DATA__|$DATA|" \
    "$REPO/backfill.plist.template" > "$PLIST"

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "installed: $PLIST"
echo "runs daily at 05:30. check with:  python3 $REPO/src/pipeline.py x --status"
echo "stop with:  launchctl unload $PLIST"
