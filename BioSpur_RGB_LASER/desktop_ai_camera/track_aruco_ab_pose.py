#!/usr/bin/env python3
"""Track two ArUco markers and report their camera and relative 6DoF pose."""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
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
    pose_matrix,
    require_aruco,
    rvec_to_euler_deg,
)


@dataclass
class MarkerPose:
    marker_id: int
    rvec: np.ndarray
    tvec: np.ndarray
    yaw: float
    pitch: float
    roll: float

    @property
    def distance(self) -> float:
        return float(np.linalg.norm(self.tvec.reshape(3)))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Track A/B ArUco marker 6DoF pose")
    parser.add_argument("--camera", type=int, default=0, help="OpenCV camera index")
    parser.add_argument("--dict", default="4x4_100", help="ArUco dictionary, for example 4x4_100")
    parser.add_argument("--ids", type=int, nargs=2, default=[30, 31], help="A and B marker IDs")
    parser.add_argument("--marker-size-m", type=float, default=0.04, help="Printed marker side length in meters")
    parser.add_argument("--fov-deg", type=float, default=60.0, help="Horizontal camera FOV fallback")
    parser.add_argument("--calib", default="calibration/camera.yaml", help="Optional camera calibration YAML")
    parser.add_argument("--width", type=int, default=640, help="Capture width")
    parser.add_argument("--height", type=int, default=480, help="Capture height")
    parser.add_argument("--draw-axes", action=argparse.BooleanOptionalAction, default=True)
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


def label_marker(frame: np.ndarray, marker: MarkerPose, corner: np.ndarray, name: str) -> None:
    x, y = np.asarray(corner).reshape(4, 2).mean(axis=0).astype(int)
    tx, ty, tz = marker.tvec.reshape(3)
    lines = [
        f"{name} id={marker.marker_id}",
        f"xyz=({tx:.3f},{ty:.3f},{tz:.3f})m",
        f"d={marker.distance:.3f}m",
        f"ypr=({marker.yaw:.1f},{marker.pitch:.1f},{marker.roll:.1f})",
    ]
    for idx, text in enumerate(lines):
        cv2.putText(
            frame,
            text,
            (int(x) + 8, int(y) + 18 + idx * 18),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.46,
            (0, 255, 255),
            1,
            cv2.LINE_AA,
        )


def relative_pose(a: MarkerPose, b: MarkerPose) -> tuple[np.ndarray, tuple[float, float, float]]:
    transform_a = pose_matrix(a.rvec, a.tvec)
    transform_b = pose_matrix(b.rvec, b.tvec)
    transform_a_b = np.linalg.inv(transform_a) @ transform_b
    rvec_rel, _ = cv2.Rodrigues(transform_a_b[:3, :3])
    return transform_a_b[:3, 3], rvec_to_euler_deg(rvec_rel)


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

    marker_names = {args.ids[0]: "A", args.ids[1]: "B"}
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
            poses: dict[int, MarkerPose] = {}

            if ids is not None:
                draw_aruco_markers(frame, corners, ids)
                for marker_corners, marker_id_arr in zip(corners, ids.reshape(-1)):
                    marker_id = int(marker_id_arr)
                    if marker_id not in marker_names:
                        continue
                    success, rvec, tvec = estimate_square_marker_pose(
                        marker_corners, args.marker_size_m, camera_matrix, dist_coeffs
                    )
                    if not success:
                        continue
                    yaw, pitch, roll = rvec_to_euler_deg(rvec)
                    pose = MarkerPose(marker_id, rvec, tvec, yaw, pitch, roll)
                    poses[marker_id] = pose
                    if args.draw_axes:
                        draw_pose_axes(frame, camera_matrix, dist_coeffs, rvec, tvec, args.marker_size_m * 0.5)
                    label_marker(frame, pose, marker_corners, marker_names[marker_id])

            now = time.perf_counter()
            dt = now - last_frame
            last_frame = now
            if dt > 0:
                fps = fps * 0.85 + (1.0 / dt) * 0.15

            if frame_idx % max(1, int(args.print_every)) == 0:
                for marker_id in args.ids:
                    marker = poses.get(marker_id)
                    name = marker_names[marker_id]
                    if marker is None:
                        print(f"{name} marker: id={marker_id} not visible", flush=True)
                    else:
                        x, y, z = marker.tvec.reshape(3)
                        print(
                            f"{name} marker: id={marker_id} xyz=({x:.4f},{y:.4f},{z:.4f}) "
                            f"ypr=({marker.yaw:.2f},{marker.pitch:.2f},{marker.roll:.2f}) "
                            f"dist={marker.distance:.4f}",
                            flush=True,
                        )
                if args.ids[0] in poses and args.ids[1] in poses:
                    rel_t, rel_ypr = relative_pose(poses[args.ids[0]], poses[args.ids[1]])
                    print(
                        "B relative to A: "
                        f"t=({rel_t[0]:.4f},{rel_t[1]:.4f},{rel_t[2]:.4f}) "
                        f"ypr=({rel_ypr[0]:.2f},{rel_ypr[1]:.2f},{rel_ypr[2]:.2f})",
                        flush=True,
                    )

            cv2.putText(
                frame,
                f"A/B ArUco Pose | FPS {fps:.1f}",
                (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            cv2.imshow("A/B ArUco Pose", frame)
            frame_idx += 1

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
