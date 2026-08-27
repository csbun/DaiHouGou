#!/usr/bin/env bash
set -euo pipefail

output_path="${1:?usage: capture-host-stats.sh OUTPUT_PATH [INTERVAL_SECONDS]}"
interval_seconds="${2:-30}"
mkdir -p "$(dirname "$output_path")"

while true; do
  {
    date --iso-8601=seconds
    awk '/MemAvailable|SwapFree|SwapTotal/ {print}' /proc/meminfo
    cat /proc/loadavg
    docker stats --no-stream --format '{{json .}}' daihougou-go2rtc daihougou-homeassistant 2>/dev/null || true
    dmesg --since '1 minute ago' 2>/dev/null | grep -i -E 'out of memory|killed process' || true
  } >> "$output_path"
  sleep "$interval_seconds"
done
