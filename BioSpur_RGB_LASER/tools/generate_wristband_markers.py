#!/usr/bin/env python3
"""Generate printable ArUco markers for wristband experiments."""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


MM_PER_INCH = 25.4
A4_MM = (210.0, 297.0)


def mm_to_px(mm: float, dpi: int) -> int:
    return round(mm / MM_PER_INCH * dpi)


def require_aruco():
    if not hasattr(cv2, "aruco"):
        raise RuntimeError("cv2.aruco is not available. Please install opencv-contrib-python.")
    return cv2.aruco


def get_dictionary(name: str):
    aruco = require_aruco()
    key = name.upper().replace("DICT_", "").replace("-", "_")
    dict_name = f"DICT_{key}"
    if not hasattr(aruco, dict_name):
        raise ValueError(f"Unknown ArUco dictionary: {name}")
    return aruco.getPredefinedDictionary(getattr(aruco, dict_name))


def generate_marker(dictionary, marker_id: int, marker_px: int) -> Image.Image:
    aruco = require_aruco()
    if hasattr(aruco, "generateImageMarker"):
        image = aruco.generateImageMarker(dictionary, marker_id, marker_px)
    else:
        image = aruco.drawMarker(dictionary, marker_id, marker_px)
    return Image.fromarray(image).convert("RGB")


def add_marker_tile(
    page: Image.Image,
    dictionary,
    marker_id: int,
    x: int,
    y: int,
    marker_px: int,
    border_px: int,
    label_h: int,
    font: ImageFont.ImageFont,
) -> None:
    draw = ImageDraw.Draw(page)
    tile_w = marker_px + border_px * 2
    tile_h = marker_px + border_px * 2 + label_h
    draw.rectangle((x, y, x + tile_w - 1, y + tile_h - 1), fill="white", outline="black", width=1)
    marker = generate_marker(dictionary, marker_id, marker_px)
    page.paste(marker, (x + border_px, y + border_px))
    label = f"ID {marker_id}"
    bbox = draw.textbbox((0, 0), label, font=font)
    label_w = bbox[2] - bbox[0]
    draw.text((x + (tile_w - label_w) // 2, y + border_px * 2 + marker_px), label, fill="black", font=font)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate wristband ArUco markers")
    parser.add_argument("--dict", default="4x4_100", help="ArUco dictionary")
    parser.add_argument("--id-min", type=int, default=30, help="Minimum marker ID")
    parser.add_argument("--id-max", type=int, default=57, help="Maximum marker ID")
    parser.add_argument("--marker-mm", type=float, default=25.0, help="Printed marker side length")
    parser.add_argument("--dpi", type=int, default=600, help="Output DPI")
    parser.add_argument("--out-dir", type=Path, default=Path("tracking_markers/aruco"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dictionary = get_dictionary(args.dict)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    marker_px = mm_to_px(args.marker_mm, args.dpi)
    border_px = mm_to_px(3.0, args.dpi)
    label_h = mm_to_px(7.0, args.dpi)
    gap_px = mm_to_px(6.0, args.dpi)
    margin_px = mm_to_px(12.0, args.dpi)
    font = ImageFont.load_default()

    ids = list(range(args.id_min, args.id_max + 1))
    for marker_id in ids:
        marker = generate_marker(dictionary, marker_id, marker_px)
        path = args.out_dir / f"aruco_{args.dict}_id_{marker_id}.png"
        marker.save(path, dpi=(args.dpi, args.dpi))

    page_w = mm_to_px(A4_MM[0], args.dpi)
    page_h = mm_to_px(A4_MM[1], args.dpi)
    page = Image.new("RGB", (page_w, page_h), "white")
    tile_w = marker_px + border_px * 2
    tile_h = marker_px + border_px * 2 + label_h
    cols = max(1, (page_w - margin_px * 2 + gap_px) // (tile_w + gap_px))

    for idx, marker_id in enumerate(ids):
        row = idx // cols
        col = idx % cols
        x = margin_px + col * (tile_w + gap_px)
        y = margin_px + row * (tile_h + gap_px)
        if y + tile_h > page_h - margin_px:
            raise RuntimeError("Markers do not fit on one A4 page; reduce marker-mm or ID range.")
        add_marker_tile(page, dictionary, marker_id, x, y, marker_px, border_px, label_h, font)

    pdf_path = args.out_dir / f"wristband_aruco_{args.dict}_ids_{args.id_min}_{args.id_max}_A4.pdf"
    page.save(pdf_path, "PDF", resolution=args.dpi)

    print(f"Generated {len(ids)} PNG markers in {args.out_dir}")
    print(pdf_path)
    print(f"Marker print size: {args.marker_mm:g}mm x {args.marker_mm:g}mm")


if __name__ == "__main__":
    main()
