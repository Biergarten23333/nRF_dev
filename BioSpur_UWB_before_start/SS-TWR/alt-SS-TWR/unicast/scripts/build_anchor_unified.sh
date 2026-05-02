#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "Usage: $0 <build-dir> [schedule-mode]" >&2
  exit 1
fi

build_dir="$1"
schedule_mode="${2:-2}"

echo "[build-anchor-unified] build_dir=${build_dir} schedule_mode=${schedule_mode}"

west build -b decawave_dwm1001_dev -s apps/anchor -d "${build_dir}" --no-sysbuild --pristine=always -- \
  -DAPP_ANCHOR_ROLE=0 \
  -DAPP_ANCHOR_MASTER=0 \
  -DAPP_ANCHOR_ALLOW_TAG_POLLS=1 \
  -DAPP_ANCHOR_USE_AUTO_SCHEDULE=1 \
  -DAPP_ANCHOR_SCHEDULE_MODE="${schedule_mode}" \
  -DAPP_ANCHOR_ID=0 \
  ${ANCHOR_CMAKE_ARGS:-}

python3 scripts/write_build_source.py \
  --build-dir "${build_dir}" \
  --source "scripts/build_anchor_unified.sh" \
  --command "$0 $* (single unified anchor artifact)"

echo "[build-anchor-unified] done: ${build_dir}"
