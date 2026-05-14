#!/usr/bin/env bash
set -euo pipefail

stamp="${1:-$(date +%Y%m%d_%H%M%S)}"
build_dir="build-master-control-b120-m1-master-anchor-lfrc-${stamp}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
conf="$repo_root/configs/b120_master_anchor_lfrc.conf"
expected_marker="${APP_EXPECTED_ANCHOR_FW_MARKER:-${APP_ANCHOR_FW_MARKER:-}}"
protect_file="$repo_root/.protec/noflash960148546"

if [ -f "$protect_file" ]; then
  # shellcheck disable=SC1090
  source "$protect_file"
fi

echo "[payload-guard] master_role=Master_Anchor expected_payload=anchor active_payload=$repo_root/apps/master_ota/generated/active_ota_payload.json"

payload_args=(--repo-root "$repo_root" --expected anchor)
if [[ -n "$expected_marker" ]]; then
  payload_args+=(--marker "$expected_marker")
fi
if [ "${BIOSPUR_FLASH_POLICY_BLOCK_PAYLOAD_MISMATCH:-1}" = "1" ]; then
  python3 "$repo_root/scripts/assert_active_ota_payload.py" "${payload_args[@]}"
else
  echo "[payload-guard] WARNING: payload mismatch guard disabled by policy"
fi

MASTER_CMAKE_ARGS="-DEXTRA_CONF_FILE=$conf -Dhci_ipc_EXTRA_CONF_FILE=$conf -DAPP_MASTER_BOOT_PROFILE=anchor ${MASTER_CMAKE_ARGS:-}" \
  "$repo_root/scripts/build_master_control_b120_m1.sh" "$build_dir"
