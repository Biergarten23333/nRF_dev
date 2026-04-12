#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <image.hex>" >&2
  exit 1
fi

SN="683234364"
IMAGE="$1"
script_dir="$(cd "$(dirname "$0")" && pwd)"
flash_mode="${BIOSPUR_FLASH_MODE:-usb_msd}"

echo "tool=flash_master_noninteractive snr=${SN} image=${IMAGE} mode=${flash_mode} policy=no_popup_default"

find_msd_mount() {
	local mnt
	local source

	# Prefer automounted user paths.
	for mnt in /media/"$USER"/* /run/media/"$USER"/*; do
		if [ -d "$mnt" ]; then
			# Common indicators for UF2/J-Link MSD style volumes.
			if [ -f "$mnt/INFO_UF2.TXT" ] || [ -f "$mnt/DETAILS.TXT" ] || \
			   [ -f "$mnt/MBED.HTM" ] || [ -f "$mnt/README.TXT" ] || \
			   [ -f "$mnt/Segger.html" ]; then
				if [ ! -w "$mnt" ] && command -v udisksctl >/dev/null 2>&1; then
					source="$(findmnt -no SOURCE --target "$mnt" 2>/dev/null || true)"
					if [ -n "$source" ]; then
						echo "tool=flash_master_noninteractive action=remount_rw source=${source} mount=${mnt}" >&2
						udisksctl unmount -b "$source" >/dev/null 2>&1 || true
						sleep 1
						udisksctl mount -b "$source" >/dev/null 2>&1 || true
						sleep 1
					fi
				fi
				if [ ! -w "$mnt" ]; then
					continue
				fi
				printf '%s\n' "$mnt"
				return 0
			fi
		fi
  done

  # Fallback: label-based detection.
  mnt="$(lsblk -nrpo LABEL,MOUNTPOINT 2>/dev/null | awk '$1=="JLINK" && $2!="" {print $2; exit}')"
  if [ -n "$mnt" ] && [ -d "$mnt" ] && [ -w "$mnt" ]; then
    printf '%s\n' "$mnt"
    return 0
  fi

  return 1
}

if [ ! -f "$IMAGE" ]; then
  echo "Image not found: $IMAGE" >&2
  exit 2
fi

if [ "$flash_mode" = "usb_msd" ]; then
  mount_point="$(find_msd_mount || true)"
  if [ -z "${mount_point:-}" ]; then
    echo "[error] USB-MSD mount not found; refusing SWD flash to avoid popup." >&2
    echo "[hint] Mount the board MSD volume, or explicitly opt in with BIOSPUR_FLASH_MODE=nrfjprog." >&2
    exit 3
  fi

  dst="${mount_point}/$(basename "$IMAGE")"
  echo "tool=flash_master_noninteractive action=copy mount=${mount_point} dst=${dst}"
  cp -f "$IMAGE" "$dst"
  sync
  sleep 1
  echo "tool=flash_master_noninteractive action=ok mode=usb_msd mount=${mount_point}"
  exit 0
fi

if [ "$flash_mode" != "nrfjprog" ]; then
  echo "[error] Unsupported BIOSPUR_FLASH_MODE=${flash_mode} (expected usb_msd or nrfjprog)" >&2
  exit 4
fi

# Legacy SWD mode (explicit opt-in only).
# Prevent VSCode Nordic background hotplug scanner from racing J-Link access.
pkill -f "nrfutil-device --json list --hotplug" >/dev/null 2>&1 || true
sleep 0.2

IDS="$(nrfjprog --ids || true)"
if ! printf '%s\n' "$IDS" | rg -q "^${SN}$"; then
  echo "Required probe SN ${SN} not present." >&2
  echo "Detected probes:" >&2
  printf '%s\n' "$IDS" >&2
  exit 5
fi

echo "tool=reset_then_flash snr=${SN} image=${IMAGE} action=begin"
BIOSPUR_FLASH_FORCE_JLINK=0 BIOSPUR_FLASH_ALLOW_JLINK_FALLBACK=0 \
  "${script_dir}/reset_then_flash.sh" "${SN}" "${IMAGE}"
echo "tool=reset_then_flash snr=${SN} image=${IMAGE} action=ok"
