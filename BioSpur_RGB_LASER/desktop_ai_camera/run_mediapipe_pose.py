#!/usr/bin/env python3
"""Run MediaPipe Pose Landmarker webcam demo on CPU."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MediaPipe Pose Landmarker webcam demo")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--model", type=Path, default=Path("models/pose_landmarker_lite.task"))
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    return parser.parse_args()


def open_camera(index: int, width: int, height: int) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
    if not cap.isOpened():
        cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open camera index {index}")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    return cap


def draw_landmarks(frame, result) -> int:
    count = 0
    height, width = frame.shape[:2]
    for pose_landmarks in result.pose_landmarks:
        for landmark in pose_landmarks:
            x = int(landmark.x * width)
            y = int(landmark.y * height)
            if 0 <= x < width and 0 <= y < height:
                cv2.circle(frame, (x, y), 2, (0, 255, 255), -1)
        count += 1
    return count


def main() -> None:
    args = parse_args()
    if not args.model.exists():
        raise FileNotFoundError(
            f"Model not found: {args.model}. Run python tools/download_mediapipe_models.py"
        )

    try:
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision
    except ImportError as exc:
        raise RuntimeError("mediapipe is not installed. Install it only if needed: python -m pip install mediapipe") from exc

    base_options = python.BaseOptions(model_asset_path=str(args.model))
    options = vision.PoseLandmarkerOptions(
        base_options=base_options,
        running_mode=vision.RunningMode.VIDEO,
        num_poses=1,
    )

    cap = open_camera(args.camera, args.width, args.height)
    last_frame = time.perf_counter()
    start = last_frame
    fps = 0.0

    print("Press q or Esc in the video window to quit.")
    try:
        with vision.PoseLandmarker.create_from_options(options) as landmarker:
            while True:
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError("Camera read failed")
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                timestamp_ms = int((time.perf_counter() - start) * 1000)
                result = landmarker.detect_for_video(mp_image, timestamp_ms)
                poses = draw_landmarks(frame, result)

                now = time.perf_counter()
                dt = now - last_frame
                last_frame = now
                if dt > 0:
                    fps = fps * 0.85 + (1.0 / dt) * 0.15
                cv2.putText(
                    frame,
                    f"MediaPipe Pose | poses {poses} | FPS {fps:.1f}",
                    (12, 28),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (255, 255, 255),
                    2,
                    cv2.LINE_AA,
                )
                cv2.imshow("MediaPipe Pose", frame)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
