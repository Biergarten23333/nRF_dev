#!/usr/bin/env python3
"""Download MediaPipe Pose Landmarker task files."""

from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path


MODEL_URLS = {
    "pose_landmarker_lite.task": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task",
    "pose_landmarker_full.task": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task",
    "pose_landmarker_heavy.task": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download MediaPipe Pose Landmarker models")
    parser.add_argument("--models", nargs="+", default=list(MODEL_URLS))
    parser.add_argument("--out-dir", type=Path, default=Path("models"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for model_name in args.models:
        if model_name not in MODEL_URLS:
            raise ValueError(f"Unknown model {model_name}. Known: {', '.join(MODEL_URLS)}")
        target = args.out_dir / model_name
        if target.exists():
            print(f"exists: {target}")
            continue
        print(f"downloading: {target}")
        urllib.request.urlretrieve(MODEL_URLS[model_name], target)
        print(f"downloaded: {target}")


if __name__ == "__main__":
    main()
