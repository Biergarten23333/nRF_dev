#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start"
SIM_ROOT="$ROOT/autopos_pipeline/28052026_Erlangen_Official/Analysis/IMU-Fusion-Simulation"
RUN_BASE="$SIM_ROOT/runs/phase4_algorithm_factory"
RUNNER="$SIM_ROOT/scripts/run_phase4_l2_singleI_full_factory.py"
LAUNCH_LOG_DIR="$RUN_BASE/launch_logs"
WORKERS_PER_RUN="${WORKERS_PER_RUN:-3}"
STAMP="${STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"

mkdir -p "$LAUNCH_LOG_DIR"

run_one() {
  local sensor_id="$1"
  local seed_id="$2"
  local run_id="phase4_${sensor_id}_TRUEFULL_REPLACE_L2_${seed_id}_2x1080ti_${STAMP}"
  local log="$LAUNCH_LOG_DIR/${run_id}.log"

  echo "[l16-l20-s01-s04] launching sensor=$sensor_id seed=$seed_id run_id=$run_id workers=$WORKERS_PER_RUN"
  (
    cd "$ROOT"
    python3 "$RUNNER" \
      --sensor-id "$sensor_id" \
      --seed-id "$seed_id" \
      --workers "$WORKERS_PER_RUN" \
      --run-id "$run_id"
  ) >"$log" 2>&1 &
}

wait_batch() {
  local status=0
  for pid in "$@"; do
    if ! wait "$pid"; then
      status=1
    fi
  done
  return "$status"
}

echo "[l16-l20-s01-s04] started $(date -Is)"
echo "[l16-l20-s01-s04] stamp=$STAMP workers_per_run=$WORKERS_PER_RUN"

batch_pids=()
for seed_id in S01 S02; do
  for sensor_id in L16 L20; do
    run_one "$sensor_id" "$seed_id"
    batch_pids+=("$!")
    sleep 2
  done
done

echo "[l16-l20-s01-s04] batch 1/2 launched; waiting"
wait_batch "${batch_pids[@]}"
echo "[l16-l20-s01-s04] batch 1/2 complete $(date -Is)"

batch_pids=()
for seed_id in S03 S04; do
  for sensor_id in L16 L20; do
    run_one "$sensor_id" "$seed_id"
    batch_pids+=("$!")
    sleep 2
  done
done

echo "[l16-l20-s01-s04] batch 2/2 launched; waiting"
wait_batch "${batch_pids[@]}"
echo "[l16-l20-s01-s04] finished $(date -Is)"
