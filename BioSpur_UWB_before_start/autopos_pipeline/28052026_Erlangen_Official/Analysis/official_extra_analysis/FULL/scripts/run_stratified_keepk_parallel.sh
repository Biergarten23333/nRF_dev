#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FULL_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
OFFICIAL_ROOT="$(cd "${FULL_DIR}/../../.." && pwd)"
LOG_DIR="${FULL_DIR}/logs/stratified_keepk"
TABLES_DIR="${FULL_DIR}/tables"
FIGS_DIR="${FULL_DIR}/figs"

NUM_SHARDS="${NUM_SHARDS:-8}"
DEVICES_CSV="${DEVICES_CSV:-cuda:0,cuda:1}"

mkdir -p "${LOG_DIR}" "${TABLES_DIR}" "${FIGS_DIR}"

rm -f "${TABLES_DIR}"/stratified_keepk*.csv
rm -f "${TABLES_DIR}"/stratified_keepk*.md
rm -f "${FIGS_DIR}"/stratified_keepk*.png
rm -f "${LOG_DIR}"/shard_*.log "${LOG_DIR}"/combine.log

IFS=',' read -r -a DEVICES <<< "${DEVICES_CSV}"
if [[ "${#DEVICES[@]}" -eq 0 ]]; then
  echo "No CUDA devices configured" >&2
  exit 1
fi

pids=()
for ((shard=0; shard<NUM_SHARDS; shard++)); do
  device="${DEVICES[$((shard % ${#DEVICES[@]}))]}"
  log="${LOG_DIR}/shard_${shard}_of_${NUM_SHARDS}.log"
  echo "[strat-parallel] launch shard ${shard}/${NUM_SHARDS} on ${device}"
  (
    export OMP_NUM_THREADS=1
    export OPENBLAS_NUM_THREADS=1
    export MKL_NUM_THREADS=1
    export NUMEXPR_NUM_THREADS=1
    python3 "${SCRIPT_DIR}/stratified_keepk_replay.py" \
      --official-root "${OFFICIAL_ROOT}" \
      --out-dir "${FULL_DIR}" \
      --num-shards "${NUM_SHARDS}" \
      --shard-id "${shard}" \
      --device "${device}"
  ) >"${log}" 2>&1 &
  pids+=("$!")
done

fail=0
for pid in "${pids[@]}"; do
  if ! wait "${pid}"; then
    fail=1
  fi
done

if [[ "${fail}" -ne 0 ]]; then
  echo "[strat-parallel] one or more shards failed; see ${LOG_DIR}" >&2
  exit 1
fi

python3 "${SCRIPT_DIR}/stratified_keepk_replay.py" \
  --official-root "${OFFICIAL_ROOT}" \
  --out-dir "${FULL_DIR}" \
  --num-shards "${NUM_SHARDS}" \
  --combine-shards-only >"${LOG_DIR}/combine.log" 2>&1

echo "[strat-parallel] complete"
echo "[strat-parallel] logs: ${LOG_DIR}"
echo "[strat-parallel] output: ${TABLES_DIR}/stratified_keepk_by_drop_set.csv"
