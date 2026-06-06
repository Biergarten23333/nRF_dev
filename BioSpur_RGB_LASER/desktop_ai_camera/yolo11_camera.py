#!/usr/bin/env python3
"""Run YOLO11 webcam detection on CPU by default."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="YOLO11 CPU webcam demo")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--model", type=Path, default=Path("models/yolo11n.pt"))
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--conf", type=float, default=0.35)
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


def main() -> None:
    args = parse_args()
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError("ultralytics is not installed. Install it only if you need YOLO: python -m pip install ultralytics") from exc

    if not args.model.exists():
        raise FileNotFoundError(f"Model not found: {args.model}. Run python tools/download_yolo_models.py")

    model = YOLO(str(args.model))
    cap = open_camera(args.camera, args.width, args.height)
    last_frame = time.perf_counter()
    fps = 0.0

    print("Press q or Esc in the video window to quit.")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError("Camera read failed")
            result = model.predict(frame, conf=args.conf, device=args.device, verbose=False)[0]
            annotated = result.plot()

            now = time.perf_counter()
            dt = now - last_frame
            last_frame = now
            if dt > 0:
                fps = fps * 0.85 + (1.0 / dt) * 0.15
            cv2.putText(
                annotated,
                f"YOLO11 {args.device} | FPS {fps:.1f}",
                (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            cv2.imshow("YOLO11 Camera", annotated)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
