#!/usr/bin/env python3
"""Generate a printable ChArUco board for calibration and board pose tracking."""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
from PIL import Image


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


def create_board(squares_x: int, squares_y: int, square_len_m: float, marker_len_m: float, dictionary):
    aruco = require_aruco()
    if hasattr(aruco, "CharucoBoard"):
        try:
            return aruco.CharucoBoard((squares_x, squares_y), square_len_m, marker_len_m, dictionary)
        except TypeError:
            return aruco.CharucoBoard_create(squares_x, squares_y, square_len_m, marker_len_m, dictionary)
    if hasattr(aruco, "CharucoBoard_create"):
        return aruco.CharucoBoard_create(squares_x, squares_y, square_len_m, marker_len_m, dictionary)
    raise RuntimeError("This OpenCV build does not provide ChArUco board APIs.")


def board_to_image(board, width_px: int, height_px: int):
    if hasattr(board, "generateImage"):
        return board.generateImage((width_px, height_px), marginSize=0, borderBits=1)
    return board.draw((width_px, height_px), marginSize=0, borderBits=1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate ChArUco board PNG/PDF")
    parser.add_argument("--squares-x", type=int, default=5)
    parser.add_argument("--squares-y", type=int, default=7)
    parser.add_argument("--square-mm", type=float, default=30.0)
    parser.add_argument("--marker-mm", type=float, default=22.0)
    parser.add_argument("--dict", default="4x4_100")
    parser.add_argument("--dpi", type=int, default=600)
    parser.add_argument("--out-dir", type=Path, default=Path("tracking_markers/charuco"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dictionary = get_dictionary(args.dict)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    square_m = args.square_mm / 1000.0
    marker_m = args.marker_mm / 1000.0
    board = create_board(args.squares_x, args.squares_y, square_m, marker_m, dictionary)

    board_w_px = mm_to_px(args.squares_x * args.square_mm, args.dpi)
    board_h_px = mm_to_px(args.squares_y * args.square_mm, args.dpi)
    board_array = board_to_image(board, board_w_px, board_h_px)
    board_image = Image.fromarray(board_array).convert("RGB")

    base = f"charuco_{args.squares_x}x{args.squares_y}_square{int(args.square_mm)}_marker{int(args.marker_mm)}"
    png_path = args.out_dir / f"{base}.png"
    board_image.save(png_path, dpi=(args.dpi, args.dpi))

    page_w = mm_to_px(A4_MM[0], args.dpi)
    page_h = mm_to_px(A4_MM[1], args.dpi)
    page = Image.new("RGB", (page_w, page_h), "white")
    x = (page_w - board_w_px) // 2
    y = (page_h - board_h_px) // 2
    if x < 0 or y < 0:
        raise RuntimeError("ChArUco board is larger than A4. Reduce squares or square-mm.")
    page.paste(board_image, (x, y))

    pdf_path = args.out_dir / f"{base}_A4.pdf"
    page.save(pdf_path, "PDF", resolution=args.dpi)

    print(png_path)
    print(pdf_path)
    print("Print at 100% scale / Actual size. Do not use Fit to page.")


if __name__ == "__main__":
    main()
