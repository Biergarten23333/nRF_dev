# Five-node IMUCoCo adapter

This package connects the published IMUCoCo encoder and DTP pose checkpoint to
the five retained BioSpur IMUs. It does not use the previous missing-segment
pose optimizer. The upstream Python sources remain unchanged in
`third_party/imucoco`; `UPSTREAM_MANIFEST.json` pins their revision and hashes.

Ownership:

- `calibration.py`: five-node functional sensor mounting, acceleration bias,
  T-pose reference and per-action evidence. H-series inputs are rejected.
- `preprocessing.py`: original common timestamps, 60 Hz sampling, world-frame
  conversion and the author's six orientation plus three acceleration inputs.
- `upstream.py`, `chunked.py`, `backend.py`: released weights and recurrent
  state. Chunk boundaries preserve hidden state; encoder equivalence to the
  author's sequential implementation is tested using the real checkpoint.
- `body.py`: an explicitly approximate SMPL shape fit to side-labelled tape
  measurements, used for forward kinematics. It does not condition the
  pretrained orientation network on subject shape.
- `workflow.py`: immutable stage outputs, asset/input hashes and probe gates.

## Run

From the Fusion_Part workspace, use its existing `.venv-v0/bin/python` and
`PYTHONPATH=src`. `tools/run_c2_imucoco.py` accepts these stages in order:

```
--stage prepare --output logs/<new-run>
--stage encoder --output logs/<new-run>
--stage probe --output logs/<new-run> --smpl <official-male-model.pkl>
--stage replay --diagnostic-only --output logs/<new-run> --smpl <official-male-model.pkl>
```

For the official 2015 Python model, first run `tools/convert_smpl_legacy.py`
with the original pickle and a new destination path. This one-time utility
uses Chumpy 0.70 to materialize its shape array without changing its values;
the resulting NumPy pickle loads in current Python without Chumpy runtime
patches. It saves source/output hashes and preserves the original download.

Each command runs in its own process. The upstream body loader changes the
process working directory; do not call it inside an interactive application.
The full replay requires a matching successful 300-frame probe and checks its
source, calibration, SMPL and output hashes. A successful finite-output probe
is not motion-accuracy acceptance. The recorded real-input pose chunk
equivalence check is `POSE_CHUNK_PARITY.json` in the reproduction run.

`prepare` now writes `CALIBRATION_CANDIDATE.json` with explicit incomplete
status. The earlier `CALIBRATION_FROZEN.json` was a reproducibility snapshot,
not an accepted calibration. Full replay currently requires the diagnostic
flag because a complete five-node calibration acceptance path is missing.
The protocol separates proposed action roles from implemented parameter
products; input statistics do not count as calibration validation. Stored
legacy elbow and geometry values unused by inference are labelled as such.

## Current experimental limits

The frontend is offline: it uses centred acceleration filtering and a C2 yaw
closure. Invalid intervals stay on the timeline and are marked in the result;
they are not deleted to make streams appear synchronized. Interpolated gap
inputs can also influence subsequent recurrent predictions.

Sensor surface positions use the author's wrist and above-ankle vertex
proxies. These are fixed body-relative placement descriptions, not UWB
measurements. Tape lengths are external landmarks, so the current shape fit
uses a stated 20 mm minimum mapping uncertainty instead of treating them as
exact internal joint lengths. Global translation is not evaluated.

The H-series was viewed during earlier failed attempts and is a regression
set, not a new blind test. No H pose or removed-node data enters fitting.
The old review server is not a visualization of this implementation.

The official SMPL asset is distributed separately and requires the user's
website login. Preserve its license and provenance; this code does not
substitute a fabricated or unofficial body model.
