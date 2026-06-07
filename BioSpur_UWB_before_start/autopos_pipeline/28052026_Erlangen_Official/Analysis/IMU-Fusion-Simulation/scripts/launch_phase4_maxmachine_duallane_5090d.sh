#!/usr/bin/env bash
set +e

export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

SIM_ROOT="/home/tomtgubbe/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/Analysis/IMU-Fusion-Simulation"
cd "$SIM_ROOT" || exit 1

STAMP="${1:-$(date -u +%Y%m%dT%H%M%SZ)}"
MASTER_RUN="phase4_MAXMACHINE_DUALLANE_5090D_${STAMP}"
MASTER_DIR="runs/phase4_algorithm_factory/${MASTER_RUN}"
mkdir -p "$MASTER_DIR/logs" "$MASTER_DIR/tables"

SENSORS=(L0 L1 L2 L3 L4 L5 L7 L8 L10 L11 L12 L13 L14 L15 L16 L17 L18 L19)
SEEDS=(S00 S01 S02 S03 S04)
PHASE2_RUN="20260604T163422Z"

echo "$MASTER_RUN" > "$MASTER_DIR/MASTER_RUN.txt"
printf "lane,run_id,status,start_utc,end_utc,log_path\n" > "$MASTER_DIR/tables/lane_status.csv"
printf "sample_utc,gpu_util_pct,gpu_mem_used_mb,gpu_mem_total_mb,gpu_temp_c,gpu_power_w,gpu_power_limit_w,mem_used_mb,mem_total_mb,swap_used_mb,cpu_tctl_c,phase4_proc_count,phase4_cpu_sum,phase4_rss_gib\n" > "$MASTER_DIR/tables/machine_stress_samples.csv"

monitor_machine() {
  while kill -0 "$1" 2>/dev/null; do
    now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    gpu=$(nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit --format=csv,noheader,nounits | head -1 | tr -d " ")
    mem=$(free -m | awk '/^Mem:/ {print $3 "," $2} /^Swap:/ {swap=$3} END {print "," swap}' | tr -d "\n")
    tctl=$(sensors 2>/dev/null | awk -F'[+°]' '/Tctl:/ {print $2; exit}')
    proc=$(ps -eo pcpu=,rss=,args= | awk '/run_phase4_gpu_pilot.py|run_phase4_nightly_bootstrap.py|run_phase4_l2_singleI_full_factory.py/ {n++; cpu+=$1; rss+=$2} END {printf "%d,%.1f,%.1f", n, cpu, rss/1024/1024}')
    echo "$now,$gpu,$mem,$tctl,$proc" >> "$MASTER_DIR/tables/machine_stress_samples.csv"
    sleep 10
  done
}

echo "[dual-master] start $(date -Is) run=${MASTER_RUN}" | tee -a "$MASTER_DIR/logs/master.log"
echo "[dual-master] GPU lane: raw CUDA all active L, S00-S04, workers/device=24" | tee -a "$MASTER_DIR/logs/master.log"
echo "[dual-master] FULL lane: TRUEFULL all active L, S00-S04, workers=8" | tee -a "$MASTER_DIR/logs/master.log"

(
  for seed in "${SEEDS[@]}"; do
    start=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    run_id="phase4_GPU_RAW_ALLL_${seed}_5090D_DUALLANE_${STAMP}"
    log_path="$MASTER_DIR/logs/${run_id}.log"
    echo "[gpu-lane] launching ${run_id} at ${start}" | tee -a "$MASTER_DIR/logs/master.log"
    python3 scripts/run_phase4_nightly_bootstrap.py \
      --run-id "$run_id" \
      --phase2-run "$PHASE2_RUN" \
      --seed-id "$seed" \
      --devices cuda:0 \
      --l-ids L0 L1 L2 L3 L4 L5 L7 L8 L10 L11 L12 L13 L14 L15 L16 L17 L18 L19 \
      --workers-per-device 24 \
      --chunk-size 8 \
      --partial-max-tracks 0 \
      --partial-max-frames 0 \
      --max-wall-time 0 \
      --chunk-timeout-s 7200 \
      --monitor-interval 5 \
      --cache-root /dev/shm/phase4_gpu_pilot > "$log_path" 2>&1
    rc=$?
    end=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    if [ "$rc" -eq 0 ]; then status=done; else status="failed_${rc}"; fi
    printf "gpu_raw,%s,%s,%s,%s,%s\n" "$run_id" "$status" "$start" "$end" "$log_path" >> "$MASTER_DIR/tables/lane_status.csv"
    echo "[gpu-lane] ${run_id} ${status} at ${end}" | tee -a "$MASTER_DIR/logs/master.log"
  done
) &
GPU_LANE_PID=$!

(
  for sensor in "${SENSORS[@]}"; do
    for seed in "${SEEDS[@]}"; do
      start=$(date -u +%Y-%m-%dT%H:%M:%SZ)
      run_id="phase4_${sensor}_TRUEFULL_${seed}_5090D_DUALLANE_${STAMP}"
      log_path="$MASTER_DIR/logs/${run_id}.log"
      echo "[full-lane] launching ${run_id} at ${start}" | tee -a "$MASTER_DIR/logs/master.log"
      python3 scripts/run_phase4_l2_singleI_full_factory.py \
        --run-id "$run_id" \
        --phase2-run "$PHASE2_RUN" \
        --seed-id "$seed" \
        --sensor-id "$sensor" \
        --workers 8 > "$log_path" 2>&1
      rc=$?
      end=$(date -u +%Y-%m-%dT%H:%M:%SZ)
      if [ "$rc" -eq 0 ]; then status=done; else status="failed_${rc}"; fi
      printf "truefull,%s,%s,%s,%s,%s\n" "$run_id" "$status" "$start" "$end" "$log_path" >> "$MASTER_DIR/tables/lane_status.csv"
      echo "[full-lane] ${run_id} ${status} at ${end}" | tee -a "$MASTER_DIR/logs/master.log"
    done
  done
) &
FULL_LANE_PID=$!

echo "[dual-master] gpu_lane_pid=${GPU_LANE_PID} full_lane_pid=${FULL_LANE_PID}" | tee -a "$MASTER_DIR/logs/master.log"
monitor_machine "$$" > "$MASTER_DIR/logs/machine_monitor.log" 2>&1 &
MONITOR_PID=$!
echo "$MONITOR_PID" > "$MASTER_DIR/MONITOR_PID.txt"

wait "$GPU_LANE_PID"
GPU_RC=$?
wait "$FULL_LANE_PID"
FULL_RC=$?
kill "$MONITOR_PID" 2>/dev/null || true
echo "[dual-master] complete $(date -Is) gpu_rc=${GPU_RC} full_rc=${FULL_RC}" | tee -a "$MASTER_DIR/logs/master.log"
