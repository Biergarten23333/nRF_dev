#!/usr/bin/env python3
"""Run real-time object detection from a local webcam."""

from __future__ import annotations

import argparse
import time

import cv2
from ultralytics import YOLO


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Real-time webcam object detection")
    parser.add_argument("--camera", type=int, default=0, help="OpenCV camera index")
    parser.add_argument("--model", default="yolo11n.pt", help="YOLO model name or path")
    parser.add_argument("--conf", type=float, default=0.35, help="Confidence threshold")
    parser.add_argument("--width", type=int, default=640, help="Capture width")
    parser.add_argument("--height", type=int, default=480, help="Capture height")
    parser.add_argument(
        "--classes",
        nargs="*",
        default=None,
        help="Optional class-name allowlist, for example: person bottle cup",
    )
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


def draw_detections(frame, result, allowed_names: set[str] | None) -> int:
    count = 0
    names = result.names

    for box in result.boxes:
        cls_id = int(box.cls[0])
        name = names.get(cls_id, str(cls_id))
        if allowed_names is not None and name not in allowed_names:
            continue

        conf = float(box.conf[0])
        x1, y1, x2, y2 = [int(v) for v in box.xyxy[0]]
        label = f"{name} {conf:.2f}"

        cv2.rectangle(frame, (x1, y1), (x2, y2), (40, 210, 90), 2)
        cv2.putText(
            frame,
            label,
            (x1, max(20, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (40, 210, 90),
            2,
            cv2.LINE_AA,
        )
        count += 1

    return count


def main() -> None:
    args = parse_args()
    allowed_names = set(args.classes) if args.classes else None

    model = YOLO(args.model)
    cap = open_camera(args.camera, args.width, args.height)

    last_time = time.perf_counter()
    fps = 0.0

    print("Press q or Esc in the video window to quit.")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError("Camera read failed")

            result = model.predict(frame, conf=args.conf, verbose=False)[0]
            count = draw_detections(frame, result, allowed_names)

            now = time.perf_counter()
            dt = now - last_time
            last_time = now
            if dt > 0:
                fps = (fps * 0.85) + ((1.0 / dt) * 0.15)

            cv2.putText(
                frame,
                f"FPS {fps:.1f} | detections {count}",
                (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            cv2.imshow("ThinkPad Webcam Object Detection", frame)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()

