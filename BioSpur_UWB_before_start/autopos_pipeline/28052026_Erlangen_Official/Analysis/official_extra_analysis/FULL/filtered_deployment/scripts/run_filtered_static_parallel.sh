#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FULL_FILTER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
FULL_DIR="$(cd "${FULL_FILTER_DIR}/.." && pwd)"
OFFICIAL_ROOT="$(cd "${FULL_DIR}/../../.." && pwd)"

NUM_SHARDS="${NUM_SHARDS:-5}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-1}"

mkdir -p "${FULL_FILTER_DIR}/tables" "${FULL_FILTER_DIR}/figs" "${FULL_FILTER_DIR}/reports" "${FULL_FILTER_DIR}/logs"
rm -f "${FULL_FILTER_DIR}"/tables/filtered_static*.csv
rm -f "${FULL_FILTER_DIR}"/figs/filtered_static*.png
rm -f "${FULL_FILTER_DIR}"/reports/filtered_static_results.md
rm -f "${FULL_FILTER_DIR}"/run_meta*.json
rm -f "${FULL_FILTER_DIR}"/logs/filtered_static_shard_*.log

pids=()
for shard in $(seq 0 $((NUM_SHARDS - 1))); do
  gpu=$((shard % 2))
  log="${FULL_FILTER_DIR}/logs/filtered_static_shard_${shard}_of_${NUM_SHARDS}.log"
  (
    cd "${FULL_DIR}"
    CUDA_VISIBLE_DEVICES="${gpu}" "${PYTHON_BIN}" filtered_deployment/scripts/run_filtered_static_matrix.py \
      --official-root "${OFFICIAL_ROOT}" \
      --out-dir "${FULL_FILTER_DIR}" \
      --eval-sets all8 \
      --num-shards "${NUM_SHARDS}" \
      --shard-id "${shard}"
  ) >"${log}" 2>&1 &
  pids+=("$!")
done

status=0
for pid in "${pids[@]}"; do
  if ! wait "${pid}"; then
    status=1
  fi
done

if [[ "${status}" -ne 0 ]]; then
  echo "[filtered-static-parallel] one or more shards failed; see ${FULL_FILTER_DIR}/logs" >&2
  exit "${status}"
fi

(
  cd "${FULL_DIR}"
  "${PYTHON_BIN}" filtered_deployment/scripts/run_filtered_static_matrix.py \
    --official-root "${OFFICIAL_ROOT}" \
    --out-dir "${FULL_FILTER_DIR}" \
    --eval-sets all8 \
    --num-shards "${NUM_SHARDS}" \
    --combine-shards-only
)

echo "[filtered-static-parallel] completed NUM_SHARDS=${NUM_SHARDS}"
