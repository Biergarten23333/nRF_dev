#!/usr/bin/env python3
"""Track many ArUco markers for wristband, wrist, forearm, and hand experiments."""

from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

import cv2
import numpy as np

from camera_utils import (
    detect_aruco_markers,
    draw_aruco_markers,
    draw_pose_axes,
    estimate_square_marker_pose,
    get_aruco_dictionary,
    load_calibration_or_fov,
    require_aruco,
    rvec_to_euler_deg,
)


CSV_COLUMNS = [
    "frame_idx",
    "timestamp",
    "id",
    "x",
    "y",
    "z",
    "distance",
    "yaw",
    "pitch",
    "roll",
    "rvec_x",
    "rvec_y",
    "rvec_z",
    "tvec_x",
    "tvec_y",
    "tvec_z",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Track wristband ArUco marker 6DoF pose")
    parser.add_argument("--camera", type=int, default=0, help="OpenCV camera index")
    parser.add_argument("--dict", default="4x4_100", help="ArUco dictionary")
    parser.add_argument("--id-min", type=int, default=30, help="Minimum marker ID to keep")
    parser.add_argument("--id-max", type=int, default=57, help="Maximum marker ID to keep")
    parser.add_argument("--marker-size-m", type=float, default=0.025, help="Printed marker side length in meters")
    parser.add_argument("--fov-deg", type=float, default=60.0, help="Horizontal camera FOV fallback")
    parser.add_argument("--calib", default="calibration/camera.yaml", help="Optional camera calibration YAML")
    parser.add_argument("--width", type=int, default=640, help="Capture width")
    parser.add_argument("--height", type=int, default=480, help="Capture height")
    parser.add_argument("--print-every", type=float, default=10.0, help="Frames between terminal reports")
    parser.add_argument("--csv", type=Path, default=None, help="Optional CSV output path")
    parser.add_argument("--draw-axes", action=argparse.BooleanOptionalAction, default=True)
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


def draw_marker_text(
    frame: np.ndarray,
    marker_id: int,
    corners: np.ndarray,
    distance: float,
    yaw: float,
    pitch: float,
    roll: float,
) -> None:
    x, y = np.asarray(corners).reshape(4, 2).mean(axis=0).astype(int)
    lines = [
        f"ID {marker_id}",
        f"Z {distance:.3f}m",
        f"YPR {yaw:.0f}/{pitch:.0f}/{roll:.0f}",
    ]
    for idx, text in enumerate(lines):
        cv2.putText(
            frame,
            text,
            (int(x) + 8, int(y) + 18 + idx * 17),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 255, 255),
            1,
            cv2.LINE_AA,
        )


def main() -> None:
    args = parse_args()
    require_aruco()
    dictionary = get_aruco_dictionary(args.dict)
    cap = open_camera(args.camera, args.width, args.height)

    ok, frame = cap.read()
    if not ok:
        cap.release()
        raise RuntimeError("Camera opened but first frame read failed")

    frame_h, frame_w = frame.shape[:2]
    camera_matrix, dist_coeffs, calib_source = load_calibration_or_fov(
        Path(args.calib), frame_w, frame_h, args.fov_deg
    )
    print(f"Calibration source: {calib_source}")
    print("Press q or Esc in the video window to quit.")

    csv_file = None
    writer = None
    if args.csv is not None:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        csv_file = args.csv.open("w", newline="", encoding="utf-8")
        writer = csv.DictWriter(csv_file, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        print(f"Writing CSV: {args.csv}")

    frame_idx = 0
    last_frame = time.perf_counter()
    fps = 0.0

    try:
        while True:
            if frame_idx > 0:
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError("Camera read failed")

            timestamp = time.time()
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            corners, ids, _ = detect_aruco_markers(gray, dictionary)
            visible_rows = []

            if ids is not None:
                draw_aruco_markers(frame, corners, ids)
                for marker_corners, marker_id_arr in zip(corners, ids.reshape(-1)):
                    marker_id = int(marker_id_arr)
                    if marker_id < args.id_min or marker_id > args.id_max:
                        continue
                    success, rvec, tvec = estimate_square_marker_pose(
                        marker_corners, args.marker_size_m, camera_matrix, dist_coeffs
                    )
                    if not success:
                        continue
                    yaw, pitch, roll = rvec_to_euler_deg(rvec)
                    t = tvec.reshape(3)
                    r = rvec.reshape(3)
                    distance = float(np.linalg.norm(t))
                    row = {
                        "frame_idx": frame_idx,
                        "timestamp": f"{timestamp:.6f}",
                        "id": marker_id,
                        "x": f"{t[0]:.6f}",
                        "y": f"{t[1]:.6f}",
                        "z": f"{t[2]:.6f}",
                        "distance": f"{distance:.6f}",
                        "yaw": f"{yaw:.6f}",
                        "pitch": f"{pitch:.6f}",
                        "roll": f"{roll:.6f}",
                        "rvec_x": f"{r[0]:.6f}",
                        "rvec_y": f"{r[1]:.6f}",
                        "rvec_z": f"{r[2]:.6f}",
                        "tvec_x": f"{t[0]:.6f}",
                        "tvec_y": f"{t[1]:.6f}",
                        "tvec_z": f"{t[2]:.6f}",
                    }
                    visible_rows.append(row)
                    if writer is not None:
                        writer.writerow(row)
                    if args.draw_axes:
                        draw_pose_axes(frame, camera_matrix, dist_coeffs, rvec, tvec, args.marker_size_m * 0.5)
                    draw_marker_text(frame, marker_id, marker_corners, distance, yaw, pitch, roll)

            now = time.perf_counter()
            dt = now - last_frame
            last_frame = now
            if dt > 0:
                fps = fps * 0.85 + (1.0 / dt) * 0.15

            if frame_idx % max(1, int(args.print_every)) == 0:
                if visible_rows:
                    for row in visible_rows:
                        print(
                            f"frame={row['frame_idx']} id={row['id']} "
                            f"xyz=({row['x']},{row['y']},{row['z']}) "
                            f"dist={row['distance']} "
                            f"ypr=({row['yaw']},{row['pitch']},{row['roll']})",
                            flush=True,
                        )
                else:
                    print("markers none", flush=True)

            cv2.putText(
                frame,
                f"Wristband ArUco Pose | visible {len(visible_rows)} | FPS {fps:.1f}",
                (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.62,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            cv2.imshow("Wristband ArUco Pose", frame)
            frame_idx += 1

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
    finally:
        if csv_file is not None:
            csv_file.close()
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
