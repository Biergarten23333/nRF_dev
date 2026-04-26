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
