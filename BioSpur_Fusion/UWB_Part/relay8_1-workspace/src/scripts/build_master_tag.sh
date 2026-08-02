#!/usr/bin/env bash
set -euo pipefail

stamp="${1:-$(date +%Y%m%d_%H%M%S)}"
build_dir="master-control-b120-m1-master-tag-lfrc-${stamp}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
conf="$repo_root/configs/b120_master_tag_lfrc.conf"
hci_resilience_conf="$repo_root/configs/b120_master_tag_hci_resilience.conf"
hci_resilience_module="$repo_root/modules/hci_ipc_resilience"
expected_marker="${APP_EXPECTED_TAG_FW_MARKER:-${APP_TAG_FW_MARKER:-}}"
protect_file="$repo_root/.protec/noflash960148546"
tag_allowlist="${APP_MASTER_BOOT_TAG_ALLOWLIST:-BS065F}"
tdma_slot_period_ms="${APP_MASTER_TAG_TDMA_SLOT_PERIOD_MS:-${APP_MASTER_TDMA_SLOT_PERIOD_MS:-10}}"
tdma_slot_active_ms="${APP_MASTER_TAG_TDMA_SLOT_ACTIVE_MS:-${APP_MASTER_TDMA_SLOT_ACTIVE_MS:-9}}"
tdma_slot_active_us="${APP_MASTER_TAG_TDMA_SLOT_ACTIVE_US:-${APP_MASTER_TDMA_SLOT_ACTIVE_US:-0}}"
tdma_epoch_lead_ms="${APP_MASTER_TAG_TDMA_EPOCH_LEAD_MS:-${APP_MASTER_TDMA_EPOCH_LEAD_MS:-5000}}"
carrier_marker="${APP_MASTER_CARRIER_MARKER:-master-tag-carrier-v2-fix5}"

if [ -f "$protect_file" ]; then
  # shellcheck disable=SC1090
  source "$protect_file"
fi

echo "[payload-guard] master_role=Master_Tag expected_payload=tag active_payload=$repo_root/apps/master_ota/generated/active_ota_payload.json"

payload_args=(--repo-root "$repo_root" --expected tag)
if [[ -n "$expected_marker" ]]; then
  payload_args+=(--marker "$expected_marker")
fi
if [ "${BIOSPUR_FLASH_POLICY_BLOCK_PAYLOAD_MISMATCH:-1}" = "1" ]; then
  python3 "$repo_root/scripts/assert_active_ota_payload.py" "${payload_args[@]}"
else
  echo "[payload-guard] WARNING: payload mismatch guard disabled by policy"
fi

MASTER_CMAKE_ARGS="-DEXTRA_CONF_FILE=$conf -Dhci_ipc_EXTRA_CONF_FILE=$conf;$hci_resilience_conf -DZEPHYR_EXTRA_MODULES=$hci_resilience_module -DAPP_MASTER_BOOT_PROFILE=tag -DAPP_MASTER_CARRIER_MARKER=${carrier_marker} -DAPP_MASTER_BOOT_TAG_ALLOWLIST=${tag_allowlist} -DAPP_MASTER_TDMA_SLOT_PERIOD_MS=${tdma_slot_period_ms} -DAPP_MASTER_TDMA_SLOT_ACTIVE_MS=${tdma_slot_active_ms} -DAPP_MASTER_TDMA_SLOT_ACTIVE_US=${tdma_slot_active_us} -DAPP_MASTER_TDMA_EPOCH_LEAD_MS=${tdma_epoch_lead_ms} ${MASTER_CMAKE_ARGS:-}" \
  "$repo_root/scripts/build_master_control_b120_m1.sh" "$build_dir"
