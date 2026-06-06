# Desktop AI Camera Test

Use the ThinkPad built-in webcam to run real-time object detection before moving the idea to embedded hardware.

## Recommended Setup Without Conda

This machine is Ubuntu 26.04 with Python 3.14. Use a project-local virtual
environment so system Python stays clean.

Install venv support once if needed:

```bash
sudo apt install python3.14-venv
```

Create the environment and install CPU-only dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r desktop_ai_camera/requirements.txt
```

The requirements pin CPU-only PyTorch wheels so pip does not download CUDA/NVIDIA packages.

## Run

```bash
python desktop_ai_camera/detect_webcam.py --camera 0 --model yolo11n.pt
```

On this ThinkPad, `camera 0` has been verified to read `640x480` color frames.
If the window is black or the wrong device opens, try:

```bash
python desktop_ai_camera/detect_webcam.py --camera 1 --model yolo11n.pt
```

Press `q` or `Esc` to quit.

## Smarter Target Tracking

The basic script detects each frame independently. For robotics or laser control,
use the tracking script instead. It locks onto one target, keeps a stable track
ID, and prints the target center offset from the image center.

```bash
python desktop_ai_camera/smart_track_webcam.py --camera 0 --target-class person
```

Example terminal output:

```text
TARGET id=3 name=person conf=0.81 cx=322 cy=217 dx=2 dy=-23 area=84231
```

For later hardware control, `dx` and `dy` are the useful values:

- `dx < 0`: target is left of center.
- `dx > 0`: target is right of center.
- `dy < 0`: target is above center.
- `dy > 0`: target is below center.

If there are several targets:

```bash
python desktop_ai_camera/smart_track_webcam.py --target-class person --select center
python desktop_ai_camera/smart_track_webcam.py --target-class bottle --select confidence
python desktop_ai_camera/smart_track_webcam.py --target-class cup --select area
```

Ultralytics YOLO supports video tracking through trackers such as ByteTrack and
BoT-SORT, which is what this script uses.

## Checkerboard Tracking

For a black/white checkerboard target, use OpenCV directly instead of AI. This is
faster and more stable for position tracking.

```bash
python desktop_ai_camera/track_checkerboard.py --camera 0 --cols 7 --rows 5
```

`--cols` and `--rows` are the number of internal corners, not the number of
black/white squares. For example:

- An 8 by 6 square board has `--cols 7 --rows 5`.
- A 7 by 5 square board has `--cols 6 --rows 4`.

Output:

```text
BOARD cx=318 cy=244 dx=-2 dy=4 area=28422
```

`dx` and `dy` are the board center offset from the camera image center.

## Useful Options

```bash
python desktop_ai_camera/detect_webcam.py --camera 0 --model yolo11n.pt --conf 0.35 --width 640 --height 480
```

- `--camera`: OpenCV camera index, usually `0` or `1`.
- `--model`: Ultralytics model path or name. `yolo11n.pt` is small and CPU-friendly.
- `--conf`: Detection confidence threshold.
- `--classes`: Optional COCO class names to keep, for example `person bottle cup`.

Example filtering to one class:

```bash
python desktop_ai_camera/detect_webcam.py --classes person
```
