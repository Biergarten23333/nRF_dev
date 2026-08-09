# Part 2 — OTA onto BSF6C53: the restore hazard is fixed; the OTA did not run

**No board or DK state changed.** BSF6C53 is on `b306-v46-val` (IMU stopped by
the quiesce attempt — see the end). The DK is untouched on v36.

## 1. The restore hazard, and why it was worse than it looked

`SNR 683234364` — the DK the transaction flashes the updater onto — **is the
live Fusion Master**, currently running `dk-fusion-imu-relay-v36`. The tool's
`--restore-build` default was `dk-fusion-imu-relay-v28`, two generations back.

So the sequence would have been: preflight passes (it checks the master marker
on the CDC, which was healthy) → B306 OTA succeeds → **the live master is
flashed back to v28** → the tool verifies the restore against the v28 hashes it
was told to expect, and reports success.

Ninth instance of a checker answering a different question: the verification
was real, it just verified the wrong generation.

### Fixed

- `--restore-build`, `--restore-merged-sha`, `--restore-bin-sha` and a new
  `--restore-marker` are all **required**. No defaults. Verified: the tool now
  exits with `the following arguments are required: --restore-build,
  --restore-merged-sha, --restore-bin-sha, --restore-marker`.
- A **restore gate** runs *before* the updater is flashed (after that the DK no
  longer carries the image being checked). Two independent checks:
  1. the restore image contains the expected marker — catches a stale
     `--restore-build`;
  2. **the DK is currently reporting that same marker, read live from its own
     CDC** — catches a command line that has drifted from the rig.
  Mismatch, or an unreadable marker, refuses the run.
- `v32_ota_batch_preflight.py` had the same defect: `MASTER_MARKER` hardcoded to
  v28. Now defaults to v36 and is overridable by env.

The gate's first version read `merged.hex`, which is Intel HEX **text** — an
ASCII marker can never appear in it, so it would have rejected every correct
image. Caught on first use by the runbook's hand-check rule; it reads the raw
`.bin` now.

## 2. Correct values for this rig

| argument | value |
|---|---|
| `--master-marker` | `dk-fusion-imu-relay-v36` |
| `--restore-build` | `dk-fusion-imu-relay-v36-a` |
| `--restore-marker` | `dk-fusion-imu-relay-v36` |
| `--restore-merged-sha` | `7a7d02cdae13b4450ffea0cb2a46607d481f3760a95e6c38d4c9dd03a2290b56` |
| `--restore-bin-sha` | `59bd57b80d762f5c3d9af9b0d0d303d288584f6f06f5baf5349a3cf3c5628b47` |

`v36-a` and `v36-b` are **byte-identical** — a determinism pair, so either is
canonical. Marker confirmed present in the raw bin.

## 3. Why the OTA still did not run: the two gates block in opposite directions

| path | gate | why it fails now |
|---|---|---|
| legacy | target must be idle | 42 UWB records / 5 s. `IMU STOP` cleared the IMU half (`imu_records: 0, latest_imu_active: 0`) but the UWB records arrive from the **UWB plane over UART**, not from the B306 |
| modern | archived fleet preflight | `remaining-nine gate failed: peers=['BSF6C53'] ready=['BSF6C53']` — it requires the other nine, which are powered down |

**A single-board OTA with the rest of the fleet powered down is not supported by
either gate.** That is not a bug I should route around on hardware: the legacy
gate would need the UWB tag plane quiesced (a subsystem I have not read the
procedure for this session), and the modern gate would need `--skip-preflight`,
which discards the target-marker check as well.

**The resolution is the one already planned.** The next step powers up the other
nine, and the follow-on package specifies all ten OTA'd **as one continuous
batch with no per-board gates** — explicitly barring the one-first-then-nine
pattern. BSF6C53 should simply be in that batch. Doing it alone first is both
unsupported by the tooling and the pattern that cost a round in relay8.3.

## 4. C4 (DFU self-check) — still unanswered

Nothing here exercised BSF6C53's DFU path; the run stopped before any BLE
transfer. The question remains specific to BSF6C53, which has now been
SWD-flashed five times.

## 5. Bootloader region

| | |
|---|---|
| region | `0x0`–`0xC000` |
| sha256 | `31fcc8413f585906ed03485ae8d4474ccc8043aaef8a4b5a00bdee3749f235f4` |
| non-erased | 31 022 B of 49 152 |
| source | readback taken during the v46-val flash, `logs/v46_20260809/C1_flash2/readback.bin` |

**Which variant it came from cannot be determined from the bootloader, and that
is the finding.** The region is byte-identical to the MCUboot image in ~100
builds — every `b306-*` build on disk from `b306-first-dfu` through
`b306-v46r2-val`. MCUboot has never changed across this project, so the
bootloader region carries no variant identity at all. Anything that tried to
identify an image from it would match everything and discriminate nothing.

## 6. Side effect to undo

`IMU STOP` was issued to BSF6C53 during the quiesce attempt and was **not**
restarted, because the board is about to be OTA'd. If the fleet batch is
deferred, issue `IMU START` to return it to nominal.
