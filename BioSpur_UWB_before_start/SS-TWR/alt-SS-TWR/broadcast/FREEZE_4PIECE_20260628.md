# FROZEN 4-PIECE SET — 2026-06-28 (restore point before OTP-read detour)

Snapshot of the **currently deployed** working set so it can be restored after the
OTP-reading work in another session. Tag side = today's baseline (RX experiments all
reverted); anchor side = untouched this session (stable since 2026-05-12).

Rig at freeze time: master-tag B120 + master-anchor B120 + 8 anchors + 6 tags.
**4 tags alive (BS9336, BS955A, BSCCF4, BSF66F); 2 dead battery (BS2DCE, BSDC91) —
their flash is intact (baseline); they rejoin once batteries are swapped (PM).**

---

## [1] TAG firmware  (on all 6 DWM1001C tags, via BLE OTA)
- marker:    `compact-sampled-tdmafix-nodiag-a7win-baseline-20260628`
- build dir: `build-tag-ble-unified-tdmafix-nodiag-a7win-baseline-20260628`
- signed.bin sha256: `416d31e3b30d453a01b13f3ff87548f0e38c7acace684684e4eba8ceb21fbcb1`
- dfu_application.zip sha256: `693bdc39a6ccf342468b050f7a29ecc89d37d5e5ac7fd2e85889b50ce44b459e`
- config: APP_ALT_SS_TWR_MODE=BROADCAST, nodiag, POSITION_OUTPUT=0, RXAUTR=OFF, RXDBLBUF=OFF
- **RESTORE (re-OTA all tags):** stage this build then OTA:
  ```
  python3 scripts/prepare_alt_ota_payload.py --kind tag \
    --marker compact-sampled-tdmafix-nodiag-a7win-baseline-20260628 \
    --build-dir build-tag-ble-unified-tdmafix-nodiag-a7win-baseline-20260628 \
    --signed-bin build-tag-ble-unified-tdmafix-nodiag-a7win-baseline-20260628/tag/zephyr/zephyr.signed.bin \
    --dfu-zip   build-tag-ble-unified-tdmafix-nodiag-a7win-baseline-20260628/dfu_application.zip
  # rebuild+flash the master-tag carrier [2] (it embeds this payload), then:
  python3 scripts/ota_deploy_tag_set.py \
    --port /dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00 \
    --out-dir logs/restore_tag_baseline --prefix BS \
    --targets BSF66F,BS2DCE,BSDC91,BS9336,BS955A,BSCCF4
  ```

## [2] MASTER-TAG carrier  (B120 nRF5340, J-Link SNR 1050070698, CDC=Master_Tag)
- build dir: `build-master-control-b120-m1-master-tag-lfrc-a7win-reroll-20260628`
- merged_domains.hex sha256: `a002d0b6a38094e602a9768552529570bf53b4cc87d1fb620f87c70b81e662c4`
- contents: baseline 7.5ms BLE conn interval (6u) + the new `reroll <BSxxxx>` CDC command;
  embeds tag OTA payload [1].
- **RESTORE (J-Link flash):**
  ```
  B120_SNR=1050070698 scripts/flash_master_control_b120_m1_noninteractive.sh \
    build-master-control-b120-m1-master-tag-lfrc-a7win-reroll-20260628/zephyr/merged_domains.hex
  ```
- NOTE: master CDC re-enumerates ttyACM18<->19 on reset; use the by-id path.

## [3] ANCHOR firmware  (on all 8 anchors, via BLE OTA from master-anchor)
- marker:    `altbcast-responder-a18-g1200-r1000-20260512_154806`
- build dir: `build-anchor-unified-ota-altbcast-responder-a18-g1200-r1000-20260512_154806`
- dfu_application.zip sha256: `b1288ef0f8f8e60dd248fb65e6cc666fdac18cb7ef2d2f2a4d1006042f746fc8`
- UNTOUCHED this session. Confirmed live: master-anchor reports
  `OTA_BUNDLE kind=anchor fw=altbcast-responder-a18-g1200-r1000-20260512_154806`.

## [4] MASTER-ANCHOR carrier  (B120 nRF5340, J-Link SNR 960148546 — PROTECTED `.protec/noflash960148546`)
- embeds anchor fw [3] (a18 / g1200-r1000 / 2026-05-12); boots mode=AUTOPOS, CDC=Master_Anchor (ttyACM0).
- carrier build dir candidates (verify before any reflash — anchor side untouched this session):
  `build-master-control-b120-m1-master-anchor-lfrc-anchoronly-altbcast-embed-altbcast-responder-a18-g1200-r1000-20260512_154806`
  (newer rfdiag carriers exist: `...-rfdiag-a27-side-prof-...-20260625`, `...-rfdiag-a26-delayed-prof-...-20260625`).
- This B120 is on the noflash-protected J-Link — DO NOT J-Link-reflash unless intentional.

---

## Quick verify after restore
- master-tag: `status` -> mode=RECV; `reroll bsccf4` -> "REROLL bs=bsccf4 rc=0" (command present).
- tags: `ota_deploy_tag_set.py ... ` VERSION post -> `compact-sampled-tdmafix-nodiag-a7win-baseline-20260628`.
- anchors: master-anchor `anchor version all` / preflight -> 8/8 responders.
- end-to-end: the 6-tag capture (+ optional `--reroll-settle-rounds 6`) runs clean.

In-progress work parked here (not part of the frozen production set): targeted-reroll
①(A) — `reroll` command (in [2]) + `--reroll-settle-rounds` in run_recv_tdma_capture.py;
6-tag convergence validation pending the BS2DCE/BSDC91 battery swap. See memory
`tdma-capacity-ble-phase-beat`.
