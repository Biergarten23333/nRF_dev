#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PID_FILE="${1:-outputs/logs/axis_dop_gpu_dense_25mm_drop1-4.pid}"
LOG_FILE="${2:-outputs/logs/axis_dop_gpu_dense_25mm_drop1-4.watcher.log}"
DROP_CSV="DATASETS/features/axis_dop_gpu_dense_25mm_drop1-4.csv"

mkdir -p "$(dirname "$LOG_FILE")"

if [[ ! -s "$PID_FILE" ]]; then
  echo "$(date -Is) missing pid file: $PID_FILE" >> "$LOG_FILE"
  exit 1
fi

pid="$(tr -d '[:space:]' < "$PID_FILE")"
echo "$(date -Is) watching pid=$pid" >> "$LOG_FILE"

while kill -0 "$pid" 2>/dev/null; do
  sleep 60
done

echo "$(date -Is) pid=$pid exited; checking output" >> "$LOG_FILE"

if [[ ! -s "$DROP_CSV" ]]; then
  echo "$(date -Is) missing output csv: $DROP_CSV" >> "$LOG_FILE"
  exit 1
fi

python3 scripts/analyze_axis_dop_dropk_redundancy.py >> "$LOG_FILE" 2>&1
echo "$(date -Is) drop1-4 analysis complete" >> "$LOG_FILE"
