#!/usr/bin/env bash
set -euo pipefail

stamp="${1:-$(date +%Y%m%d_%H%M%S)}"
build_dir="build-master-control-b120-m1-master-anchor-lfrc-${stamp}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
conf="$repo_root/configs/b120_master_anchor_lfrc.conf"

MASTER_CMAKE_ARGS="-DEXTRA_CONF_FILE=$conf -Dhci_ipc_EXTRA_CONF_FILE=$conf -DAPP_MASTER_BOOT_PROFILE=anchor ${MASTER_CMAKE_ARGS:-}" \
  "$repo_root/scripts/build_master_control_b120_m1.sh" "$build_dir"
