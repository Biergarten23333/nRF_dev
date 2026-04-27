#!/usr/bin/env bash
set -euo pipefail

build_dir="${1:-build-master-control-b120-m1-internal-synth}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
internal_conf="$repo_root/configs/b120_internal_synth.conf"
internal_hci_conf="$repo_root/configs/b120_internal_synth_hci_ipc.conf"

if [ ! -f "$internal_conf" ]; then
  echo "missing internal synth config: $internal_conf" >&2
  exit 2
fi
if [ ! -f "$internal_hci_conf" ]; then
  echo "missing internal synth hci_ipc config: $internal_hci_conf" >&2
  exit 2
fi

export MASTER_CMAKE_ARGS="-DEXTRA_CONF_FILE=$internal_conf -Dhci_ipc_EXTRA_CONF_FILE=$internal_hci_conf ${MASTER_CMAKE_ARGS:-}"
"$repo_root/scripts/build_master_control_b120_m1.sh" "$build_dir"
