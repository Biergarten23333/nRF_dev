#!/usr/bin/env python3
"""Download small YOLO11 models through Ultralytics."""

from __future__ import annotations

import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download YOLO11 model weights")
    parser.add_argument("--models", nargs="+", default=["yolo11n.pt", "yolo11s.pt"])
    parser.add_argument("--out-dir", type=Path, default=Path("models"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError("ultralytics is not installed. Install it only if you need YOLO: python -m pip install ultralytics") from exc

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for model_name in args.models:
        target = args.out_dir / model_name
        if target.exists():
            print(f"exists: {target}")
            continue
        model = YOLO(model_name)
        source = Path(model.ckpt_path)
        target.write_bytes(source.read_bytes())
        print(f"downloaded: {target}")


if __name__ == "__main__":
    main()
