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
- Dual-master naming:
  - SNR `960148546` is `Master_Anchor`.
  - SNR `1050070698` is `Master_Tag`.
  - Keep these CDC display names distinct to avoid selecting the wrong serial port.

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
