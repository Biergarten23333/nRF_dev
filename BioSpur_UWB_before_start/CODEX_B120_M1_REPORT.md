# B120 M1 Report

Date: 2026-04-12
Board: EVK-NORA-B120
Probe SNR: `960148546`

## Goal
Finish M1:
- stable logs on B120
- non-interactive flashing
- CDC serial usable

## Result
M1 completed.

The current board is running `master_control` on target `nrf5340dk/nrf5340/cpuapp` and exposes a working CDC serial port.

Detected CDC port after flash:
- `/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_87EA2F4A526C5A02-if00`

Observed boot banner:
- `BioSpur BLE master control ready on nrf5340dk/nrf5340/cpuapp`

Verified serial command path:
- sent `status`
- received `[RECV] Control status: mode=RECV pending=0`

## Build Command
```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
bash scripts/build_master_control_b120_m1.sh
```

Build output:
- `build-master-control-b120-m1/zephyr/merged_domains.hex`

## Flash Command
```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
bash scripts/flash_master_control_b120_m1_noninteractive.sh
```

## No-popup Proof
The flash path does **not** use `west flash`, `nrfjprog`, or probe auto-selection.

It uses explicit SNR with headless SEGGER CLI only:
```bash
JLinkExe \
  -NoGui 1 \
  -SelectEmuBySN 960148546 \
  -device NRF5340_XXAA_APP \
  -if SWD \
  -speed 4000 \
  -autoconnect 1 \
  -CommanderScript <tmpfile>
```

This is the current no-popup flashing path left in the repo.

## Files Added/Changed For M1
- `apps/master_control/boards/nrf5340dk_nrf5340_cpuapp.overlay`
- `scripts/build_master_control_b120_m1.sh`
- `scripts/flash_master_control_b120_m1_noninteractive.sh`
- `apps/master_control/src/main.c`
- `apps/master/src/master_app.c`
- `apps/master/src/master_multi_app.c`

## Notes
- The EVK is being brought up first on `nrf5340dk/nrf5340/cpuapp` because the installed NCS board list in this environment does not expose `ubx_evknorab12_nrf5340` directly.
- This is sufficient for M1 bring-up and CDC verification.
- PA/LNA/FEM integration has **not** been implemented yet. That belongs to M2 and later.
