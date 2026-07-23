# PC capture and parsing

This directory will contain the PC-side receiver, binary parser, capture writer,
and timing-validation tools for BioSpur Fusion.

Every capture must record both MCU firmware SHAs, node identity, BLE PHY,
connection interval, ATT MTU/data length, IMU rate, UWB interface version, and
GPIO-ready convention. Raw input and derived output belong in timestamped
directories under `../../logs/`, not beside the scripts.

The first host milestone is lossless capture and alignment diagnostics. ES-EKF
fusion remains host-side and starts only after the measurement model and R
matrix are frozen.

`fusion_config.json` deliberately contains a REQUIRED-VALUE lever-arm
placeholder. No KiCad schematic or PCB file was present in either repository
searched on 2026-07-23, so the approximately 400 mil scale was not converted
into guessed axis components. The required convention is IMU package center to
UWB antenna geometric center, expressed in the IMU body frame. Host fusion must
refuse to run while any component is null.

`../../tools/fusion_session.py` is the ordered box-orchestration prototype:

```bash
python3 B306_Part/tools/fusion_session.py start \
  --bsf BSF1234 --path relay --tag-id 1 --slot 0 --count 10
python3 B306_Part/tools/fusion_session.py stop --clear-tdma
```

It resolves the Fusion Master by VID:PID `2FE3:10F4` and product name, opens
CDC with DTR/RTS disabled, writes a pre-run prediction file before touching the
rig, and enforces S1–S7/T1–T3 with bounded waits. `--path master` uses the
existing Master_Tag `tdma hold/clear/freq/roster/rebalance/show` mechanism;
`--path relay` uses direct `TAG CFG`. Formal starts perform a B306 software
reboot before S1. `--no-preflight-reboot` exists only for controlled debugging.
