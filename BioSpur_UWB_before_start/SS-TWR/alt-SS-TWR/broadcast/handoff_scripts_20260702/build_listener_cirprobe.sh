#!/usr/bin/env bash
# L-B CIR probe build: selfheal2fix source (now with print_full_cir wired into
# capture_to_ring, flag-gated) + APP_LISTENER_CIR_CAPTURE_ENABLE=1. Only L-B gets this;
# the other 4 keep the frozen selfheal-LPD hex (their build compiles the CIR call out).
set -uo pipefail
TC=/home/zekaixiao/ncs/toolchains/b81a7cd864
export PATH="$TC/bin:$TC/usr/bin:$TC/usr/local/bin:$TC/opt/bin:$TC/opt/nanopb/generator-bin:$TC/opt/zephyr-sdk/arm-zephyr-eabi/bin:$TC/opt/zephyr-sdk/riscv64-zephyr-elf/bin:$PATH"
export LD_LIBRARY_PATH="$TC/lib:$TC/lib/x86_64-linux-gnu:$TC/usr/local/lib:${LD_LIBRARY_PATH:-}"
export PYTHONHOME="$TC/usr/local"
export PYTHONPATH="$TC/usr/local/lib/python3.12:$TC/usr/local/lib/python3.12/site-packages"
export ZEPHYR_TOOLCHAIN_VARIANT=zephyr
export ZEPHYR_SDK_INSTALL_DIR="$TC/opt/zephyr-sdk"
export ZEPHYR_BASE=/home/zekaixiao/ncs/v2.8.0/zephyr
export ZEPHYR_NRF_MODULE_DIR=/home/zekaixiao/ncs/v2.8.0/nrf
# L-B identity: co-located with Anchor B (near_anchor=1); id stays 255 (host maps by port)
export APP_LISTENER_ID=255
export APP_LISTENER_NEAR_ANCHOR_ID=1
export APP_LISTENER_CIR_CAPTURE_ENABLE=1
export APP_LISTENER_CIR_SAMPLE_PERIOD=10
export APP_LISTENER_POST_CIR_IDLE_MS=12
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
STAMP=cirprobe_lb_20260702
bash SS-TWR/alt-SS-TWR/broadcast/scripts/build_uwb_listener_poll_diag.sh "$STAMP"
rc=$?
echo "BUILD_RC=$rc"
ls -la "build-uwb-listener-poll-diag-${STAMP}/zephyr/zephyr.hex" 2>/dev/null && echo "HEX_OK"