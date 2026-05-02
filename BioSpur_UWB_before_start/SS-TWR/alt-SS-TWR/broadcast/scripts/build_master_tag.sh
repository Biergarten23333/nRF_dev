#!/usr/bin/env bash
set -euo pipefail

stamp="${1:-$(date +%Y%m%d_%H%M%S)}"
build_dir="build-master-control-b120-m1-master-tag-lfrc-${stamp}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
conf="$repo_root/configs/b120_master_tag_lfrc.conf"
expected_marker="${APP_EXPECTED_TAG_FW_MARKER:-${APP_TAG_FW_MARKER:-}}"

payload_args=(--repo-root "$repo_root" --expected tag)
if [[ -n "$expected_marker" ]]; then
  payload_args+=(--marker "$expected_marker")
fi
python3 "$repo_root/scripts/assert_active_ota_payload.py" "${payload_args[@]}"

MASTER_CMAKE_ARGS="-DEXTRA_CONF_FILE=$conf -Dhci_ipc_EXTRA_CONF_FILE=$conf -DAPP_MASTER_BOOT_PROFILE=tag -DAPP_MASTER_BOOT_TAG_ALLOWLIST=BSF66F,BS2DCE,BSDC91 ${MASTER_CMAKE_ARGS:-}" \
  "$repo_root/scripts/build_master_control_b120_m1.sh" "$build_dir"
