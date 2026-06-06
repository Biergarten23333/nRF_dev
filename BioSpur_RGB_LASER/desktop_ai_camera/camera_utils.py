#!/usr/bin/env python3
"""Shared camera calibration and pose helpers for marker tracking."""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
import yaml


def get_camera_matrix_from_fov(width: int, height: int, fov_deg: float) -> tuple[np.ndarray, np.ndarray]:
    """Build an approximate pinhole camera matrix from horizontal field of view."""
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    if not 1.0 < fov_deg < 179.0:
        raise ValueError("fov_deg must be between 1 and 179 degrees")

    fov_rad = math.radians(fov_deg)
    fx = (width * 0.5) / math.tan(fov_rad * 0.5)
    fy = fx
    cx = width * 0.5
    cy = height * 0.5

    camera_matrix = np.array(
        [[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]],
        dtype=np.float32,
    )
    dist_coeffs = np.zeros((5, 1), dtype=np.float32)
    return camera_matrix, dist_coeffs


def _matrix_from_calib_node(node: object, name: str) -> np.ndarray:
    if isinstance(node, dict):
        rows = int(node.get("rows", 0))
        cols = int(node.get("cols", 0))
        data = node.get("data")
        if rows <= 0 or cols <= 0 or data is None:
            raise ValueError(f"{name} must contain rows, cols, and data")
        return np.asarray(data, dtype=np.float32).reshape(rows, cols)

    return np.asarray(node, dtype=np.float32)


def load_calibration_or_fov(
    calib_path: str | Path,
    width: int,
    height: int,
    fov_deg: float,
) -> tuple[np.ndarray, np.ndarray, str]:
    """Load OpenCV YAML calibration if present, otherwise fall back to FOV."""
    path = Path(calib_path)
    if not path.exists():
        camera_matrix, dist_coeffs = get_camera_matrix_from_fov(width, height, fov_deg)
        return camera_matrix, dist_coeffs, f"fov:{fov_deg:g}"

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if "camera_matrix" not in data:
        raise ValueError(f"{path} is missing camera_matrix")

    camera_matrix = _matrix_from_calib_node(data["camera_matrix"], "camera_matrix").reshape(3, 3)
    dist_node = data.get("dist_coeffs", data.get("distortion_coefficients"))
    if dist_node is None:
        dist_coeffs = np.zeros((5, 1), dtype=np.float32)
    else:
        dist_coeffs = _matrix_from_calib_node(dist_node, "distortion_coefficients").reshape(-1, 1)

    return camera_matrix.astype(np.float32), dist_coeffs.astype(np.float32), str(path)


def rvec_to_euler_deg(rvec: np.ndarray) -> tuple[float, float, float]:
    """Convert OpenCV Rodrigues rvec to yaw, pitch, roll in degrees."""
    rot_mat, _ = cv2.Rodrigues(np.asarray(rvec, dtype=np.float64).reshape(3, 1))

    sy = math.sqrt(rot_mat[0, 0] * rot_mat[0, 0] + rot_mat[1, 0] * rot_mat[1, 0])
    singular = sy < 1e-6
    if not singular:
        roll = math.atan2(rot_mat[2, 1], rot_mat[2, 2])
        pitch = math.atan2(-rot_mat[2, 0], sy)
        yaw = math.atan2(rot_mat[1, 0], rot_mat[0, 0])
    else:
        roll = math.atan2(-rot_mat[1, 2], rot_mat[1, 1])
        pitch = math.atan2(-rot_mat[2, 0], sy)
        yaw = 0.0

    return math.degrees(yaw), math.degrees(pitch), math.degrees(roll)


