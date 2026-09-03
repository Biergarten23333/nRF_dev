# BioSpur Fusion V0

V0 is the runnable ten-node, magnetometer-free, IMU-only body-relative baseline. It uses the UWB Beacon/network only as the provenance of `global_time_ns`; no range, position, spatial residual, pose correction, or UWB-derived geometry reaches the estimator.

## Reproducible workflow

From `Fusion_Part`:

```bash
python3 -m venv --system-site-packages .venv-v0
.venv-v0/bin/pip install -r config/biospur_fusion_v0/requirements-lock.txt
tools/run_biospur_fusion_v0.sh logs/biospur_fusion_v0_golden_<new-UTC-timestamp>
```

The output directory must not already exist. The command performs Capture1 calibration first, writes and checksums the frozen session profile, executes the full replay twice through the actual runtime boundary (spatial payload absent versus hostile randomized/NaN payload), and emits:

- `V0_SESSION_PROFILE.json`: immutable development session profile;
- `V0_STATE.npz`: native timestamps, full segment SO(3), generalized joint SO(3), FK positions, bias/rest, residual, confidence, and uncertainty;
- `V0_VIEWER.html`: self-contained interactive body-relative replay;
- `FINAL_RESULT.json` and `REPORT.md`: classification and concise result;
- focused comparison, isolation, component-influence, drift/stability, and access audits;
- `SHA256SUMS`: artifact integrity manifest.

Verify an accepted output without rerunning estimation:

```bash
PYTHONPATH=src .venv-v0/bin/python tools/verify_biospur_fusion_v0.py \
  logs/biospur_fusion_v0_golden_<UTC-timestamp>
```

## Active architecture and conventions

The selected frontend is VQF 2.0.1 for six-axis tilt, rest, gyro-bias state, and bias uncertainty. VQF’s API accepts a fixed sampling period, so each uninterrupted block receives its measured median native period. The active yaw closure separately propagates every accepted interval using `(global_time_ns[i] - global_time_ns[i-1]) / 1e9` and projects the VQF/native difference as an SO(3) world-yaw twist. It resets VQF at gaps and boot changes and never uses Q1’s `/200` motion-gate diagnostic.

qmt 0.2.4 `headingCorrection` supplies soft one-dimensional joint-motion heading evidence independently on contiguous 10 Hz valid blocks. Measured functional-axis dispersion and qmt rating bound the applied gain. The evidence confidence enters the shared IK observation weights; full SO(3) remains available at elbows and knees, and shoulders/hips remain 3-DOF.

The shared solve follows the OpenSense/OpenSim concept of fitting calibrated measured IMU orientations to model IMU orientations, but executes the repository-native full-SO(3) body graph and canonical FK. It deliberately does not copy OpenSim’s model, hard knee/ankle joint choices, anthropometry, or solver. Geometry is a display-only, non-metric convention because non-UWB metric joint centres are unavailable.

Quaternion serialization is scalar-first `wxyz`. Gyroscope input is radians/second, accelerometer input is metres/second squared, rotations are proper 3×3 matrices mapping sensor or segment coordinates into the session/world gauge, and global yaw is not north-referenced.

## Pinned external references

| Component | Version/source | License | Executed use |
|---|---|---|---|
| VQF | Python package `vqf==2.0.1`; https://github.com/dlaidig/vqf | MIT | `VQF.updateBatch(gyr, acc)` per valid block; tilt/rest/bias/uncertainty plus native-dt yaw-twist wrapper |
| VQF paper | Laidig & Seel, *Information Fusion* 91 (2023), DOI `10.1016/j.inffus.2022.10.014` | citation | method reference |
| qmt | Python package `qmt==0.2.4`; https://github.com/dlaidig/qmt | MIT | `qmt.headingCorrection(..., constraint="proj")` as bounded relative-heading evidence |
| OpenSense/OpenSim | https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53084203 | Apache-2.0 software | conceptual orientation-IK reference only; no OpenSim code imported |
| OpenSense paper | Al Borno et al., *J NeuroEngineering Rehabil* 19, 22 (2022), DOI `10.1186/s12984-022-01001-x` | citation | weighted orientation-residual IK reference |

## Reuse/reject map

| Existing capability | V0 decision |
|---|---|
| Production typed ledger, accepted-row status, `global_time_ns`, boot epochs | Reused through an exact-name IMU-only adapter |
| R6A1A native-time preintegrator and timing contract | Reused as timing provenance and regression; V0 orientation uses its own explicit native-dt closure |
| Q1 attitude/rest/bias frontend | Executed as equal-input comparator; rejected as active V0 frontend because its motion gate contains `/200` and it exposes no useful rest state in this Capture1 comparison |
| R6A0 canonical body graph and FK | Reused as the one skeleton generator |
| R6A2A corrected identity/body adapter | Reused; notably `BSFC2CC` is pelvis |
| R6A2B Layer-B sensor-to-segment rotations | Reused after R4A provenance audit showed no UWB route into those rotation coordinates |
| R6A2B-R4 IMU-only functional axes and uncertainty | Reused as soft descriptors, never hard hinges |
| Historical 87-slot registry | Preserved and not modified; V0 writes a separate profile |
| Layer-C translations/joint centres, levers, V4 transform, UWB factors/corrections | Rejected from V0 because their spatial provenance includes UWB |
| Earlier replay/viewer implementations | Reused as design precedent; V0 emits a self-contained canvas viewer against the canonical-FK export |

## Claim boundary

V0 is calibration/replay verification on the eleven authoritative Capture1 calibration windows. It is not independent ordinary-action validation, external attitude accuracy, clinical joint-angle validation, qualified metric anthropometry, absolute yaw, or measured root position. Golf and Boxing remain unopened. Root translation is a fixed display gauge, high uncertainty and robust residuals remain visible, and skin/strap motion is robustly accommodated or unmodelled—there is no active slip state.
