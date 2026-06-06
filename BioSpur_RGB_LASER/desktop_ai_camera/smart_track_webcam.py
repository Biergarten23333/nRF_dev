#!/usr/bin/env python3
"""Track one target from the webcam and report stable center offsets."""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

import cv2
from ultralytics import YOLO


@dataclass
class Detection:
    track_id: int | None
    name: str
    conf: float
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def cx(self) -> int:
        return (self.x1 + self.x2) // 2

    @property
    def cy(self) -> int:
        return (self.y1 + self.y2) // 2

    @property
    def area(self) -> int:
        return max(0, self.x2 - self.x1) * max(0, self.y2 - self.y1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smart webcam target tracking")
    parser.add_argument("--camera", type=int, default=0, help="OpenCV camera index")
    parser.add_argument("--model", default="yolo11n.pt", help="YOLO model name or path")
    parser.add_argument("--conf", type=float, default=0.35, help="Detection confidence threshold")
    parser.add_argument("--width", type=int, default=640, help="Capture width")
    parser.add_argument("--height", type=int, default=480, help="Capture height")
    parser.add_argument("--target-class", default="person", help="Class to lock onto")
    parser.add_argument(
        "--select",
        choices=("center", "confidence", "area"),
        default="center",
        help="How to choose a target when several candidates exist",
    )
    parser.add_argument("--lost-frames", type=int, default=12, help="Frames before reacquiring target")
    parser.add_argument("--print-every", type=float, default=0.25, help="Seconds between terminal reports")
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


def detections_from_result(result) -> list[Detection]:
    detections: list[Detection] = []
    names = result.names
    boxes = result.boxes
    if boxes is None:
        return detections

    ids = boxes.id
    for idx, box in enumerate(boxes):
        cls_id = int(box.cls[0])
        track_id = int(ids[idx]) if ids is not None else None
        x1, y1, x2, y2 = [int(v) for v in box.xyxy[0]]
        detections.append(
            Detection(
                track_id=track_id,
                name=names.get(cls_id, str(cls_id)),
                conf=float(box.conf[0]),
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
            )
        )
    return detections


def choose_target(candidates: list[Detection], mode: str, frame_w: int, frame_h: int) -> Detection | None:
    if not candidates:
        return None

    if mode == "confidence":
        return max(candidates, key=lambda det: det.conf)
    if mode == "area":
        return max(candidates, key=lambda det: det.area)

    center_x = frame_w // 2
    center_y = frame_h // 2
    return min(candidates, key=lambda det: abs(det.cx - center_x) + abs(det.cy - center_y))


def draw_detection(frame, det: Detection, locked: bool) -> None:
    color = (0, 210, 255) if locked else (80, 160, 80)
    label = f"{'LOCK ' if locked else ''}{det.name}"
    if det.track_id is not None:
        label += f" #{det.track_id}"
    label += f" {det.conf:.2f}"

    cv2.rectangle(frame, (det.x1, det.y1), (det.x2, det.y2), color, 2)
    cv2.circle(frame, (det.cx, det.cy), 4, color, -1)
    cv2.putText(
        frame,
        label,
        (det.x1, max(20, det.y1 - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        color,
        2,
        cv2.LINE_AA,
    )


def main() -> None:
    args = parse_args()
    model = YOLO(args.model)
    cap = open_camera(args.camera, args.width, args.height)

    locked_id: int | None = None
    lost_count = 0
    last_report = 0.0
    last_frame = time.perf_counter()
    fps = 0.0

    print("Press q or Esc in the video window to quit.")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError("Camera read failed")

            height, width = frame.shape[:2]
            result = model.track(frame, conf=args.conf, persist=True, tracker="bytetrack.yaml", verbose=False)[0]
            detections = detections_from_result(result)
            candidates = [det for det in detections if det.name == args.target_class]

            target = None
            if locked_id is not None:
                target = next((det for det in candidates if det.track_id == locked_id), None)
                if target is None:
                    lost_count += 1
                    if lost_count >= args.lost_frames:
                        locked_id = None
                        lost_count = 0
                else:
                    lost_count = 0

            if locked_id is None:
                target = choose_target(candidates, args.select, width, height)
                locked_id = target.track_id if target is not None else None

            frame_cx = width // 2
            frame_cy = height // 2
            cv2.line(frame, (frame_cx - 18, frame_cy), (frame_cx + 18, frame_cy), (255, 255, 255), 1)
            cv2.line(frame, (frame_cx, frame_cy - 18), (frame_cx, frame_cy + 18), (255, 255, 255), 1)

            for det in detections:
                draw_detection(frame, det, target is not None and det.track_id == target.track_id)

            now = time.perf_counter()
            dt = now - last_frame
            last_frame = now
            if dt > 0:
                fps = (fps * 0.85) + ((1.0 / dt) * 0.15)

            status = "NO TARGET"
            if target is not None:
                dx = target.cx - frame_cx
                dy = target.cy - frame_cy
                status = f"LOCK {target.name} #{target.track_id} dx={dx} dy={dy}"
                cv2.line(frame, (frame_cx, frame_cy), (target.cx, target.cy), (0, 210, 255), 2)
                if now - last_report >= args.print_every:
                    print(
                        f"TARGET id={target.track_id} name={target.name} conf={target.conf:.2f} "
                        f"cx={target.cx} cy={target.cy} dx={dx} dy={dy} area={target.area}",
                        flush=True,
                    )
                    last_report = now
            elif now - last_report >= args.print_every:
                print("TARGET none", flush=True)
                last_report = now

            cv2.putText(
                frame,
                f"{status} | FPS {fps:.1f}",
                (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            cv2.imshow("Smart ThinkPad Webcam Tracking", frame)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()

