#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PRISTINE="${PRISTINE:-always}"
ZEPHYR_ENV="${ZEPHYR_ENV:-/home/zekaixiao/ncs/v2.8.0/zephyr/zephyr-env.sh}"

source "${ZEPHYR_ENV}"

PYTHONPATH=/usr/lib/python3/dist-packages west build \
	-b nrf52840dk/nrf52840 \
	"${ROOT}/gr_module" \
	-d "${ROOT}/build/gr_module" \
	-p "${PRISTINE}"
