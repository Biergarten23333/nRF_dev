#!/usr/bin/env bash
set -euo pipefail

stamp="${1:-$(date +%Y%m%d_%H%M%S)}"
build_dir="build-master-control-b120-m1-master-anchor-lfrc-${stamp}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
conf="$repo_root/configs/b120_master_anchor_lfrc.conf"
expected_marker="${APP_EXPECTED_ANCHOR_FW_MARKER:-${APP_ANCHOR_FW_MARKER:-}}"

payload_args=(--repo-root "$repo_root" --expected anchor)
if [[ -n "$expected_marker" ]]; then
  payload_args+=(--marker "$expected_marker")
fi
python3 "$repo_root/scripts/assert_active_ota_payload.py" "${payload_args[@]}"

MASTER_CMAKE_ARGS="-DEXTRA_CONF_FILE=$conf -Dhci_ipc_EXTRA_CONF_FILE=$conf -DAPP_MASTER_BOOT_PROFILE=anchor ${MASTER_CMAKE_ARGS:-}" \
  "$repo_root/scripts/build_master_control_b120_m1.sh" "$build_dir"
