#!/usr/bin/env python3
from pathlib import Path

from PIL import Image, ImageDraw


OUT_DIR = Path(__file__).resolve().parents[1] / "tracking_markers"

MM_PER_INCH = 25.4
A4_MM = (210.0, 297.0)


def mm_to_px(mm: float, dpi: int) -> int:
    return round(mm / MM_PER_INCH * dpi)


def make_checkerboard(size_px: int, squares: int) -> Image.Image:
    image = Image.new("RGB", (size_px, size_px), "white")
    draw = ImageDraw.Draw(image)

    for row in range(squares):
        for col in range(squares):
            if (row + col) % 2 == 0:
                x0 = round(col * size_px / squares)
                y0 = round(row * size_px / squares)
                x1 = round((col + 1) * size_px / squares)
                y1 = round((row + 1) * size_px / squares)
                draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill="black")

    return image


def add_crop_marks(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], dpi: int) -> None:
    x0, y0, x1, y1 = box
    mark_len = mm_to_px(4, dpi)
    gap = mm_to_px(1.5, dpi)
    width = max(1, mm_to_px(0.15, dpi))

    segments = [
        ((x0 - gap - mark_len, y0), (x0 - gap, y0)),
        ((x0, y0 - gap - mark_len), (x0, y0 - gap)),
        ((x1 + gap, y0), (x1 + gap + mark_len, y0)),
        ((x1, y0 - gap - mark_len), (x1, y0 - gap)),
        ((x0 - gap - mark_len, y1), (x0 - gap, y1)),
        ((x0, y1 + gap), (x0, y1 + gap + mark_len)),
        ((x1 + gap, y1), (x1 + gap + mark_len, y1)),
        ((x1, y1 + gap), (x1, y1 + gap + mark_len)),
    ]
    for start, end in segments:
        draw.line((start, end), fill="black", width=width)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    marker_mm = 20.0
    squares = 8

    png_dpi = 1200
    png_size_px = mm_to_px(marker_mm, png_dpi)
    marker_png = make_checkerboard(png_size_px, squares)
    png_path = OUT_DIR / "checkerboard_20mm_8x8_1200dpi.png"
    marker_png.save(png_path, dpi=(png_dpi, png_dpi))

    pdf_dpi = 600
    page_w = mm_to_px(A4_MM[0], pdf_dpi)
    page_h = mm_to_px(A4_MM[1], pdf_dpi)
    marker_px = mm_to_px(marker_mm, pdf_dpi)

    page = Image.new("RGB", (page_w, page_h), "white")
    marker_pdf = make_checkerboard(marker_px, squares)
    x = (page_w - marker_px) // 2
    y = (page_h - marker_px) // 2
    page.paste(marker_pdf, (x, y))
    add_crop_marks(ImageDraw.Draw(page), (x, y, x + marker_px, y + marker_px), pdf_dpi)

    pdf_path = OUT_DIR / "checkerboard_20mm_8x8_A4_600dpi.pdf"
    page.save(pdf_path, "PDF", resolution=pdf_dpi)

    print(png_path)
    print(pdf_path)
    print(f"PNG marker pixels: {png_size_px}x{png_size_px} at {png_dpi} DPI")
    print(f"PDF marker pixels: {marker_px}x{marker_px} at {pdf_dpi} DPI")


if __name__ == "__main__":
    main()
