#!/usr/bin/env python3
"""Track a ChArUco board and report board 6DoF pose."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2
import numpy as np

from camera_utils import (
    detect_aruco_markers,
    draw_aruco_markers,
    draw_pose_axes,
    get_aruco_dictionary,
    load_calibration_or_fov,
    require_aruco,
    rvec_to_euler_deg,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Track ChArUco board 6DoF pose")
    parser.add_argument("--camera", type=int, default=0, help="OpenCV camera index")
    parser.add_argument("--squares-x", type=int, default=5, help="Board squares in X direction")
    parser.add_argument("--squares-y", type=int, default=7, help="Board squares in Y direction")
    parser.add_argument("--square-length-m", type=float, default=0.03, help="Square side length in meters")
    parser.add_argument("--marker-length-m", type=float, default=0.022, help="ArUco marker side length in meters")
    parser.add_argument("--dict", default="4x4_100", help="ArUco dictionary")
    parser.add_argument("--fov-deg", type=float, default=60.0, help="Horizontal camera FOV fallback")
    parser.add_argument("--calib", default="calibration/camera.yaml", help="Optional camera calibration YAML")
    parser.add_argument("--width", type=int, default=640, help="Capture width")
    parser.add_argument("--height", type=int, default=480, help="Capture height")
    parser.add_argument("--print-every", type=float, default=10.0, help="Frames between terminal reports")
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


def create_charuco_board(squares_x: int, squares_y: int, square_length: float, marker_length: float, dictionary):
    aruco = require_aruco()
    if hasattr(aruco, "CharucoBoard"):
        try:
            return aruco.CharucoBoard((squares_x, squares_y), square_length, marker_length, dictionary)
        except TypeError:
            return aruco.CharucoBoard_create(squares_x, squares_y, square_length, marker_length, dictionary)
    if hasattr(aruco, "CharucoBoard_create"):
        return aruco.CharucoBoard_create(squares_x, squares_y, square_length, marker_length, dictionary)
    raise RuntimeError("This OpenCV build does not provide ChArUco board APIs.")


def estimate_charuco_pose(board, charuco_corners, charuco_ids, camera_matrix, dist_coeffs):
    aruco = require_aruco()
    rvec = np.zeros((3, 1), dtype=np.float64)
    tvec = np.zeros((3, 1), dtype=np.float64)
    ok, rvec, tvec = aruco.estimatePoseCharucoBoard(
        charuco_corners,
        charuco_ids,
        board,
        camera_matrix,
        dist_coeffs,
        rvec,
        tvec,
    )
    return bool(ok), rvec, tvec


def main() -> None:
    args = parse_args()
    aruco = require_aruco()
    dictionary = get_aruco_dictionary(args.dict)
    board = create_charuco_board(
        args.squares_x,
        args.squares_y,
        args.square_length_m,
        args.marker_length_m,
        dictionary,
    )

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

    frame_idx = 0
    last_frame = time.perf_counter()
    fps = 0.0

    try:
        while True:
            if frame_idx > 0:
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError("Camera read failed")

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            corners, ids, _ = detect_aruco_markers(gray, dictionary)
            visible_markers = 0 if ids is None else len(ids)
            status = "NO BOARD"

            if ids is not None and len(ids) > 0:
                draw_aruco_markers(frame, corners, ids)
                count, charuco_corners, charuco_ids = aruco.interpolateCornersCharuco(
                    corners,
                    ids,
                    gray,
                    board,
                    camera_matrix,
                    dist_coeffs,
                )
                if count is not None and count >= 4 and charuco_ids is not None:
                    if hasattr(aruco, "drawDetectedCornersCharuco"):
                        aruco.drawDetectedCornersCharuco(frame, charuco_corners, charuco_ids)
                    success, rvec, tvec = estimate_charuco_pose(
                        board, charuco_corners, charuco_ids, camera_matrix, dist_coeffs
                    )
                    if success:
                        yaw, pitch, roll = rvec_to_euler_deg(rvec)
                        t = tvec.reshape(3)
                        distance = float(np.linalg.norm(t))
                        draw_pose_axes(frame, camera_matrix, dist_coeffs, rvec, tvec, args.square_length_m)
                        status = (
                            f"xyz=({t[0]:.3f},{t[1]:.3f},{t[2]:.3f})m "
                            f"d={distance:.3f}m ypr=({yaw:.1f},{pitch:.1f},{roll:.1f})"
                        )
                        if frame_idx % max(1, int(args.print_every)) == 0:
                            print(status, flush=True)
                elif frame_idx % max(1, int(args.print_every)) == 0:
                    print(f"board not enough ChArUco corners; markers={visible_markers}", flush=True)
            elif frame_idx % max(1, int(args.print_every)) == 0:
                print("board none", flush=True)

            now = time.perf_counter()
            dt = now - last_frame
            last_frame = now
            if dt > 0:
                fps = fps * 0.85 + (1.0 / dt) * 0.15

            cv2.putText(
                frame,
                f"ChArUco Pose | markers {visible_markers} | FPS {fps:.1f}",
                (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.62,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            cv2.putText(
                frame,
                status,
                (12, 56),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 255),
                1,
                cv2.LINE_AA,
            )
            cv2.imshow("ChArUco Pose", frame)
            frame_idx += 1

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
