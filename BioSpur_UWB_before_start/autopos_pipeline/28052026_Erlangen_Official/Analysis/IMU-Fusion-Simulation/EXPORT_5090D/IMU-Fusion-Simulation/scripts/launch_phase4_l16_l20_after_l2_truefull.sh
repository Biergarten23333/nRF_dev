#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start"
SIM_ROOT="$ROOT/autopos_pipeline/28052026_Erlangen_Official/Analysis/IMU-Fusion-Simulation"
RUN_BASE="$SIM_ROOT/runs/phase4_algorithm_factory"
RUNNER="$SIM_ROOT/scripts/run_phase4_l2_singleI_full_factory.py"
LAUNCH_LOG_DIR="$RUN_BASE/launch_logs"
WORKERS_PER_SENSOR="${WORKERS_PER_SENSOR:-6}"

mkdir -p "$LAUNCH_LOG_DIR"

WAIT_RUNS=(
  "$RUN_BASE/phase4_L2_TRUEFULL_RANKING_S01_2x1080ti_20260605T164047Z"
  "$RUN_BASE/phase4_L2_TRUEFULL_RANKING_S02_2x1080ti_20260605T164047Z"
  "$RUN_BASE/phase4_L2_TRUEFULL_RANKING_S03_2x1080ti_20260605T164047Z"
  "$RUN_BASE/phase4_L2_TRUEFULL_RANKING_S04_2x1080ti_20260605T164047Z"
)

echo "[l16-l20-queue] started $(date -Is)"
echo "[l16-l20-queue] waiting for L2 TRUEFULL S01-S04 to complete"
echo "[l16-l20-queue] workers_per_sensor=$WORKERS_PER_SENSOR"

while true; do
  complete=0
  active=0
  failed=0

  for run_dir in "${WAIT_RUNS[@]}"; do
    run_id="$(basename "$run_dir")"
    manifest="$run_dir/manifest.json"
    if [[ -f "$manifest" ]] && grep -q '"phase_status": "complete"' "$manifest"; then
      complete=$((complete + 1))
    elif pgrep -f "$run_id" >/dev/null; then
      active=$((active + 1))
    else
      echo "[l16-l20-queue] waiting run is neither active nor complete: $run_id"
      failed=$((failed + 1))
    fi
  done

  echo "[l16-l20-queue] status complete=$complete active=$active failed=$failed at $(date -Is)"
  if [[ "$complete" -eq "${#WAIT_RUNS[@]}" ]]; then
    break
  fi
  if [[ "$failed" -gt 0 && "$active" -eq 0 ]]; then
    echo "[l16-l20-queue] aborting because an upstream L2 run disappeared before completion"
    exit 2
  fi
  sleep 60
done

echo "[l16-l20-queue] L2 TRUEFULL complete; launching replacement sensor runs"

child_pids=()
for sensor_id in L16 L20; do
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  run_id="phase4_${sensor_id}_TRUEFULL_REPLACE_L2_S00_2x1080ti_${stamp}"
  log="$LAUNCH_LOG_DIR/${run_id}.log"
  echo "[l16-l20-queue] launching $sensor_id run_id=$run_id log=$log"
  (
    cd "$ROOT"
    python3 "$RUNNER" \
      --sensor-id "$sensor_id" \
      --seed-id S00 \
      --workers "$WORKERS_PER_SENSOR" \
      --run-id "$run_id"
  ) >"$log" 2>&1 &
  child_pids+=("$!")
  sleep 2
done

status=0
for pid in "${child_pids[@]}"; do
  if ! wait "$pid"; then
    status=1
  fi
done

echo "[l16-l20-queue] finished $(date -Is) status=$status"
exit "$status"
