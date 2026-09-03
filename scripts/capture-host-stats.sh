#!/usr/bin/env bash
set -euo pipefail

output_path="${1:?usage: capture-host-stats.sh OUTPUT_PATH INTERVAL_SECONDS CAMPAIGN_ID}"
interval_seconds="${2:-30}"
campaign_id="${3:?usage: capture-host-stats.sh OUTPUT_PATH INTERVAL_SECONDS CAMPAIGN_ID}"
mkdir -p "$(dirname "$output_path")"
printf 'HOST_STATS_SESSION_START campaign=%s timestamp=%s interval=%s\n' \
  "$campaign_id" "$(date --iso-8601=seconds)" "$interval_seconds" >> "$output_path"

while true; do
  {
    date --iso-8601=seconds
    awk '/MemAvailable|SwapFree|SwapTotal/ {print}' /proc/meminfo
    cat /proc/loadavg
    docker stats --no-stream --format '{{json .}}' guduck-go2rtc guduck-homeassistant 2>/dev/null || true
    if oom_output="$(dmesg --since '1 minute ago' 2>/dev/null)"; then
      printf '%s\n' "$oom_output" | grep -i -E 'out of memory|killed process' || true
    else
      echo 'OOM_CHECK_UNAVAILABLE'
    fi
  } >> "$output_path"
  sleep "$interval_seconds"
done
