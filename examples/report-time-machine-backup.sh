#!/bin/bash
# Run on the Mac that makes the backup. See README for setup.
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "Usage: $0 <backup volume mount point> <Home Assistant webhook URL>" >&2
    exit 2
fi

backup_volume=$1
webhook_url=$2
status=destination_unavailable
timestamp=""

# The explicit destination keeps a backup on a different disk from refreshing this sensor.
if [[ -d "$backup_volume" ]] && /sbin/mount | /usr/bin/grep -F -- " on $backup_volume (" >/dev/null; then
    status=read_error
    if backup=$(/usr/bin/tmutil latestbackup -d "$backup_volume" -t); then
        # Some macOS versions return a path despite -t.
        if [[ "$backup" =~ ([0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{6})([.]backup)?/?$ ]] &&
            timestamp=$(/bin/date -j -f '%Y-%m-%d-%H%M%S' "${BASH_REMATCH[1]}" '+%Y-%m-%dT%H:%M:%S%z'); then
            status=ok
        fi
    fi
fi

if [[ "$status" == ok ]]; then
    payload="{\"status\":\"ok\",\"backup_at\":\"$timestamp\"}"
else
    payload="{\"status\":\"$status\"}"
fi

/usr/bin/curl --fail --silent --show-error --max-time 15 \
    --header 'Content-Type: application/json' \
    --data "$payload" \
    -- "$webhook_url"
echo "Time Machine report sent ($status)."
