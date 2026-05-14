#!/usr/bin/env bash
set -euo pipefail

build_dir="${1:-build-master-control-b120-m1-internal-osc}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
internal_conf="$repo_root/configs/b120_internal_osc.conf"

if [ ! -f "$internal_conf" ]; then
  echo "missing internal oscillator config: $internal_conf" >&2
  exit 2
fi

export MASTER_CMAKE_ARGS="-DEXTRA_CONF_FILE=$internal_conf -Dhci_ipc_EXTRA_CONF_FILE=$internal_conf ${MASTER_CMAKE_ARGS:-}"
"$repo_root/scripts/build_master_control_b120_m1.sh" "$build_dir"
