# Marker Tracking

This project now has three tracking routes:

- OpenPose / MMPose: human pose route. CPU is slow on this ThinkPad, so keep it as a backup.
- `desktop_ai_camera/track_checkerboard.py`: existing black/white checkerboard center tracking.
- ArUco / ChArUco: marker-based 6DoF tracking. This is the current focus.

## Minimal Install

Use the project-local environment, not the MMPose venv:

```bash
source .venv/bin/activate
python -m pip install --upgrade-strategy only-if-needed numpy opencv-contrib-python scipy pillow matplotlib pyyaml
```

Optional:

```bash
python -m pip install --upgrade-strategy only-if-needed mediapipe ultralytics
```

Do not install or upgrade `torch`, `torchvision`, `mmcv`, `mmpose`, or OpenPose for this marker workflow.

## Verify

```bash
python - <<'PY'
import cv2
import numpy as np
print("cv2:", cv2.__version__)
print("has aruco:", hasattr(cv2, "aruco"))
print("numpy:", np.__version__)
PY
```

## Generate Markers

Generate wristband ArUco markers:

```bash
python tools/generate_wristband_markers.py --dict 4x4_100 --id-min 30 --id-max 57 --marker-mm 25 --dpi 600
```

Output:

```text
tracking_markers/aruco/wristband_aruco_4x4_100_ids_30_57_A4.pdf
tracking_markers/aruco/aruco_4x4_100_id_30.png
...
tracking_markers/aruco/aruco_4x4_100_id_57.png
```

Generate a ChArUco board:

```bash
python tools/generate_charuco_board.py --squares-x 5 --squares-y 7 --square-mm 30 --marker-mm 22 --dict 4x4_100 --dpi 600
```

Output:

```text
tracking_markers/charuco/charuco_5x7_square30_marker22_A4.pdf
tracking_markers/charuco/charuco_5x7_square30_marker22.png
```

Print PDF files at `100% scale` / `Actual size`. Do not use `Fit to page`.

## Recommended Test Order

1. Print `tracking_markers/aruco/wristband_aruco_4x4_100_ids_30_57_A4.pdf`.
2. Test one or two markers.
3. Test the wristband multi-marker script.
4. Test the existing checkerboard script.
5. Use OpenPose/MMPose only if human pose is needed.

## A/B ArUco Pose

```bash
python desktop_ai_camera/track_aruco_ab_pose.py \
  --camera 0 \
  --ids 30 31 \
  --marker-size-m 0.025 \
  --fov-deg 60
```

Terminal output includes A marker pose, B marker pose, and `B relative to A` when both are visible.

## Wristband ArUco Pose

```bash
python desktop_ai_camera/track_wristband_pose.py \
  --camera 0 \
  --id-min 30 \
  --id-max 57 \
  --marker-size-m 0.025 \
  --fov-deg 60
```

Save CSV:

```bash
python desktop_ai_camera/track_wristband_pose.py \
  --camera 0 \
  --id-min 30 \
  --id-max 57 \
  --marker-size-m 0.025 \
  --fov-deg 60 \
  --csv wristband_pose_log.csv
```

CSV columns:

```text
frame_idx,timestamp,id,x,y,z,distance,yaw,pitch,roll,rvec_x,rvec_y,rvec_z,tvec_x,tvec_y,tvec_z
```

## ChArUco Pose

```bash
python desktop_ai_camera/track_charuco_pose.py \
  --camera 0 \
  --squares-x 5 \
  --squares-y 7 \
  --square-length-m 0.03 \
  --marker-length-m 0.022 \
  --fov-deg 60
```

## Existing Checkerboard

Your existing 8x8 square checkerboard has 7x7 internal corners:

```bash
python desktop_ai_camera/track_checkerboard.py \
  --camera 0 \
  --cols 7 \
  --rows 7
```

## Optional Routes

MediaPipe Pose, if installed and model files exist:

```bash
python desktop_ai_camera/run_mediapipe_pose.py \
  --camera 0 \
  --model models/pose_landmarker_lite.task
```

YOLO11 CPU, if `ultralytics` and model files exist:

```bash
python desktop_ai_camera/yolo11_camera.py \
  --camera 0 \
  --model models/yolo11n.pt \
  --device cpu
```

These optional route files are present, but the current marker tracking workflow does not depend on them.
