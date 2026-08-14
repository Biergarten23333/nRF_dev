# BioSpur Fusion Part

`Fusion_Part` is the canonical owner of offline full-body ingest, clock
alignment, UWB/IMU frontends, session calibration, articulated estimation and
IK/FK-ready output. `B306_Part` remains the owner of firmware, BLE/USB
transport, OTA and hardware diagnostics.

The V2 pipeline is fail-closed. A real capture cannot enter calibration or the
body graph until Listener-backed common-clock Gate 0 passes. The current
capture is bound by `config/captures/v47_ten_node_body_calibration_20260814_093601.json`.
Run tests with:

```bash
PYTHONPATH=Fusion_Part/src python3 -m pytest Fusion_Part/tests
```

Run the offline replay with:

```bash
PYTHONPATH=Fusion_Part/src python3 Fusion_Part/tools/run_body_fusion_v2.py
```

The runner has no hardware imports and refuses geometry/raw hash mismatch.
