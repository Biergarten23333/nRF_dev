#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PRISTINE="${PRISTINE:-always}"
ZEPHYR_ENV="${ZEPHYR_ENV:-/home/zekaixiao/ncs/v2.8.0/zephyr/zephyr-env.sh}"

source "${ZEPHYR_ENV}"

PYTHONPATH=/usr/lib/python3/dist-packages west build \
	-b nrf5340dk/nrf5340/cpuapp \
	"${ROOT}/central_b120" \
	-d "${ROOT}/build/central_b120" \
	-p "${PRISTINE}"

"${ROOT}/scripts/assert_b120_internal_osc_build.sh" "${ROOT}/build/central_b120"
