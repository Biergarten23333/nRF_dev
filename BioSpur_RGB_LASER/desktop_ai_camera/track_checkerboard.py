#!/usr/bin/env python3
"""Track a black/white checkerboard from the webcam."""

from __future__ import annotations

import argparse
import time

import cv2
import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Track a checkerboard target")
    parser.add_argument("--camera", type=int, default=0, help="OpenCV camera index")
    parser.add_argument("--width", type=int, default=640, help="Capture width")
    parser.add_argument("--height", type=int, default=480, help="Capture height")
    parser.add_argument(
        "--cols",
        type=int,
        default=7,
        help="Number of internal checkerboard corners across columns",
    )
    parser.add_argument(
        "--rows",
        type=int,
        default=5,
        help="Number of internal checkerboard corners across rows",
    )
    parser.add_argument("--print-every", type=float, default=0.1, help="Seconds between terminal reports")
    parser.add_argument("--blur", type=int, default=3, help="Gaussian blur kernel size, 0 disables blur")
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


def find_checkerboard(gray: np.ndarray, cols: int, rows: int):
    pattern_size = (cols, rows)

    if hasattr(cv2, "findChessboardCornersSB"):
        found, corners = cv2.findChessboardCornersSB(
            gray,
            pattern_size,
            flags=cv2.CALIB_CB_NORMALIZE_IMAGE,
        )
        if found:
            return True, corners

    flags = cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE
    found, corners = cv2.findChessboardCorners(gray, pattern_size, flags)
    if not found:
        return False, None

    criteria = (
        cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,
        30,
        0.001,
    )
    corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)
    return True, corners


def draw_board(frame, corners: np.ndarray, cols: int, rows: int) -> tuple[int, int, float]:
    pts = corners.reshape(-1, 2)
    center = pts.mean(axis=0)
    cx, cy = int(center[0]), int(center[1])

    hull = cv2.convexHull(pts.astype(np.float32)).astype(np.int32)
    area = float(cv2.contourArea(hull))

    cv2.drawChessboardCorners(frame, (cols, rows), corners, True)
    cv2.polylines(frame, [hull], isClosed=True, color=(0, 210, 255), thickness=2)
    cv2.circle(frame, (cx, cy), 5, (0, 210, 255), -1)
    return cx, cy, area


def main() -> None:
    args = parse_args()
    cap = open_camera(args.camera, args.width, args.height)

    last_frame = time.perf_counter()
    last_report = 0.0
    fps = 0.0

    print("Press q or Esc in the video window to quit.")
    print(f"Looking for checkerboard internal corners: cols={args.cols}, rows={args.rows}")

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError("Camera read failed")

            height, width = frame.shape[:2]
            frame_cx = width // 2
            frame_cy = height // 2

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if args.blur > 0:
                kernel = args.blur if args.blur % 2 == 1 else args.blur + 1
                gray = cv2.GaussianBlur(gray, (kernel, kernel), 0)

            found, corners = find_checkerboard(gray, args.cols, args.rows)

            cv2.line(frame, (frame_cx - 18, frame_cy), (frame_cx + 18, frame_cy), (255, 255, 255), 1)
            cv2.line(frame, (frame_cx, frame_cy - 18), (frame_cx, frame_cy + 18), (255, 255, 255), 1)

            status = "NO BOARD"
            now = time.perf_counter()
            if found and corners is not None:
                cx, cy, area = draw_board(frame, corners, args.cols, args.rows)
                dx = cx - frame_cx
                dy = cy - frame_cy
                status = f"BOARD cx={cx} cy={cy} dx={dx} dy={dy} area={area:.0f}"
                cv2.line(frame, (frame_cx, frame_cy), (cx, cy), (0, 210, 255), 2)

                if now - last_report >= args.print_every:
                    print(f"BOARD cx={cx} cy={cy} dx={dx} dy={dy} area={area:.0f}", flush=True)
                    last_report = now
            elif now - last_report >= args.print_every:
                print("BOARD none", flush=True)
                last_report = now

            dt = now - last_frame
            last_frame = now
            if dt > 0:
                fps = (fps * 0.85) + ((1.0 / dt) * 0.15)

            cv2.putText(
                frame,
                f"{status} | FPS {fps:.1f}",
                (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.62,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            cv2.imshow("Checkerboard Tracking", frame)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()

