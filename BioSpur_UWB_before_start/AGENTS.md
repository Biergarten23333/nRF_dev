# Repository Operating Rules

- B120 master-control builds must always use the internal LFRC oscillator on both CPUAPP and CPUNET.
- Do not build or flash B120 master-control images that rely on the nRF5340DK / EVK external 32.768 kHz crystal.
- Required clock config for B120 master-control images:
  - `CONFIG_CLOCK_CONTROL_NRF_K32SRC_RC=y`
  - `CONFIG_CLOCK_CONTROL_NRF_K32SRC_RC_CALIBRATION=y`
  - `CONFIG_CLOCK_CONTROL_NRF_K32SRC_XTAL` not set
  - `CONFIG_CLOCK_CONTROL_NRF_K32SRC_SYNTH` not set
- Use `scripts/build_master_control_b120_m1_internal_osc.sh` or `scripts/build_master_control_b120_m1.sh`; the latter defaults to LFRC if no explicit oscillator config is provided.
- Before any B120 flash, verify the build with `scripts/assert_b120_internal_osc_build.sh <build-dir-or-image>`.
- Never use `nrfjprog`; use the repository J-Link scripts with explicit SNR.
- UWB Tags are OTA-first devices. Do **not** directly J-Link flash a Tag unless
  zekaixiao explicitly authorizes that specific direct flash for recovery,
  restore-to-OTA-capable firmware, or another named exception. Routine Tag image
  deployment must be done through BLE OTA via the Tag Master. Before any Tag OTA,
  verify the candidate image keeps OTA capability enabled:
  - `APP_TAG_BLE_ENABLE=1`
  - `APP_TAG_MCUBOOT_ENABLE=1`
  - `APP_TAG_BLE_OTA_ENABLE=1`
  - `CONFIG_BT=y`
  - `CONFIG_BOOTLOADER_MCUBOOT=y`
  Full raw CIR USB-only Tag builds are not OTA-capable unless these are present;
  do not deploy such builds as normal Tag firmware.
- UWB Anchors are also OTA-only for routine image updates. Do **not** directly
  J-Link flash an Anchor body for normal firmware changes. Anchor image updates
  must be delivered by BLE OTA via the Anchor Master. Flashing the Anchor Master
  itself is allowed when explicitly needed to embed or transmit the new Anchor
  OTA image, but that authorization does not extend to direct flashing of the
  deployed Anchor devices.
- Dual-master naming:
  - SNR `960148546` is `Master_Anchor`.
  - SNR `1050070698` is `Master_Tag`.
  - Keep these CDC display names distinct to avoid selecting the wrong serial port.

## Workstation Resources

This repository is worked on from zekaixiao's Ubuntu workstation. When designing
long-running analysis scripts, prefer parallel/vectorized execution and use GPU
acceleration where practical instead of bottlenecking on one CPU core.

Available hardware:

```text
CPU: Intel Core i7-8700K, overclocked to about 4.9 GHz
GPU: 2x NVIDIA GeForce GTX 1080 Ti
RAM: 32 GB DDR4 3200Mhz
```

For heavy AutoPos, OptiTrack, CIR, Monte Carlo, filtering, or ROTO analyses,
assume both GPUs may be used when idle, but keep CPU/RAM pressure reasonable and
avoid spawning jobs that exhaust system memory.

Long-running analysis jobs must not leave one CPU core doing the work while the
rest of the workstation is idle. This applies to every stage of the pipeline,
including setup/precompute steps such as synthetic IMU generation, cache
generation, pairing scans, filtering, solver rows, plotting, and final ranking.
If a stage has many independent captures, tags, seeds, sensors, filters, solver
rows, or figures, parallelize/vectorize that stage before launching the long run.
Do not rely on a later stage becoming parallel while an earlier stage burns time
single-core.

For IMU/UWB/AutoPos/ROTO sweeps, default to using most CPU cores while keeping
the desktop responsive:

```text
target CPU workers: 8-10 on this 12-thread i7-8700K
minimum for broad sweeps: 2 workers
single-core broad sweep: not allowed unless there is a written technical reason
```

When GPU acceleration is practical, use balanced work allocation across both GTX
1080 Ti cards. If the current algorithm is not GPU-friendly, state that clearly
and still parallelize the CPU path instead of running single-core.

## Privileged Package Management

This workspace runs on zekaixiao's Ubuntu workstation. The machine uses a
restricted sudo package-management wrapper that is authorized by a physical USB
stick.

USB authorization key:

```text
USB UUID: 0085-E315
Typical mount point: /media/zekaixiao/INTENSO
```

Approved privileged package wrapper:

```bash
sudo /usr/local/sbin/codex-pkg
```

Verified behavior:

```text
USB inserted:
  sudo -n /usr/local/sbin/codex-pkg update
  exits with code 0

USB removed:
  sudo -n /usr/local/sbin/codex-pkg update
  exits with code 1

Normal sudo:
  sudo -n whoami
  fails with: sudo: a password is required
```

For any package-management task in this workspace, use only:

```bash
sudo /usr/local/sbin/codex-pkg update
sudo /usr/local/sbin/codex-pkg install <package...>
sudo /usr/local/sbin/codex-pkg deb <absolute-path-to-local-deb>
sudo /usr/local/sbin/codex-pkg fix
```

Never run direct privileged commands such as:

```bash
sudo apt
sudo apt-get
sudo dpkg
sudo snap
sudo rm
sudo chmod
sudo chown
sudo systemctl
sudo mount
sudo umount
sudo dd
sudo mkfs
sudo visudo
```

Do not ask for zekaixiao's sudo password.
Do not store zekaixiao's sudo password.
Do not try to bypass `/usr/local/sbin/codex-pkg`.

If the USB key is missing and the wrapper fails, stop and tell zekaixiao to
insert the USB key.

If a required privileged action is not supported by `/usr/local/sbin/codex-pkg`,
stop and ask zekaixiao instead of trying another sudo command.
