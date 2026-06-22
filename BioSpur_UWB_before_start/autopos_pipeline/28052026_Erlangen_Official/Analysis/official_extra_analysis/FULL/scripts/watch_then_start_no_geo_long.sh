#!/usr/bin/env bash
set -euo pipefail

OLD_PID="${1:-3291475}"

ROOT="/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start"
FULL="$ROOT/autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis/FULL"
PY="/tmp/biospur_static_gpr_venv/bin/python"
SCRIPT="$FULL/scripts/optuna_roto_spatiotemporal_dnn.py"
LOG_DIR="$FULL/logs"
STORAGE="sqlite:///$FULL/tables/roto_spatiotemporal_dnn_optuna_R14_no_geo_long.db"
STUDY_NAME="roto_spatiotemporal_dnn_loco_R14_no_geo_long"

mkdir -p "$LOG_DIR" "$FULL/tables" "$FULL/models" "$FULL/figs"

echo "watchdog_started_at=$(date --iso-8601=seconds)"
echo "watching_old_pid=$OLD_PID"
echo "script=$SCRIPT"
echo "storage=$STORAGE"
echo "study_name=$STUDY_NAME"

if kill -0 "$OLD_PID" 2>/dev/null; then
  echo "old_pid_status=running; waiting with tail --pid=$OLD_PID -f /dev/null"
  tail --pid="$OLD_PID" -f /dev/null
else
  echo "old_pid_status=not_running; launching no_geo immediately"
fi

echo "old_pid_finished_at=$(date --iso-8601=seconds)"

NEW_LOG="$LOG_DIR/roto_spatiotemporal_dnn_optuna_R14_no_geo_long_$(date +%Y%m%d_%H%M%S).log"
NEW_PIDFILE="${NEW_LOG%.log}.pid"

setsid bash -c '
  set -euo pipefail
  PIDFILE="$1"
  PY="$2"
  SCRIPT="$3"
  STORAGE="$4"
  STUDY_NAME="$5"
  echo $$ > "$PIDFILE"
  exec "$PY" -u "$SCRIPT" \
    --outer-test R14 \
    --feature-mode no_geo \
    --n-trials 100 \
    --epochs 30 \
    --patience 5 \
    --final-epochs 50 \
    --solve-workers 8 \
    --solve-chunk-size 128 \
    --storage "$STORAGE" \
    --study-name "$STUDY_NAME"
' bash "$NEW_PIDFILE" "$PY" "$SCRIPT" "$STORAGE" "$STUDY_NAME" > "$NEW_LOG" 2>&1 < /dev/null &

NEW_PID=$!
echo "new_task_started_at=$(date --iso-8601=seconds)"
echo "new_pid=$NEW_PID"
echo "new_pidfile=$NEW_PIDFILE"
echo "new_log=$NEW_LOG"