def draw_pose_axes(
    frame: np.ndarray,
    camera_matrix: np.ndarray,
    dist_coeffs: np.ndarray,
    rvec: np.ndarray,
    tvec: np.ndarray,
    axis_length: float,
) -> None:
    """Draw marker coordinate axes on a frame."""
    if hasattr(cv2, "drawFrameAxes"):
        cv2.drawFrameAxes(frame, camera_matrix, dist_coeffs, rvec, tvec, axis_length)
        return

    axis = np.float32(
        [[0, 0, 0], [axis_length, 0, 0], [0, axis_length, 0], [0, 0, axis_length]]
    )
    image_points, _ = cv2.projectPoints(axis, rvec, tvec, camera_matrix, dist_coeffs)
    points = image_points.reshape(-1, 2).astype(int)
    origin = tuple(points[0])
    cv2.line(frame, origin, tuple(points[1]), (0, 0, 255), 2)
    cv2.line(frame, origin, tuple(points[2]), (0, 255, 0), 2)
    cv2.line(frame, origin, tuple(points[3]), (255, 0, 0), 2)


def estimate_square_marker_pose(
    corners: np.ndarray,
    marker_size_m: float,
    camera_matrix: np.ndarray,
    dist_coeffs: np.ndarray,
) -> tuple[bool, np.ndarray, np.ndarray]:
    """Estimate 6DoF pose for one square marker from four ordered image corners."""
    s = float(marker_size_m)
    if s <= 0:
        raise ValueError("marker_size_m must be positive")

    object_points = np.array(
        [
            [-s / 2.0, s / 2.0, 0.0],
            [s / 2.0, s / 2.0, 0.0],
            [s / 2.0, -s / 2.0, 0.0],
            [-s / 2.0, -s / 2.0, 0.0],
        ],
        dtype=np.float32,
    )
    image_points = np.asarray(corners, dtype=np.float32).reshape(4, 2)

    flag = cv2.SOLVEPNP_IPPE_SQUARE if hasattr(cv2, "SOLVEPNP_IPPE_SQUARE") else cv2.SOLVEPNP_ITERATIVE
    success, rvec, tvec = cv2.solvePnP(
        object_points,
        image_points,
        camera_matrix,
        dist_coeffs,
        flags=flag,
    )
    return bool(success), rvec.reshape(3, 1), tvec.reshape(3, 1)


def pose_matrix(rvec: np.ndarray, tvec: np.ndarray) -> np.ndarray:
    """Convert rvec/tvec to a 4x4 homogeneous transform matrix."""
    rot_mat, _ = cv2.Rodrigues(np.asarray(rvec, dtype=np.float64).reshape(3, 1))
    transform = np.eye(4, dtype=np.float64)
    transform[:3, :3] = rot_mat
    transform[:3, 3] = np.asarray(tvec, dtype=np.float64).reshape(3)
    return transform


def require_aruco() -> object:
    """Return cv2.aruco or raise a clear dependency error."""
    if not hasattr(cv2, "aruco"):
        raise RuntimeError(
            "cv2.aruco is not available. Please install opencv-contrib-python."
        )
    return cv2.aruco


def get_aruco_dictionary(name: str):
    aruco = require_aruco()
    key = name.upper().replace("DICT_", "").replace("-", "_")
    dict_name = f"DICT_{key}"
    if not hasattr(aruco, dict_name):
        known = sorted(k.replace("DICT_", "").lower() for k in dir(aruco) if k.startswith("DICT_"))
        raise ValueError(f"Unknown ArUco dictionary '{name}'. Known examples: {', '.join(known[:12])}")
    return aruco.getPredefinedDictionary(getattr(aruco, dict_name))


def detect_aruco_markers(gray: np.ndarray, dictionary):
    aruco = require_aruco()
    if hasattr(aruco, "ArucoDetector"):
        parameters = aruco.DetectorParameters()
        detector = aruco.ArucoDetector(dictionary, parameters)
        return detector.detectMarkers(gray)

    parameters = aruco.DetectorParameters_create()
    return aruco.detectMarkers(gray, dictionary, parameters=parameters)


def draw_aruco_markers(frame: np.ndarray, corners: list[np.ndarray], ids: np.ndarray | None) -> None:
    aruco = require_aruco()
    if ids is not None and len(corners) > 0:
        aruco.drawDetectedMarkers(frame, corners, ids)
