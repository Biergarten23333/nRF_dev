#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FULL_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
OFFICIAL_ROOT="$(cd "${FULL_DIR}/../../.." && pwd)"
LOG_DIR="${FULL_DIR}/logs/full_all8"

mkdir -p "${LOG_DIR}" "${FULL_DIR}/tables" "${FULL_DIR}/figs"

export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-1}"

run_step() {
  local name="$1"
  shift
  local log="${LOG_DIR}/${name}.log"
  echo "[full-all8] ${name}"
  (
    cd "${FULL_DIR}"
    "$@"
  ) >"${log}" 2>&1
}

run_step 01_layout \
  python3 scripts/layout_optitrack_compare.py \
    --official-root "${OFFICIAL_ROOT}" \
    --out-dir "${FULL_DIR}" \
    --eval-sets all8

run_step 02_static_tag_absolute \
  python3 scripts/static_tag_absolute_accuracy.py \
    --official-root "${OFFICIAL_ROOT}" \
    --out-dir "${FULL_DIR}" \
    --eval-sets all8

run_step 03_static_tag_raw_replay \
  python3 scripts/static_tag_raw_replay_matrix.py \
    --official-root "${OFFICIAL_ROOT}" \
    --out-dir "${FULL_DIR}" \
    --eval-sets all8

run_step 04_tag_localization_metrics \
  python3 scripts/tag_localization_metrics.py \
    --official-root "${OFFICIAL_ROOT}" \
    --analysis-root "${FULL_DIR}"

run_step 05_surveyed_anchor_baseline \
  python3 scripts/surveyed_anchor_baseline.py \
    --official-root "${OFFICIAL_ROOT}" \
    --out-dir "${FULL_DIR}" \
    --eval-sets all8

run_step 06_pair_residual_heatmap \
  python3 scripts/pair_residual_heatmap.py \
    --official-root "${OFFICIAL_ROOT}" \
    --out-dir "${FULL_DIR}"

run_step 07_temporal_drift \
  python3 scripts/temporal_drift_analysis.py \
    --official-root "${OFFICIAL_ROOT}" \
    --out-dir "${FULL_DIR}"

for grid in 25 40 50 100; do
  run_step "08_dop_grid_${grid}" \
    python3 scripts/vdop_map.py \
      --official-root "${OFFICIAL_ROOT}" \
      --out-dir "${FULL_DIR}" \
      --grid-mm "${grid}" \
      --masks all8 \
      --device auto
done

run_step 09_stratified_keepk_parallel \
  bash scripts/run_stratified_keepk_parallel.sh

run_step 10_additional_diagnostics \
  python3 scripts/additional_diagnostics.py \
    --official-root "${OFFICIAL_ROOT}" \
    --out-dir "${FULL_DIR}"

run_step 11_mc_integrity \
  python3 scripts/mc_integrity_aggregate.py \
    --official-root "${OFFICIAL_ROOT}" \
    --out-dir "${FULL_DIR}"

run_step 12_bootstrap_ci \
  python3 scripts/bootstrap_ci.py \
    --official-root "${OFFICIAL_ROOT}" \
    --out-dir "${FULL_DIR}"

run_step 13_filtered_deployment_parallel \
  bash filtered_deployment/scripts/run_filtered_static_parallel.sh

echo "[full-all8] complete"
echo "[full-all8] logs: ${LOG_DIR}"
