# B306 v47 build and hardware qualification report

Final verdict: **BLOCKED**

The canonical v47 artifact was built, finalized, registered, rebuilt, and
offline-qualified. The sole authorized hardware transaction did not install
v47 on BSF6C53. The independent verifier saw only the old confirmed v46 image
until the original absolute deadline expired. Read-only slot inventory then
proved that neither slot contains v47 and that no pending or target-unconfirmed
state remains. No retry was attempted.

## Canonical identity

- Version: `v47`
- Marker: `b306-imu-relay-v47`
- MCUboot version: `0.1.47`
- FWID: `f7436728c36efdd28f848e7ef59c7c422437afb8c6ee07dd8924e31967046eed`
- Signed payload SHA-256: `161630a68e783ea3fb44f8eab1e70410f4eb9a46903889159dc84e99b2264f35`
- MCUboot image SHA-256: `90ef063b227feb4c70499cc186df866c24da658fba98773eacc40da73a0abf98`
- Finalized manifest SHA-256: `36c23bb56900fd5588e953249168d1ef438a2d7407d540d00cfe5a4c77196a44`
- Canonical merged hex SHA-256: `9bf27f37050ab42f26e00ea3e1c0a661b66a476608fc34a267235152311516e8`
- Registry SHA-256 at freeze: `f67cc2bde5ef62eb4b40693c116b5b02af8ff206ddabd40f166a0be8a51667dc`

The collision-closed registry indexes this one identity by FWID, `v47`, and
MCUboot `0.1.47`; all three entries bind to the same image SHA. The finalized
manifest is `B306_Part/releases/v47/finalized_manifest.json` and the registry is
`B306_Part/releases/identity_registry.json`.

## Build provenance and offline qualification

The firmware source commit is `3d1b0b1c265ae72f28fbf15cf4a8dda9806bb2d0`
with firmware tree `0d32c5df1255157ab7e661bb2abad398547e9c65` and
an empty relevant-path dirty digest. NCS is v2.8.0, Zephyr is
`0bc3393fb112ec80ebeab48cd023d69b1e9db757`, nrf is
`a2386bfc84016fa571f997ac871b25bd67ca481a`, the SDK patch is
`ff35373425c7eee54d1f10226bd65ed8042dfb6073b8ebd8cacd53cf5f39921b`,
and the compiler is Zephyr SDK 0.16.8 arm-zephyr-eabi-gcc 12.2.0 with binary
SHA-256 `a1932d42d053670f24b031ee57d8e7233f84c3fe4e9378418992f1891d192d72`.
The signing public-key DER identity is
`0e525dedaa7f50fb38d3c8f1792cacaa20f70204aa46ef6b50d720479c6ef5a2`.
The complete configuration hashes and requested capabilities are recorded in
`B306_Part/releases/v47/build_inputs.json`.

All 126 OTA tool tests passed. Firmware source/policy tests, boot-confirm and
identity negative tests, updater/host-binary tests, Fusion Master C/Python
contracts, Python compilation, C/CMake builds, and the SDK patch
apply/verify/revert/apply round trip passed. A build without `BSF_FWID` failed
as required. The rebuild produced identical unsigned application content,
effective configuration, embedded FWID, and MCUboot image SHA. ECDSA made the
second signed payload bytewise different, so it was retained only as rebuild
evidence and did not replace the canonical signed artifact.

Memory gate: FLASH 235,836 / 499,200 bytes (47.24%, PASS); RAM 140,164 /
262,144 bytes (53.47%, PASS); malloc arena 0.

## BSF6C53 hardware evidence

Before the transaction, read-only inventory recorded:

| Slot | MCUboot version | Hash | Active | Confirmed | Pending | Bootable |
|---|---|---|---:|---:|---:|---:|
| 0 | 0.1.45 | `12d383e1ad51454d327d34d1e4cf3f9b830ef43f3957893981cb2c425a2a67b5` | true | true | false | true |
| 1 | 0.1.45 | `97c2d8d99400e17654a12185a3a71bc35b56f8f52dfd0756e09ed9705a87ad34` | false | false | false | true |

The exact ten-peer preflight passed. One authorized transaction began at host
monotonic T0 `158005.656035628` with absolute confirmation deadline
`158175.656035628`. The updater Master was flashed and the production Master
was restored through `finally`. The in-transaction confirmer initially could
not find the production USB CDC device. An independent rescue verifier then
ran under the same absolute deadline, without re-uploading or creating a new
budget. It collected 102 samples; every live reply remained
`PONG name=BSF6C53 fw=b306-imu-relay-v46 proto=7`. It never observed v47,
FWID, active-image SHA, or target-unconfirmed state, and expired after 128.491
seconds.

Post-failure read-only slot inventory is identical to the pre-state. Therefore
the evidence supports the narrow conclusion that v47 was not written to either
slot. It does not support upload, activation, PREPARE, COMMIT, or durable
confirmation success. There was no observable target-unconfirmed state to
rescue, and both slots have `pending=false`.

Because canonical v47 never ran on BSF6C53, the required real state transition,
runtime active-image readback, confirmed=1 proof, ten v47 reboot-only samples,
new conservative timing bound, and hard-power persistence acceptance are all
not performed. Hard power was neither requested nor used as an activation or
recovery method.

## Recovery and fleet protection

The production Master was restored to `dk-fusion-imu-relay-v36`. The final
read-only inventory passed ten consecutive one-second samples with exactly the
expected ten unique peers, `count=10`, `ready=10`, every peer connected and
subscribed, exact requested-node PING replies, and no duplicate or unexpected
peers.

No write operation was performed against BSF3C79, BSFC2CC, BSF44AD, BSF8BC4,
BSF1120, BSF31CC, BSFAA61, BSFB165, or BSFEC35. Specifically, they received no
OTA upload, pending/test mark, PREPARE, COMMIT, B306 REBOOT, hard power cycle,
SWD access, slot write, or erase.

The nine-board plan is recorded as a non-executable dry-run in
`B306_Part/releases/v47/fleet_rollout_dry_run.json`. Its result is
`BLOCKED_NO_COMMAND_SENT`; no fleet command was transmitted. An executable
campaign remains forbidden until BSF6C53 completes all hardware gates and the
operator gives explicit fleet authorization.

## Evidence index

- Pre-slot inventory: `bsf6c53_pre_slot_inventory/result.json`
- Exact target preflight: `bsf6c53_qualification/target_preflight.json`
- Transaction ledger: `bsf6c53_qualification/transaction/transaction.json`
- Independent rescue result: `bsf6c53_qualification/confirm_rescue/result.json`
- Post-failure slot inventory: `bsf6c53_post_failure_slot_inventory/result.json`
- Final ten-peer inventory: `final_ten_peer_inventory/result.json`

Raw serial and RTT evidence remains under this report directory and was not
deleted or rewritten.
